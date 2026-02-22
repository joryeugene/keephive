"""Audit command tests: metrics, direct display function calls, closed-loop, skip_llm mode."""

from __future__ import annotations

import json
from datetime import date

from conftest import make_daily

from keephive.models import (
    AuditPlay,
    AuditSynthesis,
    CleanerPerspective,
    StrategistPerspective,
    VaultPerspective,
)


def _mock_vault():
    return VaultPerspective(
        analysis="Knowledge base is in good shape. 3 verified facts, all current.",
        issues=["1 stale fact from 2020-01-01"],
    )


def _mock_cleaner():
    return CleanerPerspective(
        analysis="Execution discipline shows room for improvement.",
        issues=["TODO backlog growing"],
    )


def _mock_strategist():
    return StrategistPerspective(
        analysis="Strategy guide exists and daily work aligns well.",
        issues=[],
    )


def _mock_synthesis():
    return AuditSynthesis(
        plays=[
            AuditPlay(issue="Verify 1 stale fact", command="hive v"),
            AuditPlay(issue="Deduplicate TODOs", command="hive dr"),
        ],
        connection="Vault and Cleaner both flag knowledge decay.",
        tension="You say 'verify facts'. You have 1 stale fact for 6 years. The cost is eroded trust.",
        wild_card="High correction rate suggests rapid learning cycle.",
    )


# ---------------------------------------------------------------------------
# TestAuditMetrics: Pure metric gathering, no LLM
# ---------------------------------------------------------------------------


class TestAuditMetrics:
    def test_vault_counts_stale_facts(self, hive_env):
        """Vault correctly identifies stale facts."""
        from keephive.commands.audit import _analyze_vault

        result = _analyze_vault()
        assert result["stale_facts"] == 1  # 2020-01-01 fact is stale
        assert result["total_facts"] == 3

    def test_vault_no_stale_when_all_fresh(self, hive_env):
        """No stale facts when all are recent."""
        mem = hive_env / "working" / "memory.md"
        today = date.today().isoformat()
        mem.write_text(
            f"# Working Memory\n\n- Fact A [verified:{today}]\n- Fact B [verified:{today}]\n"
        )
        from keephive.commands.audit import _analyze_vault

        result = _analyze_vault()
        assert result["stale_facts"] == 0
        assert result["total_facts"] == 2

    def test_cleaner_completion_rate(self, hive_env):
        """Cleaner measures TODO completion rate."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] TODO: Write tests",
                "- [10:05:00] TODO: Fix bugs",
                "- [10:10:00] DONE: Write tests",
            ],
        )
        from keephive.commands.audit import _analyze_cleaner

        result = _analyze_cleaner()
        assert result["todo_completion_rate"] > 0

    def test_cleaner_stale_todos(self, hive_env):
        """Cleaner identifies TODOs older than 7 days."""
        make_daily(
            hive_env,
            10,
            [
                "- [10:00:00] TODO: Old task that was never done",
            ],
        )
        from keephive.commands.audit import _analyze_cleaner

        result = _analyze_cleaner()
        assert result["stale_todos"] >= 1

    def test_strategist_detects_strategy_guide(self, hive_env):
        """Strategist finds the strategy guide."""
        (hive_env / "knowledge" / "guides" / "strategy.md").write_text(
            "# Strategy\nBuild verification tools."
        )
        from keephive.commands.audit import _analyze_strategist

        result = _analyze_strategist()
        assert result["has_strategy"] is True

    def test_strategist_no_strategy(self, hive_env):
        """Strategist reports missing strategy guide."""
        from keephive.commands.audit import _analyze_strategist

        result = _analyze_strategist()
        assert result["has_strategy"] is False

    def test_score_penalizes_stale_facts(self, hive_env):
        """Score drops with stale facts."""
        from keephive.commands.audit import (
            _analyze_cleaner,
            _analyze_strategist,
            _analyze_vault,
            _compute_score,
        )

        vault = _analyze_vault()
        cleaner = _analyze_cleaner()
        strategist = _analyze_strategist()
        score = _compute_score(vault, cleaner, strategist)
        assert score < 100  # Has 1 stale fact + no strategy guide

    def test_score_caps_per_perspective(self, hive_env):
        """Each perspective penalty caps at 30 points."""
        from keephive.commands.audit import _compute_score

        # Extreme vault penalties
        vault = {"stale_facts": 10, "correction_count_7d": 20}
        cleaner = {
            "stale_todos": 0,
            "duplicate_todos": 0,
            "overdue_recurring": 0,
            "todo_completion_rate": 0.5,
        }
        strategist = {
            "has_strategy": True,
            "has_rules": True,
            "decision_count_7d": 5,
            "active_days_7d": 7,
        }
        score = _compute_score(vault, cleaner, strategist)
        # Vault penalty capped at 30, so score should be >= 70 + completion bonus
        assert score >= 70

    def test_score_perfect(self, hive_env):
        """Perfect score with all healthy metrics."""
        from keephive.commands.audit import _compute_score

        vault = {"stale_facts": 0, "correction_count_7d": 0}
        cleaner = {
            "stale_todos": 0,
            "duplicate_todos": 0,
            "overdue_recurring": 0,
            "todo_completion_rate": 1.0,
        }
        strategist = {
            "has_strategy": True,
            "has_rules": True,
            "decision_count_7d": 5,
            "active_days_7d": 7,
        }
        score = _compute_score(vault, cleaner, strategist)
        assert score == 100  # 100 base + 10 completion bonus, capped at 100

    def test_topic_distribution(self, hive_env):
        """Topic distribution counts entry categories."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] FACT: Something",
                "- [10:01:00] FACT: Another",
                "- [10:02:00] DECISION: Choice",
                "- [10:03:00] TODO: Task",
            ],
        )
        from keephive.commands.audit import _topic_distribution_7d

        topics = _topic_distribution_7d()
        assert topics.get("FACT", 0) == 2
        assert topics.get("DECISION", 0) == 1
        assert topics.get("TODO", 0) == 1


# ---------------------------------------------------------------------------
# TestAuditDisplay: Direct calls to extracted display functions
# ---------------------------------------------------------------------------


class TestAuditDisplay:
    def test_display_audit_compact_shows_actions(self, hive_env, capsys):
        """Default display shows ranked actions, not essays."""
        from keephive.commands.audit import display_audit

        vault_r = _mock_vault()
        cleaner_r = _mock_cleaner()
        strategist_r = _mock_strategist()
        synthesis = _mock_synthesis()

        vault_metrics = {
            "stale_facts": 1,
            "total_facts": 3,
            "correction_count_7d": 0,
            "guide_count": 2,
            "memory_line_count": 10,
        }
        cleaner_metrics = {
            "stale_todos": 0,
            "duplicate_todos": 0,
            "overdue_recurring": 0,
            "todo_completion_rate": 0.5,
            "todo_velocity_7d": {"created": 3, "completed": 1},
        }
        strategist_metrics = {
            "has_strategy": True,
            "has_rules": True,
            "decision_count_7d": 3,
            "fact_count_7d": 5,
            "active_days_7d": 5,
            "topic_distribution_7d": {"FACT": 5},
        }

        display_audit(
            score=75,
            vault_r=vault_r,
            cleaner_r=cleaner_r,
            strategist_r=strategist_r,
            synthesis=synthesis,
            vault=vault_metrics,
            cleaner=cleaner_metrics,
            strategist=strategist_metrics,
            previous_play=None,
        )

        out = capsys.readouterr().out
        assert len(out) > 200, f"Compact audit output too short ({len(out)} chars)"
        assert "Do next" in out
        assert "Verify 1 stale fact" in out
        assert "hive v" in out
        assert "Connection:" in out
        assert "Tension:" in out
        assert "Wild Card:" in out
        assert "Vault Analysis" not in out  # Hidden in compact mode

    def test_display_audit_verbose_shows_essays(self, hive_env, capsys):
        """Verbose display includes full perspective essays."""
        from keephive.commands.audit import display_audit

        vault_r = _mock_vault()
        cleaner_r = _mock_cleaner()
        strategist_r = _mock_strategist()
        synthesis = _mock_synthesis()

        vault_metrics = {
            "stale_facts": 1,
            "total_facts": 3,
            "correction_count_7d": 0,
            "guide_count": 2,
            "memory_line_count": 10,
        }
        cleaner_metrics = {
            "stale_todos": 0,
            "duplicate_todos": 0,
            "overdue_recurring": 0,
            "todo_completion_rate": 0.5,
            "todo_velocity_7d": {"created": 3, "completed": 1},
        }
        strategist_metrics = {
            "has_strategy": True,
            "has_rules": True,
            "decision_count_7d": 3,
            "fact_count_7d": 5,
            "active_days_7d": 5,
            "topic_distribution_7d": {"FACT": 5},
        }

        display_audit(
            score=75,
            vault_r=vault_r,
            cleaner_r=cleaner_r,
            strategist_r=strategist_r,
            synthesis=synthesis,
            vault=vault_metrics,
            cleaner=cleaner_metrics,
            strategist=strategist_metrics,
            previous_play=None,
            verbose=True,
        )

        out = capsys.readouterr().out
        assert len(out) > 300, f"Verbose audit output too short ({len(out)} chars)"
        assert "Vault Analysis" in out
        assert "Cleaner Analysis" in out
        assert "Strategist Analysis" in out
        assert "Do next" in out  # Actions still shown

    def test_display_perspectives_only_on_cook_failure(self, hive_env, capsys):
        """display_perspectives_only shows reports when Cook fails."""
        from keephive.commands.audit import display_perspectives_only

        vault_r = _mock_vault()
        cleaner_r = _mock_cleaner()
        strategist_r = _mock_strategist()

        display_perspectives_only(vault_r, cleaner_r, strategist_r, score=80)

        out = capsys.readouterr().out
        assert len(out) > 200, f"Perspectives-only output too short ({len(out)} chars)"
        assert "Vault Analysis" in out
        assert "Cleaner Analysis" in out
        assert "Strategist Analysis" in out
        assert "Cook synthesis failed" in out
        # Score rendered with Rich formatting (ANSI codes around numbers)
        assert "80" in out and "100" in out

    def test_save_audit_insights_to_daily(self, hive_env):
        """save_audit_insights writes insights and top play TODO to daily log."""
        from keephive.commands.audit import save_audit_insights

        synthesis = _mock_synthesis()
        count = save_audit_insights(synthesis, score=75)

        daily = hive_env / "daily" / f"{date.today().isoformat()}.md"
        content = daily.read_text()
        assert "INSIGHT: [audit] Connection:" in content
        assert "INSIGHT: [audit] Tension:" in content
        assert "INSIGHT: [audit] Wild Card:" in content
        assert "FACT: Quality Pulse score:" in content
        assert "TODO: [audit] Verify 1 stale fact" in content
        # Only top play saved as TODO, not second
        assert content.count("TODO: [audit]") == 1
        assert count == 5  # 3 insights + 1 TODO + 1 FACT

    def test_save_audit_no_todo_when_no_action(self, hive_env):
        """When top play issue is 'no urgent action needed', skip TODO."""
        from keephive.commands.audit import save_audit_insights

        synthesis = AuditSynthesis(
            plays=[AuditPlay(issue="No urgent action needed", command="hive s")],
            connection="All looks good.",
            tension="No tension detected.",
            wild_card="Everything is fine.",
        )
        count = save_audit_insights(synthesis, score=95)

        daily = hive_env / "daily" / f"{date.today().isoformat()}.md"
        content = daily.read_text()
        assert "TODO: [audit]" not in content
        assert count == 4  # 3 insights + 1 FACT (no TODO)

    def test_display_audit_with_issues(self, hive_env, capsys):
        """Perspective issues render as warnings in verbose mode."""
        from keephive.commands.audit import display_audit

        vault_r = VaultPerspective(
            analysis="Issues found.",
            issues=["3 stale facts need attention", "Memory too large"],
        )
        cleaner_r = _mock_cleaner()
        strategist_r = _mock_strategist()
        synthesis = _mock_synthesis()

        vault_metrics = {
            "stale_facts": 3,
            "total_facts": 10,
            "correction_count_7d": 2,
            "guide_count": 1,
            "memory_line_count": 50,
        }
        cleaner_metrics = {
            "stale_todos": 0,
            "duplicate_todos": 0,
            "overdue_recurring": 0,
            "todo_completion_rate": 0.5,
            "todo_velocity_7d": {"created": 2, "completed": 1},
        }
        strategist_metrics = {
            "has_strategy": True,
            "has_rules": True,
            "decision_count_7d": 1,
            "fact_count_7d": 2,
            "active_days_7d": 3,
            "topic_distribution_7d": {},
        }

        display_audit(
            score=60,
            vault_r=vault_r,
            cleaner_r=cleaner_r,
            strategist_r=strategist_r,
            synthesis=synthesis,
            vault=vault_metrics,
            cleaner=cleaner_metrics,
            strategist=strategist_metrics,
            previous_play=None,
            verbose=True,
        )

        out = capsys.readouterr().out
        assert len(out) > 200, f"Verbose audit with issues output too short ({len(out)} chars)"
        assert "stale facts" in out
        assert "Memory too large" in out
        assert "Vault Analysis" in out  # Verbose shows full perspective essays


# ---------------------------------------------------------------------------
# TestAuditClosedLoop: Play tracking across audits
# ---------------------------------------------------------------------------


class TestAuditClosedLoop:
    def test_previous_play_detected(self, hive_env):
        """Previous audit Play TODO is found."""
        make_daily(
            hive_env,
            1,
            [
                "- [10:00:00] TODO: [audit] Verify 1 stale fact",
            ],
        )
        from keephive.commands.audit import _check_previous_play

        result = _check_previous_play()
        assert result is not None
        assert result["action"] == "Verify 1 stale fact"
        assert result["completed"] is False
        assert result["age_days"] == 1

    def test_completed_play_shows_as_done(self, hive_env):
        """Completed Play is detected correctly."""
        make_daily(
            hive_env,
            2,
            [
                "- [10:00:00] TODO: [audit] Run hive doctor",
            ],
        )
        make_daily(
            hive_env,
            1,
            [
                "- [10:00:00] DONE: Run hive doctor",
            ],
        )
        from keephive.commands.audit import _check_previous_play

        result = _check_previous_play()
        assert result is not None
        assert result["completed"] is True

    def test_completed_play_with_audit_prefix_in_done(self, hive_env):
        """DONE entry with [audit] prefix still matches stripped play text.

        Bug regression test: save_audit_insights writes TODO: [audit] X,
        hive td marks it DONE: [audit] X, but _check_previous_play strips
        [audit] from the play. The match must succeed despite prefix mismatch.
        """
        make_daily(
            hive_env,
            2,
            [
                "- [10:00:00] TODO: [audit] Run hive doctor",
            ],
        )
        make_daily(
            hive_env,
            1,
            [
                "- [10:00:00] DONE: [audit] Run hive doctor",
            ],
        )
        from keephive.commands.audit import _check_previous_play

        result = _check_previous_play()
        assert result is not None
        assert result["completed"] is True, (
            f"DONE with [audit] prefix should match stripped play text. Got: {result}"
        )

    def test_no_previous_play(self, hive_env):
        """No previous audit returns None."""
        from keephive.commands.audit import _check_previous_play

        result = _check_previous_play()
        assert result is None

    def test_skip_llm_shows_metrics_only(self, hive_env, capsys):
        """HIVE_SKIP_LLM=1 shows metrics but no LLM synthesis."""
        # hive_env fixture already sets HIVE_SKIP_LLM=1
        from keephive.commands.audit import cmd_audit

        cmd_audit([])
        out = capsys.readouterr().out
        assert len(out) > 100, f"Audit skip-LLM output too short ({len(out)} chars)"
        assert "Quality Pulse" in out
        assert "The Vault" in out
        assert "Synthesis" not in out  # No LLM synthesis in skip mode

    def test_skip_llm_json_mode(self, hive_env, capsys):
        """HIVE_SKIP_LLM + --json outputs valid JSON with metrics."""
        from keephive.commands.audit import cmd_audit

        cmd_audit(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "score" in data
        assert "vault" in data
        assert "cleaner" in data
        assert "strategist" in data
        assert isinstance(data["score"], int)


# ---------------------------------------------------------------------------
# TestAuditGathering: Data gathering helpers
# ---------------------------------------------------------------------------


class TestAuditGathering:
    def test_gather_daily_logs(self, hive_env):
        """Daily log gathering returns recent content."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] FACT: Today's fact",
            ],
        )
        make_daily(
            hive_env,
            1,
            [
                "- [09:00:00] DECISION: Yesterday's decision",
            ],
        )
        from keephive.commands.audit import _gather_daily_logs_text

        text = _gather_daily_logs_text(7)
        assert "Today's fact" in text
        assert "Yesterday's decision" in text

    def test_gather_corrections(self, hive_env):
        """Corrections gathering extracts CORRECTION entries."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] CORRECTION: old -> new",
                "- [10:01:00] FACT: something else",
            ],
        )
        from keephive.commands.audit import _gather_corrections_text

        text = _gather_corrections_text(7)
        assert "old -> new" in text
        assert "something else" not in text

    def test_gather_decisions(self, hive_env):
        """Decisions gathering extracts DECISION entries."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] DECISION: chose X over Y",
                "- [10:01:00] TODO: unrelated",
            ],
        )
        from keephive.commands.audit import _gather_decisions_text

        text = _gather_decisions_text(7)
        assert "chose X over Y" in text
        assert "unrelated" not in text

    def test_strategy_text(self, hive_env):
        """Strategy text reads guide when present."""
        (hive_env / "knowledge" / "guides" / "strategy.md").write_text(
            "# Strategy\nFocus on verification."
        )
        from keephive.commands.audit import _get_strategy_text

        text = _get_strategy_text()
        assert "Focus on verification" in text

    def test_strategy_text_missing(self, hive_env):
        """Strategy text returns placeholder when no guide exists."""
        from keephive.commands.audit import _get_strategy_text

        text = _get_strategy_text()
        assert "No strategy guide exists" in text

    def test_gather_verify_results(self, hive_env):
        """Verify results gathering extracts verify-related entries."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] FACT: [verify] VALID: Python is great",
                "- [10:01:00] CORRECTION: [verify] STALE: old fact corrected",
                "- [10:02:00] TODO: unrelated task",
            ],
        )
        from keephive.commands.audit import _gather_verify_results

        text = _gather_verify_results(7)
        assert "VALID" in text
        assert "STALE" in text
        assert "unrelated task" not in text

    def test_gather_verify_results_empty(self, hive_env):
        """No verify results returns placeholder."""
        from keephive.commands.audit import _gather_verify_results

        text = _gather_verify_results(7)
        assert "no recent verify results" in text
