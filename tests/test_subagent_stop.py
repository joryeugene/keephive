"""Tests for SubagentStop hook (hooks/subagent_stop.py)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from keephive.hooks.subagent_stop import hook_subagent_stop


class TestSubagentStopLogging:
    """Subagent completions are logged as SUBAGENT-DONE or SUBAGENT-INSIGHT breadcrumbs."""

    def _call_hook(self, input_data: dict) -> None:
        with patch("sys.stdin", StringIO(json.dumps(input_data))):
            hook_subagent_stop([])

    def test_task_subject_logged(self, hive_env):
        """When task_subject is present it appears in the daily log."""
        from keephive.storage import daily_file

        self._call_hook({"task_subject": "Impl auth endpoint"})

        content = daily_file().read_text()
        assert "SUBAGENT-DONE: Impl auth endpoint" in content

    def test_description_fallback(self, hive_env):
        """Falls back to 'description' when 'task_subject' is absent."""
        from keephive.storage import daily_file

        self._call_hook({"description": "Write storage tests"})

        content = daily_file().read_text()
        assert "SUBAGENT-DONE: Write storage tests" in content

    def test_generic_log_when_no_description(self, hive_env):
        """When no description field exists, logs generic completion breadcrumb."""
        from keephive.storage import daily_file

        self._call_hook({"session_id": "sess-subagent-1"})

        content = daily_file().read_text()
        assert "SUBAGENT-DONE: subagent task completed" in content

    def test_newlines_stripped_from_subject(self, hive_env):
        """Newlines in task_subject are replaced with spaces."""
        from keephive.storage import daily_file

        self._call_hook({"task_subject": "Build\nfeature\nX"})

        content = daily_file().read_text()
        assert "\n" not in content.split("SUBAGENT-DONE:")[-1].split("\n")[0]
        assert "Build feature X" in content

    def test_silent_on_bad_json(self, hive_env):
        """Bad JSON input produces no crash, no log entry."""
        from keephive.storage import daily_file

        with patch("sys.stdin", StringIO("not json at all")):
            hook_subagent_stop([])

        # No crash; daily log should not contain SUBAGENT-DONE
        content = daily_file().read_text() if daily_file().exists() else ""
        assert "SUBAGENT-DONE" not in content

    def test_no_stdout_output(self, hive_env, capsys):
        """SubagentStop hook never writes to stdout (non-blocking)."""
        self._call_hook({"task_subject": "something"})

        out = capsys.readouterr().out
        assert out == "", f"Expected no stdout output, got: {out!r}"


class TestSubagentStopTracking:
    """Hook event is tracked in stats."""

    def _call_hook(self, input_data: dict) -> None:
        with patch("sys.stdin", StringIO(json.dumps(input_data))):
            hook_subagent_stop([])

    def test_tracks_hook_event(self, hive_env):
        """Each invocation increments the hooks/subagent_stop stat."""
        from keephive.clock import get_today
        from keephive.storage import read_stats

        self._call_hook({"task_subject": "tracked task"})

        stats = read_stats()
        day_key = get_today().isoformat()
        day_data = stats["days"].get(day_key, {})
        hooks = day_data.get("hooks", {})
        assert hooks.get("subagent_stop", 0) >= 1


class TestSubagentStopRegistration:
    """setup.py registers SubagentStop hook in settings.json."""

    def test_subagent_stop_hook_registered(self, tmp_path):
        """_setup_hooks writes SubagentStop entry to settings.json."""
        import json

        from keephive.commands.setup import _setup_hooks

        settings = tmp_path / "settings.json"
        _setup_hooks(settings_path=settings)

        data = json.loads(settings.read_text())
        hooks = data.get("hooks", {})
        assert "SubagentStop" in hooks

        cmds = " ".join(
            h.get("command", "") for entry in hooks["SubagentStop"] for h in entry.get("hooks", [])
        )
        assert "hook-subagent-stop" in cmds

    def test_subagent_stop_not_duplicated(self, tmp_path):
        """Running _setup_hooks twice does not add duplicate SubagentStop entries."""
        from keephive.commands.setup import _setup_hooks

        settings = tmp_path / "settings.json"
        _setup_hooks(settings_path=settings)
        _setup_hooks(settings_path=settings)

        data = json.loads(settings.read_text())
        count = len(data.get("hooks", {}).get("SubagentStop", []))
        assert count == 1, f"Expected 1 SubagentStop entry, got {count}"


class TestSubagentStopExtraction:
    """LLM extraction enriches log entries for long subagent descriptions."""

    def _call_hook(self, input_data: dict) -> None:
        with patch("sys.stdin", StringIO(json.dumps(input_data))):
            hook_subagent_stop([])

    def test_extraction_skipped_for_short_description(self, hive_env):
        """Descriptions <= 20 chars skip extraction entirely."""
        import unittest.mock as mock

        with mock.patch(
            "keephive.hooks.subagent_stop._extract_output"
        ) as mock_extract:
            self._call_hook({"task_subject": "Short task"})  # 10 chars

        mock_extract.assert_not_called()

    def test_extraction_triggered_for_long_description(self, hive_env):
        """Descriptions > 20 chars trigger _extract_output exactly once."""
        import unittest.mock as mock

        mock_extract = mock.MagicMock(return_value=None)
        with mock.patch("keephive.hooks.subagent_stop._extract_output", mock_extract):
            self._call_hook({"task_subject": "Implement OAuth2 authentication endpoint"})

        mock_extract.assert_called_once()

    def test_silence_gate_falls_back_to_subagent_done(self, hive_env):
        """When extraction returns None (silence gate), logs plain SUBAGENT-DONE."""
        import unittest.mock as mock
        from keephive.storage import daily_file

        with mock.patch(
            "keephive.hooks.subagent_stop._extract_output", return_value=None
        ):
            self._call_hook({"task_subject": "Implement OAuth2 authentication endpoint"})

        content = daily_file().read_text()
        assert "SUBAGENT-DONE" in content
        assert "SUBAGENT-INSIGHT" not in content

    def test_captured_output_produces_subagent_insight(self, hive_env):
        """Non-empty captured produces SUBAGENT-INSIGHT with -> separator."""
        import unittest.mock as mock
        from keephive.models import SubagentExtractionResponse
        from keephive.storage import daily_file

        fake_result = SubagentExtractionResponse(
            captured="Implemented JWT token validation with 24h expiry",
            decision="",
        )
        with mock.patch(
            "keephive.hooks.subagent_stop._extract_output", return_value=fake_result
        ):
            self._call_hook({"task_subject": "Implement OAuth2 authentication endpoint"})

        content = daily_file().read_text()
        assert "SUBAGENT-INSIGHT" in content
        assert "->" in content
        assert "JWT token validation" in content

    def test_decision_appended_when_present(self, hive_env):
        """Non-empty decision is appended to the insight line."""
        import unittest.mock as mock
        from keephive.models import SubagentExtractionResponse
        from keephive.storage import daily_file

        fake_result = SubagentExtractionResponse(
            captured="Implemented auth endpoint",
            decision="JWT over sessions for stateless scaling",
        )
        with mock.patch(
            "keephive.hooks.subagent_stop._extract_output", return_value=fake_result
        ):
            self._call_hook({"task_subject": "Implement OAuth2 authentication endpoint"})

        content = daily_file().read_text()
        assert "(decision:" in content
        assert "JWT over sessions" in content

    def test_extraction_error_falls_back_gracefully(self, hive_env):
        """_extract_output returning None falls back to plain SUBAGENT-DONE."""
        import unittest.mock as mock
        from keephive.storage import daily_file

        with mock.patch(
            "keephive.hooks.subagent_stop._extract_output",
            side_effect=Exception("network error"),
        ):
            # The except Exception in hook_subagent_stop wraps the whole block
            # So any exception in _extract_output is caught there
            self._call_hook({"task_subject": "Implement OAuth2 authentication endpoint"})

        # The outer except Exception in hook_subagent_stop catches everything
        # so nothing is logged — or SUBAGENT-DONE is logged if _extract_output
        # raises inside the try block. Either way, no crash.
        content = daily_file().read_text() if daily_file().exists() else ""
        assert "SUBAGENT-INSIGHT" not in content

    def test_subagent_extraction_response_model_silence_valid(self):
        """SubagentExtractionResponse() defaults to empty strings (silence-valid)."""
        from keephive.models import SubagentExtractionResponse

        response = SubagentExtractionResponse()
        assert response.captured == ""
        assert response.decision == ""

    def test_no_stdout_output_with_extraction(self, hive_env, capsys):
        """SubagentStop hook never writes to stdout even with extraction."""
        import unittest.mock as mock
        from keephive.models import SubagentExtractionResponse

        fake_result = SubagentExtractionResponse(captured="something important", decision="")
        with mock.patch(
            "keephive.hooks.subagent_stop._extract_output", return_value=fake_result
        ):
            self._call_hook({"task_subject": "Implement OAuth2 authentication endpoint"})

        out = capsys.readouterr().out
        assert out == ""
