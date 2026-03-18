"""Tests for the efficacy loop: compute_experiment_results, sessionend trigger,
_recent_loop_completions, and growth story experiment observations.

TDD: all tests here are written BEFORE implementation. They will fail (red)
until each phase is implemented. Implementation order: A -> B/C -> D.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_facets_file(facets_dir: Path, name: str, friction: dict[str, int]) -> Path:
    """Write a facets JSON file with the given friction_counts dict."""
    f = facets_dir / f"{name}.json"
    f.write_text(json.dumps({"friction_counts": friction}))
    return f


def _backdate(path: Path, posix_ts: float = 1577836800.0) -> None:
    """Set a file's mtime to the given POSIX timestamp (default: 2020-01-01 UTC)."""
    os.utime(path, (posix_ts, posix_ts))


def _make_snap(
    log_entries_wk: int = 0,
    log_entries_prev: int = 0,
    todos_done: int = 0,
    corrections_wk: int = 0,
    corrections_prev: int = 0,
    guide_hits: int = 0,
    fact_freshness: float = 90.0,
    fact_count: int = 5,
    dark_pct: float = 0.0,
    auto_only: int = 0,
) -> dict:
    """Minimal snap dict for _print_growth_story tests."""
    from datetime import timedelta

    from keephive.clock import get_today

    today = get_today()
    trend = [
        {
            "date": (today - timedelta(days=30 - i)).isoformat(),
            "log_entries": 0,
            "guide_hits": 0,
            "todos_done": 0,
            "corrections": 0,
            "daemon_runs": 0,
            "commands": 0,
        }
        for i in range(30)
    ]
    return {
        "week_totals": {
            "log_entries": log_entries_wk,
            "todos_done": todos_done,
            "corrections": corrections_wk,
            "guide_hits": guide_hits,
            "daemon_runs": 0,
            "commands": 0,
        },
        "prev_week_totals": {
            "log_entries": log_entries_prev,
            "todos_done": 0,
            "corrections": corrections_prev,
            "guide_hits": 0,
            "daemon_runs": 0,
            "commands": 0,
        },
        "fact_freshness": fact_freshness,
        "fact_count": fact_count,
        "comprehension": {
            "dark_pct": dark_pct,
            "auto_only": auto_only,
            "total": 0,
            "verified": 0,
            "user_owned": 0,
            "coverage_pct": 0.0,
        },
        "trend_30d": trend,
        "recall_total": 0,
        "recall_hits": 0,
        "recall_rate": 0.0,
        "guide_count": 0,
    }


# ---------------------------------------------------------------------------
# Phase A: compute_experiment_results() in storage.py
# ---------------------------------------------------------------------------


class TestComputeExperimentResults:
    """Tests for compute_experiment_results() -- function not yet implemented."""

    def test_no_baselines_file_returns_false(self, hive_env, monkeypatch, tmp_path):
        """No .experiment-baselines.json -> returns False, no error."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        from keephive.storage import compute_experiment_results, experiment_baselines_file

        experiment_baselines_file().unlink(missing_ok=True)
        result = compute_experiment_results()
        assert result is False

    def test_no_facets_dir_returns_false(self, hive_env, monkeypatch, tmp_path):
        """Baselines exist but facets dir missing -> returns False, no error."""
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(tmp_path / "nonexistent"))

        from keephive.storage import compute_experiment_results, write_experiment_baselines

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Always test first",
                    "added": "2026-01-01",
                    "baseline_friction": {"wrong_approach": 5},
                }
            }
        )
        result = compute_experiment_results()
        assert result is False

    def test_zero_sessions_after_sets_measuring(self, hive_env, monkeypatch, tmp_path):
        """All facets pre-date the experiment -> status = 'measuring', no delta."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        # One facet file backdated to 2020 (well before the experiment)
        f = _make_facets_file(facets_dir, "sess-old", {"wrong_approach": 3})
        _backdate(f)

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Always test first",
                    "added": "2026-03-01",  # after 2020 file mtime -> file is "before" pool
                    "baseline_friction": {"wrong_approach": 5},
                }
            }
        )

        compute_experiment_results()

        entry = read_experiment_baselines()["abc123"]
        assert entry.get("status") == "measuring", (
            f"Expected status='measuring' with 0 after-sessions, got: {entry}"
        )
        assert entry.get("friction_delta") is None, (
            f"Expected no delta when measuring, got: {entry.get('friction_delta')}"
        )

    def test_one_session_after_sets_measuring(self, hive_env, monkeypatch, tmp_path):
        """Exactly 1 session after added_date -> status = 'measuring' (need >= 2)."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        # One recent file (added_date = far past, so file is in "after" pool)
        _make_facets_file(facets_dir, "sess-new", {"wrong_approach": 2})

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Check types",
                    "added": "2000-01-01",
                    "baseline_friction": {},
                }
            }
        )

        compute_experiment_results()

        entry = read_experiment_baselines()["abc123"]
        assert entry.get("status") == "measuring", (
            f"Expected status='measuring' with 1 after-session, got: {entry}"
        )

    def test_two_sessions_improvement_negative_delta(self, hive_env, monkeypatch, tmp_path):
        """2 sessions after, after_rate < before_rate -> friction_delta is negative (good)."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        # Before: 1 session with 10 friction events (high rate = 10.0)
        f_before = _make_facets_file(facets_dir, "sess-before", {"wrong_approach": 10})
        _backdate(f_before)

        # After: 2 sessions with 2 events each (after_rate = 2.0, big improvement)
        _make_facets_file(facets_dir, "sess-after-1", {"wrong_approach": 2})
        _make_facets_file(facets_dir, "sess-after-2", {"wrong_approach": 2})

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Always test first",
                    "added": "2026-01-01",
                    "baseline_friction": {"wrong_approach": 5},
                }
            }
        )

        result = compute_experiment_results()
        assert result is True, "compute_experiment_results should return True when it updated data"

        entry = read_experiment_baselines()["abc123"]
        assert entry.get("friction_delta") is not None, "friction_delta should be set"
        assert entry["friction_delta"] < 0, (
            f"Expected negative delta (improvement), got: {entry['friction_delta']}"
        )
        assert entry.get("sessions_since") == 2, (
            f"Expected sessions_since=2, got: {entry.get('sessions_since')}"
        )

    def test_two_sessions_degradation_positive_delta(self, hive_env, monkeypatch, tmp_path):
        """2 sessions after, after_rate > before_rate -> friction_delta is positive (bad)."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        # Before: 1 session with 2 friction events (before_rate = 2.0)
        f_before = _make_facets_file(facets_dir, "sess-before", {"wrong_approach": 2})
        _backdate(f_before)

        # After: 2 sessions with 10 each (after_rate = 10.0, worse)
        _make_facets_file(facets_dir, "sess-after-1", {"wrong_approach": 10})
        _make_facets_file(facets_dir, "sess-after-2", {"wrong_approach": 10})

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Try verbose logging",
                    "added": "2026-01-01",
                    "baseline_friction": {},
                }
            }
        )

        compute_experiment_results()

        entry = read_experiment_baselines()["abc123"]
        assert entry.get("friction_delta") is not None
        assert entry["friction_delta"] > 0, (
            f"Expected positive delta (degradation), got: {entry['friction_delta']}"
        )

    def test_no_before_pool_friction_delta_none(self, hive_env, monkeypatch, tmp_path):
        """No facets pre-date experiment -> friction_delta = None (no rate to compare against)."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        # Two files, both in "after" pool (added_date = far past)
        _make_facets_file(facets_dir, "sess-1", {"wrong_approach": 3})
        _make_facets_file(facets_dir, "sess-2", {"wrong_approach": 3})

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "No before baseline rule",
                    "added": "2000-01-01",
                    "baseline_friction": {},
                }
            }
        )

        compute_experiment_results()

        entry = read_experiment_baselines()["abc123"]
        assert entry.get("friction_delta") is None, (
            f"Expected friction_delta=None when no before pool, got: {entry.get('friction_delta')}"
        )
        assert entry.get("current_friction_total") is not None, (
            "current_friction_total should be set even without before pool"
        )

    def test_before_rate_zero_after_zero_delta_is_zero(self, hive_env, monkeypatch, tmp_path):
        """before_rate == 0 and after friction also 0 -> friction_delta = 0.0."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        # Before: 0 friction
        f_before = _make_facets_file(facets_dir, "sess-before", {"wrong_approach": 0})
        _backdate(f_before)

        # After: 0 friction (2 sessions)
        _make_facets_file(facets_dir, "sess-after-1", {"wrong_approach": 0})
        _make_facets_file(facets_dir, "sess-after-2", {"wrong_approach": 0})

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Zero friction rule",
                    "added": "2026-01-01",
                    "baseline_friction": {},
                }
            }
        )

        compute_experiment_results()

        entry = read_experiment_baselines()["abc123"]
        assert entry.get("friction_delta") == 0.0, (
            f"Expected 0.0 when both before and after have zero friction, got: {entry.get('friction_delta')}"
        )

    def test_malformed_facets_json_skipped(self, hive_env, monkeypatch, tmp_path):
        """Corrupt facets file is silently skipped; valid files still computed."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        (facets_dir / "corrupt.json").write_text("{bad json")

        # Before: high friction
        f_before = _make_facets_file(facets_dir, "sess-before", {"wrong_approach": 10})
        _backdate(f_before)

        # After: 2 valid sessions
        _make_facets_file(facets_dir, "sess-after-1", {"wrong_approach": 2})
        _make_facets_file(facets_dir, "sess-after-2", {"wrong_approach": 2})

        from keephive.storage import compute_experiment_results, write_experiment_baselines

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Test rule",
                    "added": "2026-01-01",
                    "baseline_friction": {},
                }
            }
        )

        # Must not raise
        result = compute_experiment_results()
        assert result is True

    def test_missing_added_field_skipped(self, hive_env, monkeypatch, tmp_path):
        """Baseline entry without 'added' field is skipped silently."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        _make_facets_file(facets_dir, "sess-1", {"wrong_approach": 3})
        _make_facets_file(facets_dir, "sess-2", {"wrong_approach": 3})

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "No added date entry",
                    # deliberately missing 'added' field
                    "baseline_friction": {},
                }
            }
        )

        # Must not raise
        compute_experiment_results()

        # Entry remains untouched (skipped) -- no friction_delta set
        entry = read_experiment_baselines().get("abc123", {})
        assert "friction_delta" not in entry or entry.get("friction_delta") is None

    def test_returns_false_when_nothing_changed(self, hive_env, monkeypatch, tmp_path):
        """Returns False when no entries were updated (e.g., all skipped or measuring)."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))

        # Only 1 session after -> measuring -> nothing committed to baselines
        _make_facets_file(facets_dir, "sess-1", {"wrong_approach": 3})

        from keephive.storage import compute_experiment_results, write_experiment_baselines

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Measuring only",
                    "added": "2000-01-01",
                    "baseline_friction": {},
                }
            }
        )

        result = compute_experiment_results()
        assert result is False, "Should return False when no deltas were computed"

    def test_experiment_results_calls_compute_lazily(self, hive_env, monkeypatch):
        """experiment_results() must call compute_experiment_results() as lazy trigger."""
        called = []

        def fake_compute() -> bool:
            called.append(True)
            return False

        monkeypatch.setattr("keephive.storage.compute_experiment_results", fake_compute)

        from keephive.storage import experiment_results

        experiment_results()

        assert len(called) == 1, (
            "experiment_results() must call compute_experiment_results() once (lazy trigger)"
        )

    def test_last_computed_written(self, hive_env, monkeypatch, tmp_path):
        """After successful computation, last_computed is set to today."""
        facets_dir = tmp_path / "facets"
        facets_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_FACETS_DIR", str(facets_dir))
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        f_before = _make_facets_file(facets_dir, "sess-before", {"wrong_approach": 10})
        _backdate(f_before)
        _make_facets_file(facets_dir, "sess-after-1", {"wrong_approach": 2})
        _make_facets_file(facets_dir, "sess-after-2", {"wrong_approach": 2})

        from keephive.storage import (
            compute_experiment_results,
            read_experiment_baselines,
            write_experiment_baselines,
        )

        write_experiment_baselines(
            {
                "abc123": {
                    "rule_text": "Test rule",
                    "added": "2026-01-01",
                    "baseline_friction": {},
                }
            }
        )

        compute_experiment_results()

        entry = read_experiment_baselines()["abc123"]
        assert entry.get("last_computed") == "2026-03-18", (
            f"Expected last_computed='2026-03-18', got: {entry.get('last_computed')}"
        )


# ---------------------------------------------------------------------------
# Phase B: sessionend.py trigger
# ---------------------------------------------------------------------------


def _run_sessionend(monkeypatch, input_data: dict) -> None:
    """Call hook_sessionend with mocked stdin."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(input_data)))
    from keephive.hooks.sessionend import hook_sessionend

    hook_sessionend([])


class TestSessionendEfficacyTrigger:
    """Tests that sessionend.py calls compute_experiment_results when baselines exist."""

    def test_no_baselines_file_skips_compute(self, hive_env, monkeypatch):
        """Guard check: no baselines file -> compute_experiment_results is NOT called."""
        calls = []

        monkeypatch.setattr(
            "keephive.storage.compute_experiment_results",
            lambda: calls.append(1) or False,
            raising=False,
        )

        from keephive.storage import experiment_baselines_file

        experiment_baselines_file().unlink(missing_ok=True)

        _run_sessionend(monkeypatch, {"session_id": "sess-guard-1", "reason": "exit"})

        assert calls == [], (
            "compute_experiment_results must NOT be called when no baselines file exists"
        )

    def test_baselines_exist_calls_compute(self, hive_env, monkeypatch):
        """Baselines file exists -> compute_experiment_results called exactly once."""
        calls = []

        monkeypatch.setattr(
            "keephive.storage.compute_experiment_results",
            lambda: calls.append(1) or False,
            raising=False,
        )

        from keephive.storage import experiment_baselines_file

        experiment_baselines_file().write_text(
            '{"abc": {"rule_text": "test", "added": "2026-01-01"}}'
        )

        _run_sessionend(monkeypatch, {"session_id": "sess-trigger-1", "reason": "exit"})

        assert len(calls) == 1, (
            f"compute_experiment_results must be called once when baselines exist, "
            f"got {len(calls)} calls"
        )

    def test_compute_throws_no_stdout(self, hive_env, monkeypatch, capsys):
        """compute_experiment_results raising must not produce any stdout."""

        def bad_compute() -> bool:
            raise RuntimeError("intentional test error from bad_compute")

        monkeypatch.setattr(
            "keephive.storage.compute_experiment_results",
            bad_compute,
            raising=False,
        )

        from keephive.storage import experiment_baselines_file

        experiment_baselines_file().write_text('{"abc": {"added": "2026-01-01"}}')

        _run_sessionend(monkeypatch, {"session_id": "sess-err-1", "reason": "exit"})

        out = capsys.readouterr().out
        assert out == "", (
            f"sessionend must never produce stdout even when compute raises, got: {out!r}"
        )


# ---------------------------------------------------------------------------
# Phase C: _recent_loop_completions() in sessionstart.py
# ---------------------------------------------------------------------------


class TestRecentLoopCompletions:
    """Tests for _recent_loop_completions() -- function not yet implemented."""

    def test_no_daily_logs_empty(self, hive_env, monkeypatch):
        """No daily log files -> returns empty list."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        from keephive.hooks.sessionstart import _recent_loop_completions

        result = _recent_loop_completions()
        assert result == []

    def test_log_with_no_loop_entries_empty(self, hive_env, monkeypatch):
        """Daily log exists but has no loop extract lines -> returns empty list."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        from keephive.storage import daily_dir

        log = daily_dir() / "2026-03-18.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("# Daily Log\n- FACT: something\n- DECISION: another\n")

        from keephive.hooks.sessionstart import _recent_loop_completions

        result = _recent_loop_completions()
        assert result == []

    def test_extract_line_parsed_correctly(self, hive_env, monkeypatch):
        """Extract line present -> one formatted entry returned."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        from keephive.storage import daily_dir

        log = daily_dir() / "2026-03-18.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "# Daily Log\n"
            "[Loop tiger-20260318-143022 iter 3: complete]\n"
            "[Loop tiger-20260318-143022 extract: 5 items queued for review]\n"
        )

        from keephive.hooks.sessionstart import _recent_loop_completions

        result = _recent_loop_completions()
        assert len(result) == 1
        assert "tiger" in result[0], f"Expected loop word 'tiger' in: {result[0]}"
        assert "5 items" in result[0], f"Expected '5 items' in: {result[0]}"

    def test_singular_item_no_plural(self, hive_env, monkeypatch):
        """1 item -> 'item' not 'items'."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        from keephive.storage import daily_dir

        log = daily_dir() / "2026-03-18.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("[Loop alpha-20260318-100000 extract: 1 items queued for review]\n")

        from keephive.hooks.sessionstart import _recent_loop_completions

        result = _recent_loop_completions()
        assert len(result) == 1
        assert "1 item" in result[0]
        assert "1 items" not in result[0], "Singular count must use 'item' not 'items'"

    def test_capped_at_three(self, hive_env, monkeypatch):
        """5 completions in log -> only first 3 returned."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        from keephive.storage import daily_dir

        log = daily_dir() / "2026-03-18.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"[Loop loop{i}-20260318-10000{i} extract: {i + 1} items queued for review]"
            for i in range(5)
        ]
        log.write_text("\n".join(lines) + "\n")

        from keephive.hooks.sessionstart import _recent_loop_completions

        result = _recent_loop_completions()
        assert len(result) == 3, f"Expected 3 (capped), got {len(result)}"

    def test_reads_yesterday_log(self, hive_env, monkeypatch):
        """Completion in yesterday's log (no today log) -> returned."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        from keephive.storage import daily_dir

        log = daily_dir() / "2026-03-17.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("[Loop beta-20260317-090000 extract: 2 items queued for review]\n")

        from keephive.hooks.sessionstart import _recent_loop_completions

        result = _recent_loop_completions()
        assert len(result) == 1
        assert "beta" in result[0], f"Expected loop word 'beta' in: {result[0]}"

    def test_today_completions_take_priority(self, hive_env, monkeypatch):
        """Today has 3 completions, yesterday has 1 -> only today's 3 returned (cap hit)."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-18")

        from keephive.storage import daily_dir

        daily_dir().mkdir(parents=True, exist_ok=True)

        today_log = daily_dir() / "2026-03-18.md"
        today_log.write_text(
            "[Loop a-20260318-100000 extract: 1 items queued for review]\n"
            "[Loop b-20260318-110000 extract: 2 items queued for review]\n"
            "[Loop c-20260318-120000 extract: 3 items queued for review]\n"
        )

        yesterday_log = daily_dir() / "2026-03-17.md"
        yesterday_log.write_text("[Loop d-20260317-090000 extract: 4 items queued for review]\n")

        from keephive.hooks.sessionstart import _recent_loop_completions

        result = _recent_loop_completions()
        assert len(result) == 3, f"Expected 3 (cap hit by today's entries), got {len(result)}"
        # Yesterday's loop 'd' should not appear
        combined = " ".join(result)
        assert "'d'" not in combined and "4 items" not in combined


# ---------------------------------------------------------------------------
# Phase D: growth story observation
# ---------------------------------------------------------------------------


class TestGrowthStoryExperimentObservation:
    """Tests for experiment efficacy narrative in _print_growth_story()."""

    def _mock_improvement_stats(self, monkeypatch) -> None:
        """Silence the improvement_history_stats call so it doesn't add noise."""
        monkeypatch.setattr(
            "keephive.commands.growth.improvement_history_stats",
            lambda: {
                "total_applied": 0,
                "total_dismissed": 0,
                "acceptance_rate": 0.0,
                "by_type": {},
                "recent": [],
            },
        )

    def test_no_experiments_no_observation(self, hive_env, monkeypatch, capsys):
        """No experiment results -> no efficacy observation in growth story."""
        monkeypatch.setattr(
            "keephive.commands.growth.experiment_results",
            lambda: [],
        )
        self._mock_improvement_stats(monkeypatch)

        from rich.console import Console

        from keephive.commands.growth import _print_growth_story

        _print_growth_story(Console(), _make_snap())

        out = capsys.readouterr().out
        assert "friction reduction" not in out
        assert "increased friction" not in out

    def test_all_measuring_no_observation(self, hive_env, monkeypatch, capsys):
        """All experiments have friction_delta=None (measuring) -> no observation."""
        monkeypatch.setattr(
            "keephive.commands.growth.experiment_results",
            lambda: [
                {"rule_text": "rule A", "friction_delta": None, "status": "measuring"},
                {"rule_text": "rule B", "friction_delta": None, "status": "measuring"},
            ],
        )
        self._mock_improvement_stats(monkeypatch)

        from rich.console import Console

        from keephive.commands.growth import _print_growth_story

        _print_growth_story(Console(), _make_snap())

        out = capsys.readouterr().out
        assert "friction reduction" not in out
        assert "increased friction" not in out

    def test_two_improving_experiments_observation(self, hive_env, monkeypatch, capsys):
        """2 experiments with friction_delta < -5 -> observation with avg reduction count."""
        monkeypatch.setattr(
            "keephive.commands.growth.experiment_results",
            lambda: [
                {"rule_text": "rule A", "friction_delta": -20.0, "status": "active"},
                {"rule_text": "rule B", "friction_delta": -30.0, "status": "active"},
            ],
        )
        self._mock_improvement_stats(monkeypatch)

        from rich.console import Console

        from keephive.commands.growth import _print_growth_story

        _print_growth_story(Console(), _make_snap())

        out = capsys.readouterr().out
        assert "friction reduction" in out, f"Expected 'friction reduction' in: {out!r}"
        assert "2 rule experiment" in out, f"Expected '2 rule experiment' count in: {out!r}"

    def test_single_improving_uses_singular(self, hive_env, monkeypatch, capsys):
        """1 improving experiment -> 'rule experiment' (singular, no 's')."""
        monkeypatch.setattr(
            "keephive.commands.growth.experiment_results",
            lambda: [
                {"rule_text": "good rule", "friction_delta": -10.0, "status": "active"},
            ],
        )
        self._mock_improvement_stats(monkeypatch)

        from rich.console import Console

        from keephive.commands.growth import _print_growth_story

        _print_growth_story(Console(), _make_snap())

        out = capsys.readouterr().out
        assert "1 rule experiment" in out
        assert "1 rule experiments" not in out

    def test_one_degrading_experiment_observation(self, hive_env, monkeypatch, capsys):
        """1 experiment with friction_delta > +5 -> 'increased friction' observation."""
        monkeypatch.setattr(
            "keephive.commands.growth.experiment_results",
            lambda: [
                {"rule_text": "bad rule", "friction_delta": 15.0, "status": "active"},
            ],
        )
        self._mock_improvement_stats(monkeypatch)

        from rich.console import Console

        from keephive.commands.growth import _print_growth_story

        _print_growth_story(Console(), _make_snap())

        out = capsys.readouterr().out
        assert "increased friction" in out, f"Expected 'increased friction' in: {out!r}"
        assert "1 rule experiment" in out

    def test_threshold_exactly_minus_five_not_improving(self, hive_env, monkeypatch, capsys):
        """friction_delta == -5.0 is NOT in the 'improving' category (threshold is < -5)."""
        monkeypatch.setattr(
            "keephive.commands.growth.experiment_results",
            lambda: [
                {"rule_text": "borderline rule", "friction_delta": -5.0, "status": "active"},
            ],
        )
        self._mock_improvement_stats(monkeypatch)

        from rich.console import Console

        from keephive.commands.growth import _print_growth_story

        _print_growth_story(Console(), _make_snap())

        out = capsys.readouterr().out
        # -5.0 is NOT < -5, so no "improving" observation
        assert "friction reduction" not in out

    def test_improving_beats_degrading(self, hive_env, monkeypatch, capsys):
        """Mixed results: 2 improving + 1 degrading -> 'improving' observation, not 'degrading'."""
        monkeypatch.setattr(
            "keephive.commands.growth.experiment_results",
            lambda: [
                {"rule_text": "good rule A", "friction_delta": -20.0, "status": "active"},
                {"rule_text": "good rule B", "friction_delta": -15.0, "status": "active"},
                {"rule_text": "bad rule", "friction_delta": 10.0, "status": "active"},
            ],
        )
        self._mock_improvement_stats(monkeypatch)

        from rich.console import Console

        from keephive.commands.growth import _print_growth_story

        _print_growth_story(Console(), _make_snap())

        out = capsys.readouterr().out
        # improving branch (elif, so improving wins over degrading)
        assert "friction reduction" in out
        assert "increased friction" not in out
