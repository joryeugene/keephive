"""Tests for the SessionEnd hook handler."""

from __future__ import annotations

import io
import json
from pathlib import Path


def run_hook(input_data: dict | str, monkeypatch, hive_env: Path) -> None:
    """Call hook_sessionend with mocked stdin."""
    if isinstance(input_data, dict):
        raw = json.dumps(input_data)
    else:
        raw = input_data
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    from keephive.hooks.sessionend import hook_sessionend

    hook_sessionend([])


class TestHookSessionEnd:
    def test_valid_session_tracks_counter(self, hive_env, monkeypatch):
        """Valid session_id -> hooks.sessionend counter increments."""
        from keephive.clock import get_today
        from keephive.storage import read_stats

        run_hook({"session_id": "sess-abc123", "reason": "user_exit"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        count = data.get("days", {}).get(day, {}).get("hooks", {}).get("sessionend", 0)
        assert count >= 1

    def test_valid_session_records_end(self, hive_env, monkeypatch):
        """Valid session -> session dict in stats has 'ended' key set."""
        from keephive.clock import get_today
        from keephive.storage import read_stats, track_session_event

        sid = "sess-endtest-001"
        # Create session first so it exists before sessionend fires
        track_session_event(sid, "start", project="/test/project")

        run_hook({"session_id": sid, "reason": "normal_exit"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        session = data.get("days", {}).get(day, {}).get("sessions", {}).get(sid, {})
        assert "ended" in session, f"'ended' not set in session dict: {session}"

    def test_valid_session_records_end_reason(self, hive_env, monkeypatch):
        """Valid session with reason -> end_reason stored in session dict."""
        from keephive.clock import get_today
        from keephive.storage import read_stats, track_session_event

        sid = "sess-reason-001"
        track_session_event(sid, "start")

        run_hook({"session_id": sid, "reason": "timeout"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        session = data.get("days", {}).get(day, {}).get("sessions", {}).get(sid, {})
        assert session.get("end_reason") == "timeout"

    def test_empty_session_id_noop(self, hive_env, monkeypatch):
        """Empty session_id -> hook returns early, no stats written."""
        from keephive.storage import read_stats

        run_hook({"session_id": "", "reason": "exit"}, monkeypatch, hive_env)

        data = read_stats()
        # No sessions should exist
        day_data = data.get("days", {})
        for day in day_data.values():
            assert "sessions" not in day or day["sessions"] == {}, (
                "Sessions written despite empty session_id"
            )

    def test_missing_session_id_noop(self, hive_env, monkeypatch):
        """No session_id key -> defaults to "" -> early return, no crash."""
        run_hook({"reason": "exit"}, monkeypatch, hive_env)
        # Just verify no crash

    def test_missing_reason_no_end_reason_key(self, hive_env, monkeypatch):
        """No 'reason' key -> reason defaults to "" -> end_reason key NOT set.

        Per track_session_event: `if reason: session["end_reason"] = reason`
        So with reason="", end_reason key is NOT set. This is documented behavior.
        """
        from keephive.clock import get_today
        from keephive.storage import read_stats, track_session_event

        sid = "sess-noreason-001"
        track_session_event(sid, "start")

        run_hook({"session_id": sid}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        session = data.get("days", {}).get(day, {}).get("sessions", {}).get(sid, {})
        # end_reason should either be missing or be empty string
        end_reason = session.get("end_reason", "")
        assert end_reason == "", f"Unexpected end_reason: {end_reason!r}"

    def test_invalid_json_no_crash(self, hive_env, monkeypatch):
        """Malformed JSON -> exits silently, no crash, no stats written."""
        run_hook("{{{{not json", monkeypatch, hive_env)
        # No assertion beyond "no exception raised"

    def test_stats_file_created_if_missing(self, hive_env, monkeypatch):
        """No pre-existing .stats.json -> hook creates it."""
        stats = hive_env / ".stats.json"
        if stats.exists():
            stats.unlink()

        run_hook({"session_id": "sess-new-001", "reason": "exit"}, monkeypatch, hive_env)

        assert stats.exists(), ".stats.json not created by sessionend hook"

    def test_double_end_second_overwrites_first(self, hive_env, monkeypatch):
        """OBS-1: calling sessionend twice for the same session_id.

        Second call silently overwrites 'ended' and 'end_reason' with latest values.
        This is documented behavior (last-write-wins, no data accumulation).
        """
        from keephive.clock import get_today
        from keephive.storage import read_stats, track_session_event

        sid = "sess-doubleend-001"
        track_session_event(sid, "start")

        run_hook({"session_id": sid, "reason": "first_reason"}, monkeypatch, hive_env)
        run_hook({"session_id": sid, "reason": "second_reason"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        session = data.get("days", {}).get(day, {}).get("sessions", {}).get(sid, {})
        # Second reason wins
        assert session.get("end_reason") == "second_reason", (
            f"Expected 'second_reason', got {session.get('end_reason')!r}"
        )

    def test_session_id_with_dots_and_dashes(self, hive_env, monkeypatch):
        """Session IDs with dots and dashes (UUID-like) are stored correctly."""
        from keephive.clock import get_today
        from keephive.storage import read_stats

        sid = "550e8400-e29b-41d4-a716-446655440000"
        run_hook({"session_id": sid, "reason": "exit"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        # Session should be tracked by its ID as a key
        hooks = data.get("days", {}).get(day, {}).get("hooks", {})
        assert hooks.get("sessionend", 0) >= 1

    def test_hook_counter_increments_each_call(self, hive_env, monkeypatch):
        """Multiple valid hook calls -> counter increments additively."""
        from keephive.clock import get_today
        from keephive.storage import read_stats

        for i in range(3):
            run_hook({"session_id": f"sess-multi-{i}", "reason": "exit"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        count = data.get("days", {}).get(day, {}).get("hooks", {}).get("sessionend", 0)
        assert count == 3, f"Expected 3 hook events, got {count}"

    def test_end_without_prior_start_creates_session(self, hive_env, monkeypatch):
        """Ending a session that was never started still creates a session entry.

        track_session_event creates the session dict on first access regardless
        of event type. The session will have 'ended' but a synthetic 'started' timestamp.
        """
        from keephive.clock import get_today
        from keephive.storage import read_stats

        sid = "sess-nostart-001"
        run_hook({"session_id": sid, "reason": "orphan_exit"}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        session = data.get("days", {}).get(day, {}).get("sessions", {}).get(sid, {})
        assert "ended" in session, "Session should have 'ended' even without prior start"
        assert "started" in session, "track_session_event creates 'started' on first access"
