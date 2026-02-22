"""Tests for the Stop hook handler (hooks/stop.py)."""

from __future__ import annotations

import io
import json
from unittest.mock import patch


def run_hook(input_data: dict | str, monkeypatch, hive_env) -> None:
    """Call hook_stop with mocked stdin."""
    if isinstance(input_data, dict):
        raw = json.dumps(input_data)
    else:
        raw = input_data
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    from keephive.hooks.stop import hook_stop

    hook_stop([])


class TestStopHookSilence:
    """Stop hook should never crash and produce no output on bad input."""

    def test_silent_on_bad_json(self, hive_env, monkeypatch, capsys):
        """Malformed JSON -> no output, no crash."""
        run_hook("{{{{not json", monkeypatch, hive_env)
        out = capsys.readouterr().out
        assert out == ""

    def test_silent_on_missing_session_id(self, hive_env, monkeypatch, capsys):
        """Missing session_id key -> no output."""
        run_hook({}, monkeypatch, hive_env)
        out = capsys.readouterr().out
        assert out == ""

    def test_silent_on_empty_session_id(self, hive_env, monkeypatch, capsys):
        """Empty session_id string -> treated as missing, no output."""
        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "1"}):
            run_hook({"session_id": ""}, monkeypatch, hive_env)
        out = capsys.readouterr().out
        assert out == ""

    def test_no_crash_on_corrupt_stats(self, hive_env, monkeypatch, capsys):
        """Corrupt .stats.json does not propagate an exception."""
        stats_file = hive_env / ".stats.json"
        stats_file.write_text("{{{broken json")
        # Should not raise
        run_hook({"session_id": "sess-corrupt"}, monkeypatch, hive_env)


class TestStopHookCounter:
    """Turn counter increments correctly via stop hook."""

    def test_counter_increments_each_call(self, hive_env, monkeypatch):
        """Repeated hook calls increment the stop counter."""
        from keephive.nudge import read_counter

        session_id = "sess-stop-counter"
        payload = {"session_id": session_id}

        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "999"}):
            run_hook(payload, monkeypatch, hive_env)
            count1, _ = read_counter("stop")
            run_hook(payload, monkeypatch, hive_env)
            count2, _ = read_counter("stop")
            run_hook(payload, monkeypatch, hive_env)
            count3, _ = read_counter("stop")

        assert count2 == count1 + 1
        assert count3 == count2 + 1

    def test_counter_resets_on_new_session(self, hive_env, monkeypatch):
        """Counter resets when session_id changes."""
        from keephive.nudge import read_counter

        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "999"}):
            for _ in range(4):
                run_hook({"session_id": "sess-stop-A"}, monkeypatch, hive_env)
            count_a, sid_a = read_counter("stop")
            assert count_a == 4
            assert sid_a == "sess-stop-A"

            run_hook({"session_id": "sess-stop-B"}, monkeypatch, hive_env)
            count_b, sid_b = read_counter("stop")
            assert count_b == 1
            assert sid_b == "sess-stop-B"

    def test_turn_count_written_to_stats(self, hive_env, monkeypatch):
        """hook_stop increments session turns in .stats.json."""
        from keephive.clock import get_today
        from keephive.storage import read_stats

        session_id = "sess-stop-turns"
        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "999"}):
            run_hook({"session_id": session_id}, monkeypatch, hive_env)
            run_hook({"session_id": session_id}, monkeypatch, hive_env)

        day = get_today().isoformat()
        data = read_stats()
        session = data["days"].get(day, {}).get("sessions", {}).get(session_id, {})
        assert session.get("turns", 0) == 2


class TestStopHookNudge:
    """Nudge fires at the correct interval with correct hookEventName."""

    def test_nudge_fires_at_interval(self, hive_env, monkeypatch, capsys):
        """After HIVE_STOP_NUDGE_INTERVAL calls, a nudge fires."""
        session_id = "sess-stop-nudge"
        payload = {"session_id": session_id}

        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "3"}):
            run_hook(payload, monkeypatch, hive_env)
            out1 = capsys.readouterr().out
            assert out1 == "", "No nudge before interval"

            run_hook(payload, monkeypatch, hive_env)
            out2 = capsys.readouterr().out
            assert out2 == "", "No nudge before interval"

            run_hook(payload, monkeypatch, hive_env)
            out3 = capsys.readouterr().out
            assert out3.strip(), "Nudge should fire at interval=3"

    def test_nudge_hookEventName_is_Stop(self, hive_env, monkeypatch, capsys):
        """Nudge output carries hookEventName='Stop', not 'UserPromptSubmit'."""
        session_id = "sess-stop-event"
        payload = {"session_id": session_id}

        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "1"}):
            run_hook(payload, monkeypatch, hive_env)

        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "Stop"

    def test_nudge_output_is_valid_json(self, hive_env, monkeypatch, capsys):
        """Nudge output is valid JSON with expected structure."""
        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "1"}):
            run_hook({"session_id": "sess-stop-json"}, monkeypatch, hive_env)

        out = capsys.readouterr().out.strip()
        assert out, "Expected nudge output at interval=1"
        parsed = json.loads(out)
        assert "hookSpecificOutput" in parsed
        assert "hookEventName" in parsed["hookSpecificOutput"]
        assert "additionalContext" in parsed["hookSpecificOutput"]


class TestStopHookUIQueue:
    """UI queue injection via stop hook."""

    def test_ui_queue_hookEventName_is_Stop(self, hive_env, monkeypatch, capsys):
        """This is the regression test for the event_name bug.

        drain_ui_queue() used to hardcode 'UserPromptSubmit' regardless of caller.
        Stop hook must emit hookEventName='Stop' when injecting UI feedback.
        """
        queue_file = hive_env / ".ui-queue"
        queue_file.write_text(
            json.dumps(
                {"page": "/stats", "selector": "div.pipeline", "html": "<div/>", "note": "fix me"}
            )
        )

        run_hook({"session_id": "sess-stop-ui", "cwd": ""}, monkeypatch, hive_env)

        out = capsys.readouterr().out
        assert out.strip(), "Expected output when UI queue present"
        parsed = json.loads(out)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "Stop", (
            "Stop hook must emit hookEventName='Stop', not 'UserPromptSubmit' — regression for drain_ui_queue bug"
        )

    def test_ui_queue_skips_nudge(self, hive_env, monkeypatch, capsys):
        """When UI queue is consumed, nudge is skipped for that invocation."""
        queue_file = hive_env / ".ui-queue"
        queue_file.write_text(
            json.dumps({"page": "/", "selector": "body", "html": "<body/>", "note": ""})
        )

        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "1"}):
            run_hook({"session_id": "sess-stop-skip", "cwd": ""}, monkeypatch, hive_env)

        out = capsys.readouterr().out
        parsed = json.loads(out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "UI Feedback" in ctx
        # Must not contain nudge text (hive_remember or similar)
        assert "hive_r" not in ctx.lower()

    def test_ui_queue_consumed_after_drain(self, hive_env, monkeypatch, capsys):
        """UI queue file is deleted after stop hook processes it."""
        queue_file = hive_env / ".ui-queue"
        queue_file.write_text(json.dumps({"page": "/", "selector": "body", "html": "", "note": ""}))

        run_hook({"session_id": "sess-stop-consume", "cwd": ""}, monkeypatch, hive_env)
        capsys.readouterr()

        assert not queue_file.exists(), ".ui-queue should be deleted after stop hook consumes it"

    def test_no_output_when_queue_absent_and_nudge_suppressed(self, hive_env, monkeypatch, capsys):
        """No output when queue missing and nudge interval is high."""
        with patch.dict("os.environ", {"HIVE_STOP_NUDGE_INTERVAL": "999"}):
            run_hook({"session_id": "sess-stop-quiet", "cwd": ""}, monkeypatch, hive_env)

        out = capsys.readouterr().out
        assert out == ""
