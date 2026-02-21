"""Tests for hive rule learn: friction-to-rules pipeline."""

from __future__ import annotations

import json
from pathlib import Path

# ---- Helpers ----


def _make_facet(facets_dir: Path, friction_counts: dict[str, int], session_id: str = "") -> Path:
    """Create a facet JSON file with given friction counts."""
    sid = session_id or f"test-{len(list(facets_dir.glob('*.json')))}"
    fpath = facets_dir / f"{sid}.json"
    fpath.write_text(
        json.dumps(
            {
                "underlying_goal": "test",
                "friction_counts": friction_counts,
                "friction_detail": "",
                "session_id": sid,
            }
        )
    )
    return fpath


def _make_facets_dir(tmp_path: Path) -> Path:
    """Create the facets directory under a fake ~/.claude."""
    facets = tmp_path / ".claude" / "usage-data" / "facets"
    facets.mkdir(parents=True)
    return facets


# ---- Unit tests: _trigrams ----


class TestTrigrams:
    def test_basic(self):
        from keephive.commands.memory import _trigrams

        result = _trigrams("hello world")
        assert "hel" in result
        assert "ell" in result
        assert "llo" in result
        assert "wor" in result
        assert "orl" in result
        assert "rld" in result

    def test_short_words_excluded(self):
        from keephive.commands.memory import _trigrams

        result = _trigrams("a is it the")
        # "a" and "is" are too short for trigrams; "it" too; "the" gives {"the"}
        assert result == {"the"}

    def test_empty_string(self):
        from keephive.commands.memory import _trigrams

        assert _trigrams("") == set()


# ---- Unit tests: _rule_already_covered ----


class TestRuleAlreadyCovered:
    def test_no_existing_rules(self):
        from keephive.commands.memory import _rule_already_covered

        assert _rule_already_covered("make minimal changes", "") is False

    def test_covered_by_similar_rule(self):
        from keephive.commands.memory import _rule_already_covered

        existing = "- Make the minimal change needed. Do not refactor."
        assert _rule_already_covered("Make minimal changes to code", existing) is True

    def test_not_covered_by_different_rule(self):
        from keephive.commands.memory import _rule_already_covered

        existing = "- Always run tests after changes."
        # "clarifying question" shares no significant trigrams with test-running rules
        assert (
            _rule_already_covered(
                "Ask a clarifying question before acting on ambiguous requests",
                existing,
            )
            is False
        )

    def test_candidate_with_no_trigrams(self):
        from keephive.commands.memory import _rule_already_covered

        assert _rule_already_covered("a b", "some existing rule text") is False


# ---- Unit tests: _read_facets ----


class TestReadFacets:
    def test_reads_and_aggregates(self, tmp_path, monkeypatch):
        from keephive.commands.memory import _read_facets

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _make_facet(facets, {"wrong_approach": 3}, "session-1")
        _make_facet(facets, {"wrong_approach": 2, "buggy_code": 1}, "session-2")

        result = _read_facets()
        assert result["wrong_approach"]["count"] == 5
        assert result["wrong_approach"]["sessions"] == 2
        assert result["buggy_code"]["count"] == 1
        assert result["buggy_code"]["sessions"] == 1

    def test_skips_corrupt_files(self, tmp_path, monkeypatch):
        from keephive.commands.memory import _read_facets

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _make_facet(facets, {"wrong_approach": 1}, "good")
        (facets / "corrupt.json").write_text("{invalid json")

        result = _read_facets()
        assert result["wrong_approach"]["count"] == 1

    def test_no_facets_dir(self, tmp_path, monkeypatch):
        from keephive.commands.memory import _read_facets

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert _read_facets() == {}

    def test_empty_friction_counts(self, tmp_path, monkeypatch):
        from keephive.commands.memory import _read_facets

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _make_facet(facets, {}, "no-friction")
        assert _read_facets() == {}


# ---- Integration tests: _rule_learn ----


class TestRuleLearn:
    def test_no_friction_data(self, hive_env, tmp_path, monkeypatch, capsys):
        from keephive.commands.memory import _rule_learn

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        _rule_learn()
        out = capsys.readouterr().out
        assert "No friction data found" in out

    def test_below_threshold(self, hive_env, tmp_path, monkeypatch, capsys):
        from keephive.commands.memory import _rule_learn

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        # 2 sessions with wrong_approach: below the 3-session threshold
        _make_facet(facets, {"wrong_approach": 5}, "s1")
        _make_facet(facets, {"wrong_approach": 3}, "s2")

        _rule_learn()
        out = capsys.readouterr().out
        assert "No friction types meet the threshold" in out

    def test_dry_run_does_not_write(self, hive_env, tmp_path, monkeypatch, capsys):
        from keephive.commands.memory import _rule_learn
        from keephive.storage import hive_dir

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        for i in range(4):
            _make_facet(facets, {"wrong_approach": 1}, f"s{i}")

        _rule_learn(dry_run=True)
        out = capsys.readouterr().out

        assert "Dry run" in out
        assert "New Rule Candidates" in out
        pending = hive_dir() / ".pending-rules.md"
        assert not pending.exists() or not pending.read_text().strip()

    def test_queues_rules(self, hive_env, tmp_path, monkeypatch, capsys):
        from keephive.commands.memory import _rule_learn
        from keephive.storage import hive_dir

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        for i in range(5):
            _make_facet(facets, {"wrong_approach": 2, "buggy_code": 1}, f"s{i}")

        _rule_learn()
        out = capsys.readouterr().out

        assert "Queued" in out
        pending = hive_dir() / ".pending-rules.md"
        assert pending.exists()
        content = pending.read_text()
        assert "[5 sessions: wrong_approach]" in content
        assert "[5 sessions: buggy_code]" in content
        assert "Before starting implementation" in content

    def test_dedup_skips_covered_rules(self, hive_env, tmp_path, monkeypatch, capsys):
        from keephive.commands.memory import FRICTION_RULES, _rule_learn
        from keephive.storage import rules_file

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        for i in range(4):
            _make_facet(facets, {"wrong_approach": 1, "buggy_code": 1}, f"s{i}")

        # Pre-populate rules.md with the wrong_approach rule text
        rf = rules_file()
        rf.write_text("# Working Rules\n\n- " + FRICTION_RULES["wrong_approach"] + "\n")

        _rule_learn()
        out = capsys.readouterr().out

        # wrong_approach should be skipped (already covered), buggy_code should be queued
        if "Queued" in out:
            from keephive.storage import hive_dir

            content = (hive_dir() / ".pending-rules.md").read_text()
            assert "wrong_approach" not in content
            assert "buggy_code" in content

    def test_dedup_skips_pending_rules(self, hive_env, tmp_path, monkeypatch, capsys):
        from keephive.commands.memory import FRICTION_RULES, _rule_learn
        from keephive.storage import hive_dir

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        for i in range(4):
            _make_facet(facets, {"wrong_approach": 1}, f"s{i}")

        # Pre-populate .pending-rules.md with the rule already queued
        pending = hive_dir() / ".pending-rules.md"
        pending.write_text(
            "- [3 sessions: wrong_approach] " + FRICTION_RULES["wrong_approach"] + "\n"
        )

        _rule_learn()
        out = capsys.readouterr().out
        assert "already covered" in out

    def test_session_dedup(self, hive_env, tmp_path, monkeypatch, capsys):
        """Each facet file = one session. Session count, not instance count."""
        from keephive.commands.memory import _rule_learn

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        # 1 session with count=100: still only 1 unique session
        _make_facet(facets, {"wrong_approach": 100}, "single-session")

        _rule_learn()
        out = capsys.readouterr().out
        assert "No friction types meet the threshold" in out

    def test_friction_summary_table(self, hive_env, tmp_path, monkeypatch, capsys):
        from keephive.commands.memory import _rule_learn

        facets = _make_facets_dir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _make_facet(facets, {"rate_limit": 5}, "s1")
        _make_facet(facets, {"wrong_approach": 1}, "s2")

        _rule_learn()
        out = capsys.readouterr().out

        # Summary table should show both types
        assert "Friction Summary" in out
        assert "rate_limit" in out
        assert "wrong_approach" in out
        # rate_limit is not actionable (no mapped rule), wrong_approach is
        assert "* = actionable" in out

    def test_environmental_types_excluded(self, hive_env, tmp_path, monkeypatch, capsys):
        """Environmental friction types should never produce rule candidates."""
        from keephive.commands.memory import FRICTION_RULES

        for env_type in ["rate_limit", "api_error", "tool_unavailable", "tool_limitation"]:
            assert env_type not in FRICTION_RULES


# ---- Integration tests: _rule_review with annotations ----


class TestRuleReviewAnnotations:
    def test_annotation_stripped_on_accept(self, hive_env, monkeypatch, capsys):
        from keephive.storage import hive_dir, rules_file

        pending = hive_dir() / ".pending-rules.md"
        pending.write_text("- [5 sessions: wrong_approach] Before starting, state your approach.\n")

        # Auto-accept
        monkeypatch.setattr("builtins.input", lambda _: "y")

        from keephive.commands.memory import _rule_review

        _rule_review()

        # The annotation should NOT appear in rules.md
        rf = rules_file()
        content = rf.read_text()
        assert "Before starting, state your approach." in content
        assert "[5 sessions:" not in content

    def test_annotation_displayed_during_review(self, hive_env, monkeypatch, capsys):
        from keephive.storage import hive_dir

        pending = hive_dir() / ".pending-rules.md"
        pending.write_text("- [8 sessions: buggy_code] Run tests after changes.\n")

        monkeypatch.setattr("builtins.input", lambda _: "n")

        from keephive.commands.memory import _rule_review

        _rule_review()
        out = capsys.readouterr().out
        assert "8 sessions: buggy_code" in out

    def test_non_annotated_rules_unchanged(self, hive_env, monkeypatch, capsys):
        from keephive.storage import hive_dir, rules_file

        pending = hive_dir() / ".pending-rules.md"
        pending.write_text("- Always be helpful.\n")

        monkeypatch.setattr("builtins.input", lambda _: "y")

        from keephive.commands.memory import _rule_review

        _rule_review()

        rf = rules_file()
        content = rf.read_text()
        assert "Always be helpful." in content
