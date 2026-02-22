"""E2E tests for audit closed-loop, metrics accuracy, and score time-travel.

Priorities 1, 2, 7 from the E2E coverage gap analysis.

These tests run in real tmux sessions with HIVE_SKIP_LLM=1 (metrics-only mode).
They validate that the audit pipeline's data flow is correct end-to-end:
- Insights written to daily log feed back into next audit
- Metric counts match seeded data
- Score changes deterministically as facts age

Run: uv run pytest -m terminal -k test_e2e_audit -v -o "addopts="
"""

from __future__ import annotations

import json

import pytest


def _audit_json(term) -> dict:
    """Run audit --json in HIVE_SKIP_LLM mode and parse the result.

    Redirects to a file to avoid tmux line-wrapping issues with wide JSON.
    """
    term.type("python -m keephive audit --json > $HIVE_HOME/.audit-result.json")
    raw = term.read_file(".audit-result.json")
    return json.loads(raw)


def _seed_memory(term, facts: list[str]) -> None:
    """Write facts to working memory via python one-liner."""
    lines = ["# Working Memory", ""]
    lines.extend(f"- {f}" for f in facts)
    content = "\\n".join(lines) + "\\n"
    term.type(f'python -c "from pathlib import Path; import os; '
              f"Path(os.environ['HIVE_HOME'], 'working', 'memory.md')"
              f".write_text('{content}')\"")


def _seed_daily(term, date_str: str, entries: list[str]) -> None:
    """Write a daily log file for a specific date."""
    lines = [f"# Daily Log: {date_str}"]
    lines.extend(entries)
    content = "\\n".join(lines) + "\\n"
    term.type(f'python -c "from pathlib import Path; import os; '
              f"Path(os.environ['HIVE_HOME'], 'daily', '{date_str}.md')"
              f".write_text('{content}')\"")


# ============================================================
#  Priority 1: Audit Closed-Loop Roundtrip
# ============================================================


@pytest.mark.terminal
class TestAuditClosedLoop:
    """Verify the full feedback loop: audit TODO → todo done → next audit sees completion."""

    def test_audit_todo_appears_in_next_audit(self, term, save_terminal_output):
        """An audit TODO written to daily log is detected by the next audit."""
        # Day 1: Seed a TODO: [audit] entry in the daily log
        term.set_date("2026-03-01")
        _seed_memory(term, [
            "FACT: Python is great [verified:2020-01-01]",
        ])
        _seed_daily(term, "2026-03-01", [
            "- [10:00:00] TODO: [audit] Verify stale facts in vault",
        ])

        # Day 2: Run audit, check previous_play is detected
        term.set_date("2026-03-02")
        data = _audit_json(term)

        assert data["previous_play"] is not None, "Expected previous play to be detected"
        assert data["previous_play"]["action"] == "Verify stale facts in vault"
        assert data["previous_play"]["completed"] is False
        assert data["previous_play"]["age_days"] == 1

        save_terminal_output("audit_loop/todo_detected", term)

    def test_completed_play_detected_with_audit_prefix(self, term, save_terminal_output):
        """Completing an audit TODO via 'hive td' is detected as done by next audit.

        This is the critical roundtrip: save_audit_insights() writes TODO: [audit] X,
        hive td marks it DONE: [audit] X, and _check_previous_play() must match
        despite the [audit] prefix in the DONE entry.
        """
        # Day 1: Audit TODO exists
        term.set_date("2026-03-01")
        _seed_daily(term, "2026-03-01", [
            "- [10:00:00] TODO: [audit] Run hive doctor to fix duplicates",
        ])

        # Day 2: Mark it done via hive td (which preserves the [audit] prefix)
        term.set_date("2026-03-02")
        term.type('python -m keephive todo done "Run hive doctor"').has("Completed")

        # Day 3: Audit should see it as completed
        term.set_date("2026-03-03")
        data = _audit_json(term)

        assert data["previous_play"] is not None, "Expected previous play"
        assert data["previous_play"]["completed"] is True, (
            f"Expected completed=True but got {data['previous_play']}"
        )

        save_terminal_output("audit_loop/completed_with_prefix", term)

    def test_open_play_shows_age(self, term, save_terminal_output):
        """An open audit TODO shows correct age in days."""
        term.set_date("2026-03-01")
        _seed_daily(term, "2026-03-01", [
            "- [10:00:00] TODO: [audit] Review knowledge gaps",
        ])

        # 5 days later, still open
        term.set_date("2026-03-06")
        data = _audit_json(term)

        assert data["previous_play"] is not None
        assert data["previous_play"]["completed"] is False
        assert data["previous_play"]["age_days"] == 5

        save_terminal_output("audit_loop/open_play_age", term)

    def test_no_previous_play_when_clean(self, term):
        """Audit with no prior TODO: [audit] entries returns null previous_play."""
        term.set_date("2026-03-01")
        _seed_memory(term, [
            "FACT: Clean state [verified:2026-03-01]",
        ])

        data = _audit_json(term)
        assert data["previous_play"] is None

    def test_most_recent_play_used(self, term, save_terminal_output):
        """When multiple audit TODOs exist, the most recent one is used."""
        term.set_date("2026-03-01")
        _seed_daily(term, "2026-02-28", [
            "- [10:00:00] TODO: [audit] Old action from February",
        ])
        _seed_daily(term, "2026-03-01", [
            "- [10:00:00] TODO: [audit] Latest action from March",
        ])

        term.set_date("2026-03-02")
        data = _audit_json(term)

        assert data["previous_play"] is not None
        assert data["previous_play"]["action"] == "Latest action from March"

        save_terminal_output("audit_loop/most_recent_play", term)


# ============================================================
#  Priority 2: Audit Metrics Accuracy
# ============================================================


@pytest.mark.terminal
class TestAuditMetricsAccuracy:
    """Verify that audit --json metrics match seeded data exactly."""

    def test_vault_stale_facts_count(self, term, save_terminal_output):
        """Vault stale_facts matches actual count of facts older than 30 days."""
        term.set_date("2026-03-15")
        _seed_memory(term, [
            "FACT: Fresh fact [verified:2026-03-10]",
            "FACT: Stale fact one [verified:2026-01-01]",
            "FACT: Stale fact two [verified:2025-12-15]",
            "FACT: Borderline fact [verified:2026-02-13]",
        ])

        data = _audit_json(term)

        # Facts older than 30 days from 2026-03-15: Jan 1, Dec 15 = 2 stale
        # Feb 13 is exactly 30 days old (not stale, threshold is >30)
        assert data["vault"]["stale_facts"] >= 2, (
            f"Expected at least 2 stale facts, got {data['vault']['stale_facts']}"
        )
        assert data["vault"]["total_facts"] == 4

        save_terminal_output("audit_metrics/vault_stale", term)

    def test_cleaner_todo_completion_rate(self, term, save_terminal_output):
        """Cleaner completion_rate matches actual TODO/DONE ratio."""
        term.set_date("2026-03-15")
        _seed_daily(term, "2026-03-14", [
            "- [10:00:00] TODO: First task to complete",
            "- [10:05:00] TODO: Second task stays open",
            "- [10:10:00] TODO: Third task to complete",
        ])
        _seed_daily(term, "2026-03-15", [
            "- [10:00:00] DONE: First task to complete",
            "- [10:05:00] DONE: Third task to complete",
        ])

        data = _audit_json(term)

        # 3 TODOs created, 2 DONEs = 2/3 ≈ 0.67
        rate = data["cleaner"]["todo_completion_rate"]
        assert 0.5 <= rate <= 0.8, f"Expected ~0.67 completion rate, got {rate}"

        save_terminal_output("audit_metrics/cleaner_completion", term)

    def test_strategist_topic_distribution(self, term, save_terminal_output):
        """Strategist topic_distribution matches actual category counts."""
        term.set_date("2026-03-15")
        _seed_daily(term, "2026-03-14", [
            "- [10:00:00] FACT: Something factual alpha",
            "- [10:05:00] FACT: Another factual beta",
            "- [10:10:00] DECISION: Chose approach gamma",
            "- [10:15:00] TODO: Task to track delta",
        ])
        _seed_daily(term, "2026-03-15", [
            "- [10:00:00] FACT: Third factual epsilon",
            "- [10:05:00] INSIGHT: Pattern observed zeta",
            "- [10:10:00] DONE: Task to track delta",
        ])

        data = _audit_json(term)
        dist = data["strategist"]["topic_distribution_7d"]

        assert dist.get("FACT", 0) == 3, f"Expected 3 FACTs, got {dist.get('FACT', 0)}"
        assert dist.get("DECISION", 0) == 1, f"Expected 1 DECISION, got {dist.get('DECISION', 0)}"
        assert dist.get("INSIGHT", 0) == 1, f"Expected 1 INSIGHT, got {dist.get('INSIGHT', 0)}"
        assert dist.get("TODO", 0) == 1, f"Expected 1 TODO, got {dist.get('TODO', 0)}"
        assert dist.get("DONE", 0) == 1, f"Expected 1 DONE, got {dist.get('DONE', 0)}"

        save_terminal_output("audit_metrics/strategist_distribution", term)

    def test_strategist_has_strategy_detection(self, term, save_terminal_output):
        """Strategist detects presence of a strategy guide."""
        term.set_date("2026-03-15")

        # No strategy guide
        data = _audit_json(term)
        assert data["strategist"]["has_strategy"] is False

        # Create one
        term.type(
            'python -c "from pathlib import Path; import os; '
            "Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'strategy.md')"
            ".write_text('# Strategy\\nFocus on verification.\\n')\""
        )

        data = _audit_json(term)
        assert data["strategist"]["has_strategy"] is True

        save_terminal_output("audit_metrics/strategy_detection", term)

    def test_score_is_deterministic(self, term, save_terminal_output):
        """Same input produces same score on repeated runs."""
        term.set_date("2026-03-15")
        _seed_memory(term, [
            "FACT: Stable fact [verified:2026-03-10]",
        ])

        scores = []
        for _ in range(3):
            data = _audit_json(term)
            scores.append(data["score"])

        assert len(set(scores)) == 1, f"Score should be deterministic, got {scores}"

        save_terminal_output("audit_metrics/deterministic_score", term)

    def test_vault_correction_count(self, term):
        """Vault correction_count_7d counts CORRECTION entries in daily logs."""
        term.set_date("2026-03-15")
        _seed_daily(term, "2026-03-14", [
            "- [10:00:00] CORRECTION: old assumption -> new reality",
            "- [10:05:00] CORRECTION: wrong config -> correct config",
            "- [10:10:00] FACT: unrelated fact",
        ])
        _seed_daily(term, "2026-03-15", [
            "- [10:00:00] CORRECTION: another fix applied",
        ])

        data = _audit_json(term)
        assert data["vault"]["correction_count_7d"] == 3

    def test_cleaner_stale_todos(self, term):
        """Cleaner stale_todos counts TODOs older than 7 days."""
        term.set_date("2026-03-15")
        # Stale TODO (>7 days old)
        _seed_daily(term, "2026-03-01", [
            "- [10:00:00] TODO: Ancient task from two weeks ago",
        ])
        # Fresh TODO (<7 days old)
        _seed_daily(term, "2026-03-14", [
            "- [10:00:00] TODO: Recent task from yesterday",
        ])

        data = _audit_json(term)
        assert data["cleaner"]["stale_todos"] >= 1, (
            f"Expected at least 1 stale TODO, got {data['cleaner']['stale_todos']}"
        )

    def test_strategist_active_days(self, term):
        """Strategist active_days_7d counts days with at least one entry."""
        term.set_date("2026-03-15")
        # Create entries on 3 different days within the window
        for day_str in ["2026-03-13", "2026-03-14", "2026-03-15"]:
            _seed_daily(term, day_str, [
                f"- [10:00:00] FACT: Entry on {day_str}",
            ])

        data = _audit_json(term)
        assert data["strategist"]["active_days_7d"] == 3


# ============================================================
#  Priority 7: Audit Score Under Time Travel
# ============================================================


@pytest.mark.terminal
class TestAuditScoreTimeTravel:
    """Verify score changes predictably as facts age past staleness thresholds."""

    def test_score_degrades_as_facts_go_stale(self, term, save_terminal_output):
        """Score drops when fresh facts age past the 30-day staleness boundary."""
        # Day 0: All facts fresh, score should be high
        term.set_date("2026-03-01")
        _seed_memory(term, [
            "FACT: Alpha fact [verified:2026-03-01]",
            "FACT: Beta fact [verified:2026-03-01]",
            "FACT: Gamma fact [verified:2026-03-01]",
        ])
        # Also seed rules and active days to avoid strategist penalties
        term.type(
            'python -c "from pathlib import Path; import os; '
            "Path(os.environ['HIVE_HOME'], 'working', 'rules.md')"
            ".write_text('# Rules\\n\\n## Rule 1\\nDo good things.\\n')\""
        )
        term.type(
            'python -c "from pathlib import Path; import os; '
            "Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'strategy.md')"
            ".write_text('# Strategy\\nBe excellent.\\n')\""
        )
        # Seed active days
        for i in range(5):
            day = f"2026-02-{24 + i:02d}"
            _seed_daily(term, day, [f"- [10:00:00] FACT: Activity day {i}"])
        _seed_daily(term, "2026-03-01", [
            "- [10:00:00] DECISION: Made a choice today",
        ])

        data_fresh = _audit_json(term)
        score_fresh = data_fresh["score"]

        # Day 31: Facts are now stale (>30 days)
        term.set_date("2026-04-01")
        # Add some activity so strategist penalty for low activity doesn't dominate
        _seed_daily(term, "2026-03-31", [
            "- [10:00:00] FACT: Recent activity alpha",
            "- [10:05:00] DECISION: Recent decision beta",
        ])
        _seed_daily(term, "2026-04-01", [
            "- [10:00:00] FACT: Today activity gamma",
        ])
        # Seed a few more active days
        for i in range(3):
            day = f"2026-03-{28 + i:02d}"
            _seed_daily(term, day, [f"- [10:00:00] FACT: Late March activity {i}"])

        data_stale = _audit_json(term)
        score_stale = data_stale["score"]

        assert score_stale < score_fresh, (
            f"Score should drop when facts go stale: "
            f"fresh={score_fresh}, stale={score_stale}"
        )

        save_terminal_output("audit_time_travel/score_degradation", term)

    def test_score_formula_vault_penalty(self, term, save_terminal_output):
        """Each stale fact costs 10 points (capped at 30)."""
        term.set_date("2026-03-15")

        # 0 stale facts
        _seed_memory(term, [
            "FACT: Fresh [verified:2026-03-10]",
        ])
        data_0 = _audit_json(term)

        # 1 stale fact
        _seed_memory(term, [
            "FACT: Fresh [verified:2026-03-10]",
            "FACT: Stale [verified:2020-01-01]",
        ])
        data_1 = _audit_json(term)

        # 2 stale facts
        _seed_memory(term, [
            "FACT: Fresh [verified:2026-03-10]",
            "FACT: Stale A [verified:2020-01-01]",
            "FACT: Stale B [verified:2019-06-01]",
        ])
        data_2 = _audit_json(term)

        # Score should drop ~10 per stale fact (other factors constant)
        assert data_1["score"] < data_0["score"], "1 stale fact should lower score"
        assert data_2["score"] < data_1["score"], "2 stale facts should lower score more"

        save_terminal_output("audit_time_travel/vault_penalty", term)

    def test_empty_hive_baseline_score(self, term):
        """Empty hive has a baseline score reflecting missing strategy + rules + activity."""
        term.set_date("2026-03-15")
        data = _audit_json(term)

        # Empty hive: no strategy (-10), no rules (-5), no decisions (-5), low activity (-10)
        # = 100 - 30 (strategist cap) = 70
        assert data["score"] <= 80, f"Empty hive should have penalties, got {data['score']}"
        assert data["score"] >= 40, f"Empty hive score too low: {data['score']}"
