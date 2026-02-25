"""Tests for the Notification hook handler.

Covers: valid payload, missing message, bad JSON, missing session_id.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch


def _run_hook(payload: dict | str, hive_env) -> Path:
    """Run the notification hook with the given payload and return today's log path."""
    from keephive.clock import get_today
    from keephive.hooks.notification import hook_notification
    from keephive.storage import daily_file

    raw = json.dumps(payload) if isinstance(payload, dict) else payload
    with patch("sys.stdin", StringIO(raw)):
        hook_notification([])

    return daily_file(get_today().isoformat())


class TestNotificationHook:
    def test_valid_payload_logs_to_daily(self, hive_env):
        """Valid payload writes [Notification] entry to today's daily log."""
        log_path = _run_hook(
            {"session_id": "abc123", "message": "Task complete", "title": "Claude Code"},
            hive_env,
        )
        assert log_path.exists()
        content = log_path.read_text()
        assert "[Notification] Claude Code: Task complete" in content

    def test_missing_message_no_log_entry(self, hive_env):
        """Empty message skips logging — no daily log entry created."""
        log_path = _run_hook(
            {"session_id": "abc123", "message": "", "title": "Claude Code"},
            hive_env,
        )
        # Log file may not exist at all, or exist without the Notification entry
        if log_path.exists():
            assert "[Notification]" not in log_path.read_text()

    def test_bad_json_no_crash(self, hive_env):
        """Malformed JSON input returns silently — no exception raised."""
        from keephive.hooks.notification import hook_notification

        with patch("sys.stdin", StringIO("not json at all {")):
            hook_notification([])  # must not raise

    def test_missing_session_id_no_log_entry(self, hive_env):
        """Missing session_id triggers early return — nothing logged."""
        log_path = _run_hook(
            {"message": "Done", "title": "Claude Code"},
            hive_env,
        )
        if log_path.exists():
            assert "[Notification]" not in log_path.read_text()
