"""Tests for UserPromptSubmit hook (hooks/userpromptsubmit.py)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from keephive.hooks.userpromptsubmit import _format_ui_context, hook_userpromptsubmit


class TestUserPromptSubmit:
    def _call_hook(self, input_data):
        """Call hook with mocked stdin."""
        if isinstance(input_data, dict):
            stdin_text = json.dumps(input_data)
        else:
            stdin_text = input_data
        with patch("sys.stdin", StringIO(stdin_text)):
            hook_userpromptsubmit([])

    def test_silent_on_bad_json(self, hive_env, capsys):
        """Bad JSON input produces no output, no crash."""
        self._call_hook("not json at all")
        out = capsys.readouterr().out
        assert out == ""

    def test_silent_on_missing_session_id(self, hive_env, capsys):
        """Missing session_id produces no output."""
        self._call_hook({"prompt": "hello"})
        out = capsys.readouterr().out
        assert out == ""

    def test_fires_nudge_at_interval(self, hive_env, capsys):
        """After HIVE_NUDGE_INTERVAL calls, a nudge fires."""
        session_id = "test-session-ups"
        payload = {"session_id": session_id, "prompt": "test"}

        # Reset counter
        counter_file = hive_env / f".prompt-counter-{session_id}"
        if counter_file.exists():
            counter_file.unlink()

        # Run hook multiple times until nudge fires
        got_output = False
        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "4"}):
            for _ in range(12):
                self._call_hook(payload)
                out = capsys.readouterr().out
                if out.strip():
                    got_output = True
                    break

        assert got_output, "Nudge never fired after 12 calls"


class TestUIQueueInjection:
    """Tests for .ui-queue file injection into hook output."""

    def _call_hook(self, input_data):
        if isinstance(input_data, dict):
            stdin_text = json.dumps(input_data)
        else:
            stdin_text = input_data
        with patch("sys.stdin", StringIO(stdin_text)):
            hook_userpromptsubmit([])

    def test_ui_queue_injected_as_additional_context(self, hive_env, capsys):
        """When .ui-queue exists with valid JSON, it is injected as additionalContext."""
        queue_data = {
            "page": "http://localhost:8080/dashboard",
            "selector": "div.stats-panel",
            "html": "<div class='stats-panel'>42 facts</div>",
            "styles": "color: red;",
            "note": "Panel text is too small\n\nNote: Increase font size to 16px",
        }
        queue_file = hive_env / ".ui-queue"
        queue_file.write_text(json.dumps(queue_data))

        self._call_hook({"session_id": "sess-ui-1", "prompt": "fix the dashboard"})

        out = capsys.readouterr().out
        assert out.strip(), "Expected output from UI queue injection"
        parsed = json.loads(out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "UI Feedback" in ctx
        assert "div.stats-panel" in ctx
        assert "Increase font size to 16px" in ctx

    def test_ui_queue_deleted_after_injection(self, hive_env, capsys):
        """After injecting the queue, the .ui-queue file is removed."""
        queue_data = {"page": "/", "selector": "body", "html": "", "note": ""}
        queue_file = hive_env / ".ui-queue"
        queue_file.write_text(json.dumps(queue_data))

        self._call_hook({"session_id": "sess-ui-2", "prompt": "hello"})

        assert not queue_file.exists(), ".ui-queue should be deleted after consumption"

    def test_no_injection_when_queue_missing(self, hive_env, capsys):
        """When no .ui-queue file exists, no additionalContext is injected (may get nudge instead)."""
        queue_file = hive_env / ".ui-queue"
        assert not queue_file.exists()

        # Use interval=999 to suppress nudge so output is only from UI queue
        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "999"}):
            self._call_hook({"session_id": "sess-ui-3", "prompt": "hello"})

        out = capsys.readouterr().out
        assert out == "", "No output expected when queue is missing and nudge suppressed"

    def test_ui_queue_skips_nudge(self, hive_env, capsys):
        """When UI queue is consumed, nudge is skipped for that invocation."""
        queue_data = {
            "page": "/test",
            "selector": "#btn",
            "html": "<button>click</button>",
            "note": "",
        }
        queue_file = hive_env / ".ui-queue"
        queue_file.write_text(json.dumps(queue_data))

        # Set interval=1 so nudge would fire on every call
        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "1"}):
            self._call_hook({"session_id": "sess-ui-4", "prompt": "hello"})

        out = capsys.readouterr().out
        parsed = json.loads(out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        # Should contain UI feedback, not a nudge
        assert "UI Feedback" in ctx
        assert "hive_remember" not in ctx

    def test_project_scoped_ui_queue(self, hive_env, capsys):
        """When cwd is set, project-scoped .ui-queue-{project} is preferred."""
        project_queue = hive_env / ".ui-queue-myapp"
        project_queue.write_text(
            json.dumps(
                {"page": "/myapp", "selector": ".header", "html": "<h1>MyApp</h1>", "note": ""}
            )
        )
        # Also create global queue to verify it is NOT consumed
        global_queue = hive_env / ".ui-queue"
        global_queue.write_text(
            json.dumps({"page": "/global", "selector": "body", "html": "", "note": ""})
        )

        self._call_hook(
            {"session_id": "sess-ui-5", "prompt": "fix header", "cwd": "/home/dev/myapp"}
        )

        out = capsys.readouterr().out
        parsed = json.loads(out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "/myapp" in ctx
        # Project queue consumed, global queue untouched
        assert not project_queue.exists()
        assert global_queue.exists()

    def test_global_queue_fallback_when_project_queue_missing(self, hive_env, capsys):
        """When project-scoped queue doesn't exist, falls back to global .ui-queue."""
        global_queue = hive_env / ".ui-queue"
        global_queue.write_text(
            json.dumps(
                {"page": "/fallback", "selector": "#root", "html": "<div>hi</div>", "note": ""}
            )
        )

        self._call_hook(
            {"session_id": "sess-ui-6", "prompt": "test", "cwd": "/home/dev/noprojectqueue"}
        )

        out = capsys.readouterr().out
        parsed = json.loads(out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "/fallback" in ctx
        assert not global_queue.exists()


class TestFormatUIContext:
    """Tests for the _format_ui_context helper."""

    def test_basic_formatting(self):
        """All fields render into the context block."""
        data = {
            "page": "http://localhost:3000/settings",
            "selector": "input#email",
            "html": "<input id='email' value='test@test.com' />",
            "styles": "border: 1px solid red;",
            "note": "Validation error\n\nNote: Border should be green on success",
        }
        result = _format_ui_context(data)
        parsed = json.loads(result)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "[UI Feedback" in ctx
        assert "input#email" in ctx
        assert "border: 1px solid red;" in ctx
        assert "Border should be green on success" in ctx
        assert "[/UI Feedback]" in ctx

    def test_note_extraction_strips_prefix(self):
        """The 'Note: ' prefix after double newline is stripped, leaving only the user note."""
        data = {
            "page": "/",
            "selector": "body",
            "html": "",
            "note": "Element: body\nPage: /\n\nNote: The actual user feedback here",
        }
        result = _format_ui_context(data)
        parsed = json.loads(result)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "The actual user feedback here" in ctx
        # The preamble before "Note: " should not appear as the note value
        assert "Note: Element: body" not in ctx

    def test_html_truncated_at_400_chars(self):
        """HTML snippets longer than 400 chars are truncated."""
        long_html = "<div>" + "x" * 500 + "</div>"
        data = {"page": "/", "selector": "div", "html": long_html, "note": ""}
        result = _format_ui_context(data)
        parsed = json.loads(result)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        # The html in context should be at most 400 chars of the original
        # (the function slices data["html"][:400])
        assert "x" * 400 not in ctx or len(long_html) > 400


class TestCounterBehavior:
    """Tests for counter increment, reset, and file persistence."""

    def _call_hook(self, input_data):
        if isinstance(input_data, dict):
            stdin_text = json.dumps(input_data)
        else:
            stdin_text = input_data
        with patch("sys.stdin", StringIO(stdin_text)):
            hook_userpromptsubmit([])

    def test_counter_increments_each_call(self, hive_env):
        """Counter value increases with each hook invocation."""
        from keephive.nudge import read_counter

        session_id = "sess-counter-inc"
        payload = {"session_id": session_id, "prompt": "test"}

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "999"}):
            self._call_hook(payload)
            count1, _ = read_counter("prompt")
            self._call_hook(payload)
            count2, _ = read_counter("prompt")
            self._call_hook(payload)
            count3, _ = read_counter("prompt")

        assert count2 == count1 + 1
        assert count3 == count2 + 1

    def test_counter_resets_on_new_session(self, hive_env):
        """When session_id changes, the counter resets to 1."""
        from keephive.nudge import read_counter

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "999"}):
            # Build up counter with session A
            for _ in range(5):
                self._call_hook({"session_id": "sess-A", "prompt": "test"})
            count_a, sid_a = read_counter("prompt")
            assert count_a == 5
            assert sid_a == "sess-A"

            # Switch to session B: counter resets
            self._call_hook({"session_id": "sess-B", "prompt": "test"})
            count_b, sid_b = read_counter("prompt")
            assert count_b == 1, f"Expected counter reset to 1, got {count_b}"
            assert sid_b == "sess-B"

    def test_counter_file_persists_on_disk(self, hive_env):
        """Counter state is written to the expected file in hive_dir."""
        counter_file = hive_env / ".prompt-counter"

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "999"}):
            self._call_hook({"session_id": "sess-persist", "prompt": "test"})

        assert counter_file.exists(), "Counter file should exist after hook call"
        data = json.loads(counter_file.read_text())
        assert data["count"] == 1
        assert data["session_id"] == "sess-persist"

    def test_nudge_fires_exactly_at_interval(self, hive_env, capsys):
        """Nudge fires at count == interval, not before, not one after."""
        session_id = "sess-exact"
        payload = {"session_id": session_id, "prompt": "test"}

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "3"}):
            # Call 1: no nudge
            self._call_hook(payload)
            out1 = capsys.readouterr().out
            assert out1 == "", f"No nudge expected at call 1, got: {out1!r}"

            # Call 2: no nudge
            self._call_hook(payload)
            out2 = capsys.readouterr().out
            assert out2 == "", f"No nudge expected at call 2, got: {out2!r}"

            # Call 3: nudge fires (3 % 3 == 0)
            self._call_hook(payload)
            out3 = capsys.readouterr().out
            assert out3.strip(), "Nudge should fire at call 3 (interval=3)"
            parsed = json.loads(out3)
            assert "additionalContext" in parsed["hookSpecificOutput"]

            # Call 4: no nudge
            self._call_hook(payload)
            out4 = capsys.readouterr().out
            assert out4 == "", f"No nudge expected at call 4, got: {out4!r}"


class TestNudgeContent:
    """Tests for nudge message content and status-awareness."""

    def _call_hook(self, input_data):
        if isinstance(input_data, dict):
            stdin_text = json.dumps(input_data)
        else:
            stdin_text = input_data
        with patch("sys.stdin", StringIO(stdin_text)):
            hook_userpromptsubmit([])

    def test_nudge_output_has_hook_event_name(self, hive_env, capsys):
        """Nudge output includes hookEventName=UserPromptSubmit."""
        session_id = "sess-event-name"
        payload = {"session_id": session_id, "prompt": "test"}

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "1"}):
            self._call_hook(payload)

        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_nudge_contains_actionable_content(self, hive_env, capsys):
        """Nudge text contains either a hive tool mention or a status-aware message."""
        session_id = "sess-tool-mention"
        payload = {"session_id": session_id, "prompt": "test"}

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "1"}):
            self._call_hook(payload)

        out = capsys.readouterr().out
        parsed = json.loads(out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        lower = ctx.lower()
        # Nudge is either a tool-mentioning nudge or a status-aware message
        has_tool_mention = "hive_r" in lower or "hive r" in lower or "hive v" in lower
        has_status = (
            "stale" in lower
            or "overdue" in lower
            or "verification" in lower
            or "unverified" in lower
        )
        assert has_tool_mention or has_status, f"Expected actionable nudge content, got: {ctx!r}"


class TestUsageTracking:
    """Tests for track_event and track_session_event calls."""

    def _call_hook(self, input_data):
        if isinstance(input_data, dict):
            stdin_text = json.dumps(input_data)
        else:
            stdin_text = input_data
        with patch("sys.stdin", StringIO(stdin_text)):
            hook_userpromptsubmit([])

    def test_tracks_hook_event(self, hive_env):
        """Each invocation increments the hooks/userpromptsubmit stat."""
        from keephive.storage import read_stats

        self._call_hook({"session_id": "sess-track-1", "prompt": "hello"})
        stats = read_stats()
        from keephive.clock import get_today

        day_key = get_today().isoformat()
        day_data = stats["days"].get(day_key, {})
        hooks = day_data.get("hooks", {})
        assert hooks.get("userpromptsubmit", 0) >= 1

    def test_no_session_prompt_tracking(self, hive_env):
        """Hook does NOT write per-session prompt counts to stats.

        Session prompt counting was intentionally removed because Claude Code
        session-meta provides accurate user_message_count. Hook invocations
        overcount (~71x) due to sub-agent spawns and tool continuations.
        """
        from keephive.clock import get_today
        from keephive.storage import read_stats

        session_id = "sess-track-prompt"
        self._call_hook({"session_id": session_id, "prompt": "hello"})
        self._call_hook({"session_id": session_id, "prompt": "world"})

        stats = read_stats()
        day_key = get_today().isoformat()
        sessions = stats["days"].get(day_key, {}).get("sessions", {})
        assert session_id not in sessions


# ---- Edge-case tests: counter behavior ----


class TestCounterEdgeCases:
    """Edge cases for counter file corruption and unusual inputs."""

    def _call_hook(self, input_data):
        if isinstance(input_data, dict):
            stdin_text = json.dumps(input_data)
        else:
            stdin_text = input_data
        with patch("sys.stdin", StringIO(stdin_text)):
            hook_userpromptsubmit([])

    def test_corrupted_counter_file(self, hive_env, capsys):
        """Corrupted counter file is handled gracefully (reset to 0)."""
        counter_file = hive_env / ".prompt-counter"
        counter_file.write_text("not valid json at all{{{")

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "999"}):
            self._call_hook({"session_id": "sess-corrupt-counter", "prompt": "test"})

        # Should not crash, counter resets
        data = json.loads(counter_file.read_text())
        assert data["count"] == 1

    def test_nudge_interval_zero_treated_as_default(self, hive_env, capsys):
        """HIVE_NUDGE_INTERVAL=0 should not cause division by zero."""
        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "0"}):
            # Should not raise ZeroDivisionError
            self._call_hook({"session_id": "sess-zero-interval", "prompt": "test"})

    def test_empty_session_id_produces_no_output(self, hive_env, capsys):
        """Empty session_id is handled the same as missing."""
        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "1"}):
            self._call_hook({"session_id": "", "prompt": "test"})

        out = capsys.readouterr().out
        assert out == ""


# ---- Edge-case tests: nudge content validity ----


class TestNudgeContentEdgeCases:
    """Test that nudge output is always valid JSON."""

    def _call_hook(self, input_data):
        if isinstance(input_data, dict):
            stdin_text = json.dumps(input_data)
        else:
            stdin_text = input_data
        with patch("sys.stdin", StringIO(stdin_text)):
            hook_userpromptsubmit([])

    def test_nudge_output_is_valid_json(self, hive_env, capsys):
        """Every nudge output should parse as valid JSON with expected structure."""
        session_id = "sess-json-valid"
        payload = {"session_id": session_id, "prompt": "test"}

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "1"}):
            self._call_hook(payload)

        out = capsys.readouterr().out.strip()
        if out:
            parsed = json.loads(out)
            assert "hookSpecificOutput" in parsed
            assert "hookEventName" in parsed["hookSpecificOutput"]
            assert "additionalContext" in parsed["hookSpecificOutput"]

    def test_nudge_with_stale_facts_and_overdue_tasks(self, hive_env, capsys, monkeypatch):
        """Nudge content adapts when there are stale facts and overdue tasks."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import memory_file, recurring_file

        # Create stale fact
        memory_file().write_text("# Memory\n\n- FACT: ancient fact [verified:2020-01-01]\n")
        # Create overdue recurring
        rf = recurring_file()
        rf.write_text(
            "# Recurring Tasks\n\n- [daily] Check logs\n\n"
            "## Last Completed\n- Check logs: 2026-01-01\n"
        )

        session_id = "sess-status-nudge"
        payload = {"session_id": session_id, "prompt": "test"}

        with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "1"}):
            self._call_hook(payload)

        out = capsys.readouterr().out.strip()
        assert out, "Expected nudge output with stale data"
        parsed = json.loads(out)
        assert "hookSpecificOutput" in parsed
