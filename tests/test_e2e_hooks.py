"""E2E tests for hook side effects (taskcompleted, sessionend).

Priority 3 from the E2E coverage gap analysis.

These hooks are silent (no stdout) and write to .stats.json / daily log.
Tests pipe JSON to stdin and verify file side effects.

Run: uv run pytest -m terminal -k test_e2e_hooks -v -o "addopts="
"""

from __future__ import annotations

import json

import pytest


# ============================================================
#  Priority 3a: TaskCompleted Hook
# ============================================================


@pytest.mark.terminal
class TestTaskCompletedHook:
    """Verify taskcompleted hook writes DONE entries and tracks stats."""

    def test_done_entry_written_to_daily(self, term, save_terminal_output):
        """Piping a task subject writes a DONE entry to today's daily log."""
        term.set_date("2026-03-10")
        # Ensure daily log exists with a header
        term.type("python -m keephive r 'FACT: Setup entry for daily log'").has("Remembered")

        # Pipe task completed event to the hook
        term.type(
            'echo \'{"task_subject":"Ship version 1.0 to production"}\' '
            "| python -m keephive hook-taskcompleted"
        )

        # Read daily log and verify DONE entry
        daily_content = term.read_file("daily/2026-03-10.md")
        assert "DONE: Ship version 1.0 to production" in daily_content, (
            f"Expected DONE entry in daily log, got:\n{daily_content}"
        )

        save_terminal_output("hooks/taskcompleted_done_entry", term)

    def test_stats_tracked(self, term, save_terminal_output):
        """Hook increments hooks.taskcompleted and meta.tasks_completed counters."""
        term.set_date("2026-03-10")
        term.type(
            'echo \'{"task_subject":"Complete integration tests"}\' '
            "| python -m keephive hook-taskcompleted"
        )

        # Read .stats.json and verify counters
        stats_raw = term.read_file(".stats.json")
        stats = json.loads(stats_raw)

        day_data = stats.get("days", {}).get("2026-03-10", {})
        assert day_data.get("hooks", {}).get("taskcompleted", 0) >= 1, (
            f"Expected hooks.taskcompleted >= 1, got: {day_data}"
        )
        assert day_data.get("meta", {}).get("tasks_completed", 0) >= 1, (
            f"Expected meta.tasks_completed >= 1, got: {day_data}"
        )

        save_terminal_output("hooks/taskcompleted_stats", term)

    def test_empty_subject_no_done_entry(self, term):
        """Empty task_subject does not write a DONE entry (stats still tracked)."""
        term.set_date("2026-03-10")
        term.type(
            'echo \'{"task_subject":""}\' '
            "| python -m keephive hook-taskcompleted"
        )

        # Daily log may not exist at all, or should have no DONE entry
        if term.file_exists("daily/2026-03-10.md"):
            daily = term.read_file("daily/2026-03-10.md")
            assert "DONE:" not in daily, f"Empty subject should not create DONE entry: {daily}"

    def test_invalid_json_graceful(self, term):
        """Invalid JSON input does not crash the hook."""
        term.set_date("2026-03-10")
        # This should exit silently without error
        screen = term.type(
            "echo 'not valid json' | python -m keephive hook-taskcompleted"
        )
        screen.lacks("Traceback", "Error")

    def test_multiple_completions_accumulate(self, term):
        """Multiple task completions increment counters correctly."""
        term.set_date("2026-03-10")
        for i in range(3):
            term.type(
                f'echo \'{{"task_subject":"Task number {i + 1} completed"}}\' '
                "| python -m keephive hook-taskcompleted"
            )

        stats_raw = term.read_file(".stats.json")
        stats = json.loads(stats_raw)
        day_data = stats.get("days", {}).get("2026-03-10", {})

        assert day_data.get("hooks", {}).get("taskcompleted", 0) >= 3, (
            f"Expected 3 taskcompleted events, got: {day_data}"
        )
        assert day_data.get("meta", {}).get("tasks_completed", 0) >= 3, (
            f"Expected 3 tasks_completed, got: {day_data}"
        )

        # All 3 DONE entries should be in daily log
        daily = term.read_file("daily/2026-03-10.md")
        for i in range(3):
            assert f"Task number {i + 1} completed" in daily


# ============================================================
#  Priority 3b: SessionEnd Hook
# ============================================================


@pytest.mark.terminal
class TestSessionEndHook:
    """Verify sessionend hook tracks events in .stats.json."""

    def test_event_tracked(self, term, save_terminal_output):
        """Piping a session end event increments hooks.sessionend counter."""
        term.set_date("2026-03-10")
        term.type(
            'echo \'{"session_id":"test-session-abc","reason":"user_exit"}\' '
            "| python -m keephive hook-sessionend"
        )

        stats_raw = term.read_file(".stats.json")
        stats = json.loads(stats_raw)
        day_data = stats.get("days", {}).get("2026-03-10", {})

        assert day_data.get("hooks", {}).get("sessionend", 0) >= 1, (
            f"Expected hooks.sessionend >= 1, got: {day_data}"
        )

        save_terminal_output("hooks/sessionend_event", term)

    def test_session_end_recorded(self, term, save_terminal_output):
        """Session end event creates session entry with end timestamp."""
        term.set_date("2026-03-10")
        term.type(
            'echo \'{"session_id":"end-test-session","reason":"timeout"}\' '
            "| python -m keephive hook-sessionend"
        )

        stats_raw = term.read_file(".stats.json")
        stats = json.loads(stats_raw)
        day_data = stats.get("days", {}).get("2026-03-10", {})
        sessions = day_data.get("sessions", {})

        assert "end-test-session" in sessions, (
            f"Expected session 'end-test-session' in sessions, got: {list(sessions.keys())}"
        )
        session = sessions["end-test-session"]
        assert "ended" in session, f"Expected 'ended' field in session data, got: {session}"
        assert "end_reason" in session, f"Expected 'end_reason' field, got: {session}"
        assert session["end_reason"] == "timeout"

        save_terminal_output("hooks/sessionend_recorded", term)

    def test_empty_session_id_noop(self, term):
        """Empty session_id is a no-op (no crash, no entry)."""
        term.set_date("2026-03-10")
        screen = term.type(
            'echo \'{"session_id":"","reason":"user_exit"}\' '
            "| python -m keephive hook-sessionend"
        )
        screen.lacks("Traceback", "Error")

    def test_invalid_json_graceful(self, term):
        """Invalid JSON input does not crash the hook."""
        screen = term.type(
            "echo 'garbage input' | python -m keephive hook-sessionend"
        )
        screen.lacks("Traceback", "Error")
