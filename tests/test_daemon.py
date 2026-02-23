"""Tests for KingBee daemon — storage helpers, scheduling, and self-improve throttles.

Covers:
- daemon_config_file() / daemon_state_file() / daemon_pid_file() paths
- read_daemon_config(), daemon_task_enabled(), read_daemon_state(), write_daemon_state()
- _is_task_due() scheduling logic (daily + weekly)
- _mark_last_run() persistence
- _task_self_improve() time throttle (1-day) and depth cap (20 items)
- _enable_task() / _disable_task() via cmd_daemon enable/disable subcommands
- sessionend fires non-blocking Popen when soul-update is enabled
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
        monkeypatch.setattr("keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t))

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
        monkeypatch.setattr("keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t))
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
        monkeypatch.setattr("keephive.commands.daemon._mark_last_run", lambda t: mark_calls.append(t))

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
