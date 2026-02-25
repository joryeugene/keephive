"""Tests for KingBee daemon — storage helpers, scheduling, and self-improve throttles.

Covers:
- daemon_config_file() / daemon_state_file() / daemon_pid_file() paths
- read_daemon_config(), daemon_task_enabled(), read_daemon_state(), write_daemon_state()
- _is_task_due() scheduling logic (daily + weekly)
- _mark_last_run() persistence
- _task_self_improve() time throttle (1-day) and depth cap (20 items)
- _enable_task() / _disable_task() via cmd_daemon enable/disable subcommands
- sessionend fires non-blocking Popen when soul-update is enabled
- _execute_task() track_event instrumentation (only fires on True result)
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock


class TestDaemonFilePaths:
    """daemon_config_file / daemon_state_file / daemon_pid_file live in hive_dir()."""

    def test_daemon_config_file_in_hive_dir(self, hive_env):
        """daemon_config_file() is hive_dir()/daemon.json."""
        from keephive.storage import daemon_config_file, hive_dir

        assert daemon_config_file() == hive_dir() / "daemon.json"

    def test_daemon_state_file_in_hive_dir(self, hive_env):
        """daemon_state_file() is hive_dir()/.daemon-state.json."""
        from keephive.storage import daemon_state_file, hive_dir

        assert daemon_state_file() == hive_dir() / ".daemon-state.json"

    def test_daemon_pid_file_in_hive_dir(self, hive_env):
        """daemon_pid_file() is hive_dir()/.daemon.pid."""
        from keephive.storage import daemon_pid_file, hive_dir

        assert daemon_pid_file() == hive_dir() / ".daemon.pid"


class TestReadDaemonConfig:
    """read_daemon_config() parses daemon.json or returns {} on missing/corrupt."""

    def test_returns_empty_when_missing(self, hive_env):
        """Returns {} when daemon.json does not exist."""
        from keephive.storage import daemon_config_file, read_daemon_config

        assert not daemon_config_file().exists()
        assert read_daemon_config() == {}

    def test_parses_valid_json(self, hive_env):
        """Returns parsed dict when daemon.json contains valid JSON."""
        from keephive.storage import daemon_config_file, read_daemon_config

        config = {"tasks": {"morning-briefing": {"enabled": False, "time": "07:00"}}}
        daemon_config_file().write_text(json.dumps(config))
        result = read_daemon_config()
        assert result["tasks"]["morning-briefing"]["time"] == "07:00"

    def test_returns_empty_on_corrupt_json(self, hive_env):
        """Returns {} when daemon.json is corrupted (not valid JSON)."""
        from keephive.storage import daemon_config_file, read_daemon_config

        daemon_config_file().write_text("{{{broken json")
        assert read_daemon_config() == {}


class TestDaemonTaskEnabled:
    """daemon_task_enabled() reads task enabled state from daemon.json."""

    def test_returns_false_when_config_missing(self, hive_env):
        """Returns False when daemon.json does not exist."""
        from keephive.storage import daemon_task_enabled

        assert daemon_task_enabled("morning-briefing") is False

    def test_returns_false_when_task_disabled(self, hive_env):
        """Returns False when task exists but enabled=False."""
        from keephive.storage import daemon_config_file, daemon_task_enabled

        config = {"tasks": {"soul-update": {"enabled": False}}}
        daemon_config_file().write_text(json.dumps(config))
        assert daemon_task_enabled("soul-update") is False

    def test_returns_true_when_task_enabled(self, hive_env):
        """Returns True when task exists and enabled=True."""
        from keephive.storage import daemon_config_file, daemon_task_enabled

        config = {"tasks": {"soul-update": {"enabled": True}}}
        daemon_config_file().write_text(json.dumps(config))
        assert daemon_task_enabled("soul-update") is True


class TestDaemonStateRoundtrip:
    """read_daemon_state() / write_daemon_state() persist and recover correctly."""

    def test_returns_empty_when_missing(self, hive_env):
        """Returns {} when .daemon-state.json does not exist."""
        from keephive.storage import daemon_state_file, read_daemon_state

        assert not daemon_state_file().exists()
        assert read_daemon_state() == {}

    def test_write_and_read_roundtrip(self, hive_env):
        """write_daemon_state() persists; read_daemon_state() recovers it exactly."""
        from keephive.storage import read_daemon_state, write_daemon_state

        state = {"soul-update": {"last_run": "2026-02-22T07:01:00"}}
        write_daemon_state(state)
        result = read_daemon_state()
        assert result == state

    def test_write_is_atomic(self, hive_env):
        """write_daemon_state() uses a .tmp file and replaces atomically."""
        from keephive.storage import daemon_state_file, write_daemon_state

        write_daemon_state({"k": "v"})
        # .tmp file must not remain after write
        tmp = daemon_state_file().with_suffix(".tmp")
        assert not tmp.exists()
        assert daemon_state_file().exists()


class TestMarkLastRun:
    """_mark_last_run() writes a timestamp to daemon state."""

    def test_mark_last_run_writes_timestamp(self, hive_env):
        """After _mark_last_run(name), read_daemon_state() has a last_run for that task."""
        from keephive.commands.daemon import _mark_last_run
        from keephive.storage import read_daemon_state

        _mark_last_run("soul-update")
        state = read_daemon_state()
        assert "soul-update" in state
        last_run = state["soul-update"]["last_run"]
        # Verify it's a parseable ISO timestamp close to now
        dt = datetime.fromisoformat(last_run)
        assert (datetime.now() - dt).total_seconds() < 5


class TestIsTaskDue:
    """_is_task_due() correctly gates task execution by time and day."""

    def test_due_when_never_run_and_past_task_time(self, hive_env):
        """Task is due when it has never run and current time is past the task's time."""
        from keephive.commands.daemon import _is_task_due

        config = {"time": "07:00"}
        state = {}  # never run
        now = datetime(2026, 2, 24, 9, 0, 0)  # 9am, past 7am
        assert _is_task_due("morning-briefing", config, state, now) is True

    def test_not_due_when_already_run_today(self, hive_env):
        """Task is not due when already run today after its scheduled time."""
        from keephive.commands.daemon import _is_task_due

        config = {"time": "07:00"}
        # Last run was today at 07:30 (after scheduled 07:00)
        last_run = datetime(2026, 2, 24, 7, 30, 0).isoformat()
        state = {"morning-briefing": {"last_run": last_run}}
        now = datetime(2026, 2, 24, 9, 0, 0)
        assert _is_task_due("morning-briefing", config, state, now) is False

    def test_not_due_before_task_time(self, hive_env):
        """Task is not due when current time is before the scheduled task time."""
        from keephive.commands.daemon import _is_task_due

        config = {"time": "09:00"}
        state = {}  # never run
        now = datetime(2026, 2, 24, 7, 0, 0)  # 7am, before 9am task
        assert _is_task_due("morning-briefing", config, state, now) is False

    def test_weekly_task_due_on_correct_day(self, hive_env):
        """Weekly task is due on the configured day when past the scheduled time."""
        from keephive.commands.daemon import _is_task_due

        config = {"time": "08:00", "day": "monday"}
        state = {}
        # 2026-02-23 is a Monday, at 09:00 (past 08:00)
        monday_9am = datetime(2026, 2, 23, 9, 0, 0)
        assert monday_9am.weekday() == 0  # Sanity check: 0 = Monday
        assert _is_task_due("stale-check", config, state, monday_9am) is True

    def test_weekly_task_not_due_on_wrong_day(self, hive_env):
        """Weekly task is not due when today is not the configured day."""
        from keephive.commands.daemon import _is_task_due

        config = {"time": "08:00", "day": "monday"}
        state = {}
        # 2026-02-24 is a Tuesday
        tuesday_9am = datetime(2026, 2, 24, 9, 0, 0)
        assert tuesday_9am.weekday() == 1  # Sanity check: 1 = Tuesday
        assert _is_task_due("stale-check", config, state, tuesday_9am) is False


class TestSelfImproveThrottles:
    """_task_self_improve() respects its 1-day time throttle and 20-item depth cap."""

    def test_skips_when_last_run_within_1_day(self, hive_env):
        """_task_self_improve() exits immediately when last_run < 1 day ago.

        No LLM call, no new proposals, queue stays empty.
        Bug caught: self-improve running more than daily (wasted LLM cost + noise).
        """
        from keephive.commands.daemon import _task_self_improve
        from keephive.storage import (
            read_daemon_state,
            read_pending_improvements,
            write_daemon_state,
        )

        # Set last_run to 12 hours ago (days_since == 0, which is < 1)
        twelve_hours_ago = (datetime.now() - timedelta(hours=12)).isoformat()
        write_daemon_state({"self-improve": {"last_run": twelve_hours_ago}})

        _task_self_improve()

        # Queue must be empty — no proposals were generated
        assert read_pending_improvements() == []
        # last_run should still be the old value (not updated on skip)
        state = read_daemon_state()
        assert state["self-improve"]["last_run"] == twelve_hours_ago

    def test_skips_when_queue_at_20_items(self, hive_env):
        """_task_self_improve() exits immediately when pending queue has >= 20 items.

        Prevents proposal pile-up when user hasn't reviewed yet.
        Bug caught: queue growing unbounded, overwhelming the user with stale proposals.
        """
        from keephive.commands.daemon import _task_self_improve
        from keephive.storage import (
            append_pending_improvements,
            read_pending_improvements,
        )

        # Seed 20 items into the queue
        items = [{"type": "skill", "name": f"skill-{i}", "rationale": "test"} for i in range(20)]
        append_pending_improvements(items)
        assert len(read_pending_improvements()) == 20

        _task_self_improve()

        # Queue must still be exactly 20 — no new items appended
        assert len(read_pending_improvements()) == 20


class TestSelfImproveThrottleConstant:
    """_SELF_IMPROVE_THROTTLE_DAYS is 1 — daily feedback loop, not weekly."""

    def test_throttle_constant_is_one_day(self):
        """_SELF_IMPROVE_THROTTLE_DAYS must be 1 (daily cadence).

        Bug caught: constant reverted to 7 would silently miss weekly-active users
        whose patterns emerge mid-week, defeating the self-evolving system goal.
        """
        from keephive.commands.daemon import _SELF_IMPROVE_THROTTLE_DAYS

        assert _SELF_IMPROVE_THROTTLE_DAYS == 1


class TestEnableDisableTask:
    """cmd_daemon enable/disable subcommands toggle task enabled state."""

    def test_enable_task_sets_enabled_true(self, hive_env):
        """enable writes enabled=True for a disabled task.

        Bug caught: enabling a task silently fails if JSON write path is wrong.
        """
        from keephive.commands.daemon import cmd_daemon
        from keephive.storage import daemon_config_file, read_daemon_config

        config = {"tasks": {"morning-briefing": {"enabled": False, "time": "07:00"}}}
        daemon_config_file().write_text(json.dumps(config))

        cmd_daemon(["enable", "morning-briefing"])

        result = read_daemon_config()
        assert result["tasks"]["morning-briefing"]["enabled"] is True

    def test_disable_task_sets_enabled_false(self, hive_env):
        """disable writes enabled=False for an enabled task.

        Bug caught: disable path not wired, task stays enabled after command.
        """
        from keephive.commands.daemon import cmd_daemon
        from keephive.storage import daemon_config_file, read_daemon_config

        config = {"tasks": {"stale-check": {"enabled": True, "time": "09:00"}}}
        daemon_config_file().write_text(json.dumps(config))

        cmd_daemon(["disable", "stale-check"])

        result = read_daemon_config()
        assert result["tasks"]["stale-check"]["enabled"] is False

    def test_enable_unknown_task_prints_error(self, hive_env, capsys):
        """enable with unknown task name prints error and known tasks.

        Bug caught: KeyError crash when task not in config.
        """
        from keephive.commands.daemon import cmd_daemon
        from keephive.storage import daemon_config_file

        config = {"tasks": {"soul-update": {"enabled": True}}}
        daemon_config_file().write_text(json.dumps(config))

        cmd_daemon(["enable", "nonexistent-task"])

        out = capsys.readouterr().out
        assert "Unknown task" in out
        assert "soul-update" in out

    def test_enable_no_task_name_prints_usage(self, capsys):
        """enable with no task name argument prints usage hint.

        Bug caught: IndexError on args[1] when subcommand called with no extra arg.
        """
        from keephive.commands.daemon import cmd_daemon

        cmd_daemon(["enable"])

        out = capsys.readouterr().out
        assert "Usage" in out
        assert "enable" in out

    def test_disable_no_task_name_prints_usage(self, capsys):
        """disable with no task name argument prints usage hint."""
        from keephive.commands.daemon import cmd_daemon

        cmd_daemon(["disable"])

        out = capsys.readouterr().out
        assert "Usage" in out
        assert "disable" in out


class TestSoulUpdateThrottle:
    """_task_soul_update() respects its 1-hour time throttle."""

    def test_skips_when_run_within_1_hour(self, hive_env):
        """_task_soul_update() exits immediately when last_run < 1 hour ago.

        Bug caught: soul-update running multiple times per session when
        PreCompact fires frequently — wastes LLM calls and produces noise.
        """
        from keephive.commands.daemon import _task_soul_update
        from keephive.storage import read_daemon_state, write_daemon_state

        # Set last_run to 30 minutes ago
        thirty_min_ago = (datetime.now() - timedelta(minutes=30)).isoformat()
        write_daemon_state({"soul-update": {"last_run": thirty_min_ago}})

        _task_soul_update()

        # last_run must NOT be updated — task was skipped by throttle
        state = read_daemon_state()
        assert state["soul-update"]["last_run"] == thirty_min_ago

    def test_runs_when_older_than_1_hour(self, hive_env, monkeypatch):
        """_task_soul_update() runs when last_run > 1 hour ago (throttle not triggered).

        Confirms the 1-hour gate only blocks recent runs, not older ones.
        """
        from unittest.mock import MagicMock

        from keephive.commands.daemon import _task_soul_update
        from keephive.storage import append_to_daily, ensure_daily, write_daemon_state

        # Set last_run to 2 hours ago
        two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
        write_daemon_state({"soul-update": {"last_run": two_hours_ago}})

        # Write today's log so the empty-log early-exit doesn't trigger
        ensure_daily()
        append_to_daily("- FACT: test entry for throttle test")

        # Mock run_claude_pipe to capture call without hitting real LLM
        llm_called = []
        mock_result = MagicMock()
        mock_result.content = "# SOUL.md\n## Summary\nThrottle bypassed."

        def mock_pipe(prompt, model_class, **kwargs):
            llm_called.append(prompt)
            return mock_result

        monkeypatch.setattr("keephive.claude.run_claude_pipe", mock_pipe)

        _task_soul_update()

        assert llm_called, "Expected LLM call — 1-hour throttle should not have blocked this"


class TestRunTaskThrottle:
    """_run_task only calls _mark_last_run when the task returns True (did work)."""

    def test_run_task_soul_update_within_throttle_does_not_mark(self, monkeypatch, hive_env):
        """_run_task soul-update within 1h throttle must NOT write last_run.

        Bug caught: running 'hive daemon run soul-update' within the 1-hour window
        used to reset the throttle even though no work was done, pushing the next
        eligible run further out.
        """
        from keephive.commands.daemon import _run_task
        from keephive.storage import write_daemon_state

        # Pre-set last_run to 10 minutes ago (inside 1h throttle)
        state = {"soul-update": {"last_run": (datetime.now() - timedelta(minutes=10)).isoformat()}}
        write_daemon_state(state)

        mark_calls: list[str] = []
        monkeypatch.setattr(
            "keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t)
        )

        _run_task("soul-update")  # should throttle — _task_soul_update returns False

        assert mark_calls == [], f"Expected no _mark_last_run calls, got: {mark_calls}"

    def test_run_task_soul_update_outside_throttle_marks_once(self, monkeypatch, hive_env):
        """_run_task soul-update outside 1h throttle calls _mark_last_run exactly once.

        Bug caught: double _mark_last_run — internal call in _task_soul_update plus
        the unconditional call in _run_task meant two JSON writes for the same event.
        """
        from keephive.commands.daemon import _run_task
        from keephive.storage import write_daemon_state

        # Pre-set last_run to 2 hours ago (outside 1h throttle)
        state = {"soul-update": {"last_run": (datetime.now() - timedelta(hours=2)).isoformat()}}
        write_daemon_state(state)

        mark_calls: list[str] = []
        monkeypatch.setattr(
            "keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t)
        )
        # Simulate work done without needing real log files or LLM
        monkeypatch.setattr("keephive.commands.daemon._task_soul_update", lambda: True)

        _run_task("soul-update")

        assert mark_calls == ["soul-update"], (
            f"Expected exactly one _mark_last_run('soul-update'), got: {mark_calls}"
        )

    def test_run_task_self_improve_within_throttle_does_not_mark(self, monkeypatch, hive_env):
        """_run_task self-improve within 1d throttle must NOT write last_run.

        Bug caught: throttle was being reset on every manual run even when
        self-improve returned early — pushing the next eligible run further out.
        """
        from keephive.commands.daemon import _run_task
        from keephive.storage import write_daemon_state

        # Pre-set last_run to 6 hours ago (days_since == 0, inside 1d throttle)
        state = {"self-improve": {"last_run": (datetime.now() - timedelta(hours=6)).isoformat()}}
        write_daemon_state(state)

        mark_calls: list[str] = []
        monkeypatch.setattr(
            "keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t)
        )

        _run_task("self-improve")  # should throttle — _task_self_improve returns False

        assert mark_calls == [], f"Expected no _mark_last_run calls, got: {mark_calls}"


class TestSessionendFiresSoulUpdate:
    """sessionend fires a non-blocking subprocess for soul-update when enabled."""

    def test_popen_called_with_soul_update_when_enabled(self, hive_env, monkeypatch):
        """hook_sessionend spawns 'daemon run soul-update' subprocess when task is enabled.

        Bug caught: soul-update silently not running because Popen args were wrong.
        """
        from keephive.storage import daemon_config_file

        # Enable soul-update in daemon.json
        daemon_config_file().write_text(json.dumps({"tasks": {"soul-update": {"enabled": True}}}))

        popen_calls: list[list[str]] = []

        def mock_popen(cmd, **kwargs):
            popen_calls.append(list(cmd))
            return MagicMock()

        monkeypatch.setattr("subprocess.Popen", mock_popen)

        payload = json.dumps({"session_id": "sess-se-popen", "reason": "user_exit"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        from keephive.hooks.sessionend import hook_sessionend

        hook_sessionend([])

        # At least one Popen call should target daemon run soul-update
        soul_update_calls = [c for c in popen_calls if "daemon" in c and "soul-update" in c]
        assert soul_update_calls, (
            f"Expected Popen call with 'daemon run soul-update', got: {popen_calls}"
        )

    def test_popen_not_called_when_soul_update_disabled(self, hive_env, monkeypatch):
        """hook_sessionend does NOT spawn soul-update when task is disabled."""
        from keephive.storage import daemon_config_file

        daemon_config_file().write_text(json.dumps({"tasks": {"soul-update": {"enabled": False}}}))

        popen_calls: list[list[str]] = []

        def mock_popen(cmd, **kwargs):
            popen_calls.append(list(cmd))
            return MagicMock()

        monkeypatch.setattr("subprocess.Popen", mock_popen)

        payload = json.dumps({"session_id": "sess-se-disabled", "reason": "user_exit"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        from keephive.hooks.sessionend import hook_sessionend

        hook_sessionend([])

        soul_update_calls = [c for c in popen_calls if "daemon" in c and "soul-update" in c]
        assert not soul_update_calls, "soul-update should NOT fire when disabled in daemon.json"


class TestTickSkipBehavior:
    """_tick only calls _mark_last_run when _execute_task returns True."""

    def test_tick_does_not_mark_last_run_when_execute_returns_false(self, monkeypatch, hive_env):
        """_tick must not call _mark_last_run when _execute_task returns False.

        Bug caught: stale-check was being marked as 'completed' in daemon.log
        even when it skipped (no memory.md), misleading the audit trail.
        """
        import json

        from keephive.commands.daemon import _tick
        from keephive.storage import daemon_config_file, hive_dir, write_daemon_state

        # Configure stale-check as due (last_run far in the past)
        config = {
            "tasks": {
                "stale-check": {
                    "enabled": True,
                    "time": "00:00",
                }
            }
        }
        daemon_config_file().write_text(json.dumps(config))
        write_daemon_state({})  # no last_run → task is due

        mark_calls: list[str] = []
        monkeypatch.setattr(
            "keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t)
        )
        # Force _execute_task to return False (simulates stale-check with no memory.md)
        monkeypatch.setattr("keephive.commands.daemon._execute_task", lambda t: False)

        _tick()

        # _mark_last_run must NOT have been called
        assert mark_calls == [], f"Expected no _mark_last_run calls, got: {mark_calls}"

        # daemon.log must contain "skipped (no data)", NOT "completed"
        log_text = (hive_dir() / "daemon.log").read_text()
        assert "skipped (no data)" in log_text, f"Expected 'skipped (no data)' in log: {log_text}"
        assert "completed: stale-check" not in log_text, (
            f"Must not log 'completed' on skip: {log_text}"
        )

    def test_tick_marks_last_run_when_execute_returns_true(self, monkeypatch, hive_env):
        """_tick calls _mark_last_run exactly once when _execute_task returns True.

        Bug caught: if _tick had ignored return value in both directions, tasks that
        returned True would also skip the state write — this confirms the happy path.
        """
        import json

        from keephive.commands.daemon import _tick
        from keephive.storage import daemon_config_file, hive_dir, write_daemon_state

        config = {
            "tasks": {
                "stale-check": {
                    "enabled": True,
                    "time": "00:00",
                }
            }
        }
        daemon_config_file().write_text(json.dumps(config))
        write_daemon_state({})

        mark_calls: list[str] = []
        monkeypatch.setattr(
            "keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t)
        )
        monkeypatch.setattr("keephive.commands.daemon._execute_task", lambda t: True)

        _tick()

        assert mark_calls == ["stale-check"], (
            f"Expected _mark_last_run('stale-check'), got: {mark_calls}"
        )
        log_text = (hive_dir() / "daemon.log").read_text()
        assert "completed: stale-check" in log_text, f"Expected 'completed' in log: {log_text}"


class TestTaskReturnValues:
    """Daemon task functions return False on LLM error, True only when work was done."""

    def test_morning_briefing_returns_false_on_llm_error(self, hive_env, monkeypatch):
        """_task_morning_briefing() returns False when ClaudePipeError is raised.

        Bug caught: task always returned True even on timeout, marking itself
        done and preventing retry until the next scheduled slot (tomorrow 07:00).
        """
        from keephive.claude import ClaudePipeError
        from keephive.commands.daemon import _task_morning_briefing
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        append_to_daily("- FACT: morning briefing test entry")

        monkeypatch.setattr(
            "keephive.claude.run_claude_pipe",
            lambda *a, **kw: (_ for _ in ()).throw(ClaudePipeError("timed out")),
        )

        result = _task_morning_briefing()

        assert result is False, "morning-briefing must return False on LLM error"

    def test_stale_check_returns_false_on_llm_error(self, hive_env, monkeypatch):
        """_task_stale_check() returns False when ClaudePipeError is raised.

        Bug caught: stale-check marked itself done on error, skipping
        the next scheduled slot even though no check was performed.
        """
        from keephive.claude import ClaudePipeError
        from keephive.commands.daemon import _task_stale_check
        from keephive.storage import hive_dir

        (hive_dir() / "memory.md").write_text(
            "# Working Memory\n\n- Some fact to check [verified:2020-01-01]\n"
        )

        monkeypatch.setattr(
            "keephive.claude.run_claude_pipe",
            lambda *a, **kw: (_ for _ in ()).throw(ClaudePipeError("timed out")),
        )

        result = _task_stale_check()

        assert result is False, "stale-check must return False on LLM error"

    def test_standup_draft_returns_false_on_gather_exception(self, hive_env, monkeypatch):
        """_task_standup_draft() returns False when _gather_raw_data raises.

        Bug caught: previously returned True regardless of exception — task
        would mark itself done with empty daily log on any failure.
        """
        from keephive.commands.daemon import _task_standup_draft

        monkeypatch.setattr(
            "keephive.commands.standup._gather_raw_data",
            lambda: (_ for _ in ()).throw(RuntimeError("no git")),
        )

        result = _task_standup_draft()

        assert result is False, "standup-draft must return False on exception"

    def test_standup_draft_returns_true_when_content_written(self, hive_env, monkeypatch):
        """_task_standup_draft() returns True and writes to daily log when LLM produces content.

        Confirms the happy path: real content → appended to log → True returned.
        """
        from keephive.commands.daemon import _task_standup_draft
        from keephive.storage import daily_file, ensure_daily

        ensure_daily()

        fake_data = {
            "recent_done": [],
            "open_todos": [],
            "merged_prs": [],
            "closed_prs": [],
            "open_prs": [],
        }
        fake_content = "**Done:** Fixed daemon return values\n**Next:** Write tests"

        monkeypatch.setattr("keephive.commands.standup._gather_raw_data", lambda: fake_data)
        monkeypatch.setattr("keephive.commands.standup._display_llm", lambda d: fake_content)

        result = _task_standup_draft()

        assert result is True, "standup-draft must return True when content was written"
        log = daily_file().read_text()
        assert "standup draft" in log, "Daily log must contain standup draft header"
        assert "Fixed daemon return values" in log, "Daily log must contain standup content"


class TestVoiceDisciplineConstant:
    """_VOICE_DISCIPLINE constant is defined and injected into all daemon prompts."""

    def test_constant_exists_and_is_nonempty(self):
        """_VOICE_DISCIPLINE is importable and non-empty."""
        from keephive.commands.daemon import _VOICE_DISCIPLINE

        assert _VOICE_DISCIPLINE
        assert len(_VOICE_DISCIPLINE.strip()) > 20

    def test_constant_contains_no_opener_constraint(self):
        """Constant includes the opener constraint."""
        from keephive.commands.daemon import _VOICE_DISCIPLINE

        assert "Here is" in _VOICE_DISCIPLINE

    def test_constant_injected_in_morning_briefing(self, hive_env, monkeypatch):
        """_VOICE_DISCIPLINE text appears in the morning_briefing prompt."""
        from keephive.commands.daemon import _VOICE_DISCIPLINE, _task_morning_briefing

        captured_prompts = []

        def fake_pipe(prompt, *args, **kwargs):
            captured_prompts.append(prompt)
            return None

        monkeypatch.setattr("keephive.claude.run_claude_pipe", fake_pipe)
        _task_morning_briefing()

        assert captured_prompts, "Expected run_claude_pipe to be called"
        assert _VOICE_DISCIPLINE.strip() in captured_prompts[0]

    def test_constant_injected_in_stale_check(self, hive_env, monkeypatch):
        """_VOICE_DISCIPLINE text appears in the stale_check prompt."""
        from keephive.commands.daemon import _VOICE_DISCIPLINE, _task_stale_check

        captured_prompts = []

        def fake_pipe(prompt, *args, **kwargs):
            captured_prompts.append(prompt)
            return None

        # Create memory.md so stale_check doesn't return early
        memory_path = hive_env / "memory.md"
        memory_path.write_text("- FACT: test fact [verified:2020-01-01]\n")

        monkeypatch.setattr("keephive.claude.run_claude_pipe", fake_pipe)
        _task_stale_check()

        assert captured_prompts, "Expected run_claude_pipe to be called"
        assert _VOICE_DISCIPLINE.strip() in captured_prompts[0]

    def test_constant_injected_in_soul_update(self, hive_env, monkeypatch):
        """_VOICE_DISCIPLINE text appears in the soul_update prompt."""
        from keephive.commands.daemon import _VOICE_DISCIPLINE, _task_soul_update

        captured_prompts = []

        def fake_pipe(prompt, *args, **kwargs):
            captured_prompts.append(prompt)
            return None

        # Create today's log so soul_update doesn't return early
        from keephive.clock import get_today
        from keephive.storage import daily_file

        today = get_today()
        df = daily_file(today.isoformat())
        df.parent.mkdir(parents=True, exist_ok=True)
        df.write_text("- [10:00:00] FACT: test fact today\n" * 5)

        monkeypatch.setattr("keephive.claude.run_claude_pipe", fake_pipe)
        _task_soul_update()

        assert captured_prompts, "Expected run_claude_pipe to be called"
        assert _VOICE_DISCIPLINE.strip() in captured_prompts[0]

    def test_constant_injected_in_wander(self, hive_env, monkeypatch):
        """_VOICE_DISCIPLINE text appears in the wander prompt."""
        from keephive.commands.daemon import _VOICE_DISCIPLINE, _task_wander
        from keephive.commands.wander import select_wander_seed

        captured_prompts = []

        def fake_pipe(prompt, *args, **kwargs):
            captured_prompts.append(prompt)
            return None

        monkeypatch.setattr("keephive.claude.run_claude_pipe", fake_pipe)
        monkeypatch.setattr(
            "keephive.commands.wander.select_wander_seed",
            lambda *a, **kw: ("test seed", "user-queued"),
        )
        _task_wander()

        assert captured_prompts, "Expected run_claude_pipe to be called"
        assert _VOICE_DISCIPLINE.strip() in captured_prompts[0]


class TestExecuteTaskTracking:
    """_execute_task() writes to daemon_tasks category only when task does real work."""

    def test_track_event_called_when_task_returns_true(self, hive_env, monkeypatch):
        """track_event("daemon_tasks", task_name) fires when task fn returns True."""
        from keephive.commands.daemon import _execute_task

        tracked: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "keephive.commands.daemon.track_event",
            lambda cat, name: tracked.append((cat, name)),
        )
        monkeypatch.setattr("keephive.commands.daemon._task_wander", lambda: True)

        _execute_task("wander")

        assert tracked == [("daemon_tasks", "wander")]

    def test_track_event_not_called_when_task_returns_false(self, hive_env, monkeypatch):
        """track_event is NOT called when task fn returns False (throttled or skipped)."""
        from keephive.commands.daemon import _execute_task

        tracked: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "keephive.commands.daemon.track_event",
            lambda cat, name: tracked.append((cat, name)),
        )
        monkeypatch.setattr("keephive.commands.daemon._task_wander", lambda: False)

        _execute_task("wander")

        assert tracked == []

    def test_track_event_not_called_for_unknown_task(self, hive_env, monkeypatch):
        """track_event is NOT called and False is returned for unknown task names."""
        from keephive.commands.daemon import _execute_task

        tracked: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "keephive.commands.daemon.track_event",
            lambda cat, name: tracked.append((cat, name)),
        )

        result = _execute_task("nonexistent-task")

        assert result is False
        assert tracked == []

    def test_execute_task_returns_true_when_task_succeeds(self, hive_env, monkeypatch):
        """_execute_task() propagates the True return from the task fn."""
        from keephive.commands.daemon import _execute_task

        monkeypatch.setattr("keephive.commands.daemon.track_event", lambda *a: None)
        monkeypatch.setattr("keephive.commands.daemon._task_soul_update", lambda: True)

        assert _execute_task("soul-update") is True

    def test_execute_task_returns_false_when_task_skips(self, hive_env, monkeypatch):
        """_execute_task() propagates False when the task fn returns False."""
        from keephive.commands.daemon import _execute_task

        monkeypatch.setattr("keephive.commands.daemon.track_event", lambda *a: None)
        monkeypatch.setattr("keephive.commands.daemon._task_soul_update", lambda: False)

        assert _execute_task("soul-update") is False

    def test_track_event_records_correct_task_name_for_each_task(
        self, hive_env, monkeypatch
    ):
        """Each of the 6 task names produces a track_event with that exact name."""
        from keephive.commands.daemon import _execute_task

        task_pairs = [
            ("morning-briefing", "_task_morning_briefing"),
            ("stale-check", "_task_stale_check"),
            ("standup-draft", "_task_standup_draft"),
            ("soul-update", "_task_soul_update"),
            ("self-improve", "_task_self_improve"),
            ("wander", "_task_wander"),
        ]
        for task_name, fn_attr in task_pairs:
            tracked: list[tuple[str, str]] = []
            monkeypatch.setattr(
                "keephive.commands.daemon.track_event",
                lambda cat, name: tracked.append((cat, name)),
            )
            monkeypatch.setattr(f"keephive.commands.daemon.{fn_attr}", lambda: True)
            _execute_task(task_name)
            assert tracked == [("daemon_tasks", task_name)], f"Failed for {task_name}"
