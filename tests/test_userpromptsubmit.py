"""Tests for UserPromptSubmit hook (hooks/userpromptsubmit.py)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from keephive.hooks.userpromptsubmit import hook_userpromptsubmit


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
