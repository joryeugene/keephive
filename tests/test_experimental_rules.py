"""Tests for experimental rules: add, expire, baselines."""

from __future__ import annotations

from keephive.commands.memory import (
    _EXPERIMENT_TAG_RE,
    _is_expired,
    _rule_hash,
    cmd_rule_try,
    expire_experimental_rules,
)
from keephive.storage import (
    experiment_baselines_file,
    read_experiment_baselines,
    rules_file,
    write_experiment_baselines,
)


class TestExperimentTagRegex:
    def test_matches_valid_tag(self):
        m = _EXPERIMENT_TAG_RE.search("- Run tests first [experiment:7d:2026-03-11]")
        assert m is not None
        assert m.group(1) == "7"
        assert m.group(2) == "2026-03-11"

    def test_no_match_without_tag(self):
        m = _EXPERIMENT_TAG_RE.search("- Run tests first")
        assert m is None

    def test_matches_14_day_experiment(self):
        m = _EXPERIMENT_TAG_RE.search("[experiment:14d:2026-04-01]")
        assert m is not None
        assert m.group(1) == "14"

    def test_no_match_malformed(self):
        assert _EXPERIMENT_TAG_RE.search("[experiment:d:2026-03-11]") is None
        assert _EXPERIMENT_TAG_RE.search("[experiment:7d:]") is None


class TestRuleHash:
    def test_stable_hash(self):
        h1 = _rule_hash("- Run tests first [experiment:7d:2026-03-11]")
        h2 = _rule_hash("- Run tests first [experiment:14d:2026-04-01]")
        assert h1 == h2, "Hash should be tag-independent"

    def test_different_rules_different_hash(self):
        h1 = _rule_hash("- Run tests first")
        h2 = _rule_hash("- Always verify before committing")
        assert h1 != h2

    def test_hash_strips_prefix(self):
        h1 = _rule_hash("- Run tests")
        h2 = _rule_hash("Run tests")
        assert h1 == h2


class TestIsExpired:
    def test_no_tag_not_expired(self):
        assert _is_expired("- Permanent rule") is False

    def test_future_expiry_not_expired(self, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-01")
        assert _is_expired("- Rule [experiment:7d:2026-03-11]") is False

    def test_past_expiry_is_expired(self, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-12")
        assert _is_expired("- Rule [experiment:7d:2026-03-11]") is True

    def test_expiry_on_exact_date(self, monkeypatch):
        """Expiry date itself counts as expired (>= comparison)."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-11")
        assert _is_expired("- Rule [experiment:7d:2026-03-11]") is True


class TestExpireExperimentalRules:
    def test_no_rules_file(self, hive_env):
        rf = rules_file()
        rf.unlink(missing_ok=True)
        assert expire_experimental_rules() == []

    def test_no_experimental_rules(self, hive_env):
        rf = rules_file()
        rf.write_text("# Rules\n\n- Permanent rule\n")
        assert expire_experimental_rules() == []

    def test_expires_past_rule(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-04-01")
        rf = rules_file()
        rf.write_text(
            "# Rules\n\n"
            "- Keep it simple\n"
            "- Try verbose logging [experiment:7d:2026-03-11]\n"
            "- Stay focused\n"
        )
        expired = expire_experimental_rules()
        assert len(expired) == 1
        assert "verbose logging" in expired[0]
        # Rule removed from file
        content = rf.read_text()
        assert "verbose logging" not in content
        assert "Keep it simple" in content
        assert "Stay focused" in content

    def test_keeps_future_rule(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-05")
        rf = rules_file()
        rf.write_text("# Rules\n\n- Try it [experiment:7d:2026-03-11]\n")
        expired = expire_experimental_rules()
        assert expired == []
        assert "Try it" in rf.read_text()

    def test_cleans_up_baselines(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-04-01")
        rf = rules_file()
        rule_line = "- Try verbose [experiment:7d:2026-03-11]"
        rf.write_text(f"# Rules\n\n{rule_line}\n")
        h = _rule_hash(rule_line)
        write_experiment_baselines({h: {"rule": "Try verbose", "baseline_friction": {}}})

        expire_experimental_rules()
        baselines = read_experiment_baselines()
        assert h not in baselines

    def test_multiple_expired_mixed(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-04-01")
        rf = rules_file()
        rf.write_text(
            "# Rules\n\n"
            "- Old experiment [experiment:7d:2026-03-10]\n"
            "- Future experiment [experiment:30d:2026-05-01]\n"
            "- Permanent rule\n"
        )
        expired = expire_experimental_rules()
        assert len(expired) == 1
        assert "Old experiment" in expired[0]
        content = rf.read_text()
        assert "Future experiment" in content
        assert "Permanent rule" in content


class TestCmdRuleTry:
    def test_adds_rule_with_tag(self, hive_env, monkeypatch, capsys):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        cmd_rule_try(["Always run tests first"])
        content = rules_file().read_text()
        assert "[experiment:7d:2026-03-11]" in content
        assert "Always run tests first" in content

    def test_custom_days(self, hive_env, monkeypatch, capsys):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        cmd_rule_try(["Check types", "--days", "14"])
        content = rules_file().read_text()
        assert "[experiment:14d:2026-03-18]" in content

    def test_max_days_clamped(self, hive_env, monkeypatch, capsys):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        cmd_rule_try(["Big rule", "--days", "999"])
        content = rules_file().read_text()
        # Clamped to 30 days
        assert "[experiment:30d:" in content

    def test_saves_friction_baseline(self, hive_env, monkeypatch, capsys):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        # Mock _read_facets to return known friction data
        monkeypatch.setattr(
            "keephive.commands.memory._read_facets",
            lambda: {"wrong_approach": {"count": 5, "sessions": 2}},
        )
        cmd_rule_try(["Verify before coding"])
        baselines = read_experiment_baselines()
        assert len(baselines) == 1
        entry = list(baselines.values())[0]
        assert entry["rule"] == "Verify before coding"
        assert entry["baseline_friction"]["wrong_approach"] == 5

    def test_empty_text_rejected(self, hive_env, capsys):
        cmd_rule_try([])
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_creates_rules_file_if_missing(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        rf = rules_file()
        rf.unlink(missing_ok=True)
        cmd_rule_try(["New rule"])
        assert rf.exists()
        assert "New rule" in rf.read_text()


class TestSessionstartExpiry:
    """Test that sessionstart integration expires rules and logs them."""

    def test_expired_rule_logged_to_daily(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-04-15")
        rf = rules_file()
        rf.write_text("# Rules\n\n- Old rule [experiment:7d:2026-03-11]\n")

        from keephive.hooks.sessionstart import build_context

        build_context(cwd="/tmp/test", project_name="test-project")

        from keephive.storage import daily_dir

        log_file = daily_dir() / "2026-04-15.md"
        assert log_file.exists()
        content = log_file.read_text()
        assert "EXPIRED-RULE:" in content
        assert "Old rule" in content

    def test_no_crash_on_empty_rules(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-04-15")
        rf = rules_file()
        rf.unlink(missing_ok=True)
        # Should not crash
        from keephive.hooks.sessionstart import build_context

        build_context(cwd="/tmp/test", project_name="test-project")


class TestExperimentBaselinesStorage:
    def test_read_empty(self, hive_env):
        assert read_experiment_baselines() == {}

    def test_roundtrip(self, hive_env):
        data = {"abc123": {"rule": "Test", "baseline_friction": {"wrong_approach": 3}}}
        write_experiment_baselines(data)
        assert read_experiment_baselines() == data

    def test_corrupt_json_returns_empty(self, hive_env):
        experiment_baselines_file().write_text("{corrupt")
        assert read_experiment_baselines() == {}
