"""Tests for the TaskCompleted hook handler."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


def run_hook(input_data: dict | str, monkeypatch, hive_env: Path) -> None:
    """Call hook_taskcompleted with mocked stdin."""
    if isinstance(input_data, dict):
        raw = json.dumps(input_data)
    else:
        raw = input_data
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    from keephive.hooks.taskcompleted import hook_taskcompleted

    hook_taskcompleted([])


def read_daily(hive_env: Path) -> str:
    """Read today's daily log content."""
    from keephive.clock import get_today

    day = get_today().isoformat()
    path = hive_env / "daily" / f"{day}.md"
    return path.read_text() if path.exists() else ""


class TestHookTaskCompleted:
    def test_valid_subject_done_entry(self, hive_env, monkeypatch):
        """Valid task_subject -> DONE entry appears in today's daily log."""
        run_hook({"task_subject": "Fix the authentication bug"}, monkeypatch, hive_env)
        content = read_daily(hive_env)
        assert "DONE: Fix the authentication bug" in content

    def test_valid_subject_stats_updated(self, hive_env, monkeypatch):
        """Valid task_subject -> tasks_completed counter increments in stats."""
        from keephive.clock import get_today
        from keephive.storage import read_stats

        run_hook({"task_subject": "Write more tests"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        count = data.get("days", {}).get(day, {}).get("meta", {}).get("tasks_completed", 0)
        assert count >= 1

    def test_empty_subject_no_done_entry(self, hive_env, monkeypatch):
        """Empty task_subject -> no DONE entry written."""
        run_hook({"task_subject": ""}, monkeypatch, hive_env)
        content = read_daily(hive_env)
        assert "DONE:" not in content

    def test_missing_subject_field_noop(self, hive_env, monkeypatch):
        """No task_subject key -> defaults to "" -> no entry, no crash."""
        run_hook({}, monkeypatch, hive_env)
        content = read_daily(hive_env)
        assert "DONE:" not in content

    def test_invalid_json_no_crash(self, hive_env, monkeypatch):
        """Malformed JSON -> exits silently, no entry, no crash."""
        run_hook("{{{{not json", monkeypatch, hive_env)
        content = read_daily(hive_env)
        assert "DONE:" not in content

    def test_multiple_invocations_accumulate(self, hive_env, monkeypatch):
        """Three hook calls -> tasks_completed counter == 3."""
        from keephive.clock import get_today
        from keephive.storage import read_stats

        for i in range(3):
            run_hook({"task_subject": f"Task number {i}"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        count = data.get("days", {}).get(day, {}).get("meta", {}).get("tasks_completed", 0)
        assert count == 3

    def test_newline_in_subject_sanitized(self, hive_env, monkeypatch):
        """BUG-5 fix: newline in task_subject is replaced with a space.

        Before the fix: "First line\\nSecond line" would produce:
            - [HH:MM:SS] DONE: First line
            Second line
        The second line is orphaned text that corrupts collect_todos() parsing.

        After the fix: both lines merged to "First line Second line".
        """
        run_hook({"task_subject": "First line\nSecond line"}, monkeypatch, hive_env)
        content = read_daily(hive_env)

        # The entry must be a single line
        assert "DONE: First line Second line" in content

        # "Second line" must NOT appear as an orphaned line
        for line in content.splitlines():
            assert not line.startswith("Second"), (
                f"Orphaned line found: {line!r} — BUG-5 not fixed"
            )

        # collect_todos() must be able to parse DONEs correctly
        from keephive.storage import collect_todos

        _todos, dones = collect_todos()
        assert any("first line" in d for d in dones), (
            f"collect_todos() failed to recognize DONE entry. dones={dones}"
        )

    def test_carriage_return_in_subject_sanitized(self, hive_env, monkeypatch):
        """\\r in task_subject is also replaced with a space."""
        run_hook({"task_subject": "Windows\r\nline endings"}, monkeypatch, hive_env)
        content = read_daily(hive_env)
        assert "DONE: Windows  line endings" in content or "DONE: Windows line endings" in content
        # No orphaned lines
        for line in content.splitlines():
            assert not line.startswith("line endings")

    def test_tab_in_subject_handled(self, hive_env, monkeypatch):
        """Tab in task_subject: entry is written and log remains parseable."""
        run_hook({"task_subject": "Fix\ttab\there"}, monkeypatch, hive_env)
        content = read_daily(hive_env)
        assert "DONE:" in content
        # Daily log must be parseable
        from keephive.storage import collect_todos

        collect_todos()  # must not crash

    def test_track_event_failure_continues(self, hive_env, monkeypatch):
        """track_event raising an exception does not prevent DONE entry."""
        import keephive.storage as _storage

        def failing_track(*a, **kw):
            raise RuntimeError("stats error")

        monkeypatch.setattr(_storage, "track_event", failing_track)

        run_hook({"task_subject": "Task despite error"}, monkeypatch, hive_env)
        content = read_daily(hive_env)
        assert "DONE: Task despite error" in content

    def test_append_failure_continues(self, hive_env, monkeypatch):
        """append_to_daily raising does not crash the hook."""
        import keephive.storage as _storage

        def failing_append(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(_storage, "append_to_daily", failing_append)

        # Should not raise
        run_hook({"task_subject": "Append failure task"}, monkeypatch, hive_env)
        # No assertion on content, just verify no exception propagated
