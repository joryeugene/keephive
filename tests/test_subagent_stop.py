"""Tests for SubagentStop hook (hooks/subagent_stop.py)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from keephive.hooks.subagent_stop import hook_subagent_stop


class TestSubagentStopLogging:
    """Subagent completions are logged as SUBAGENT-DONE breadcrumbs."""

    def _call_hook(self, input_data: dict) -> None:
        with patch("sys.stdin", StringIO(json.dumps(input_data))):
            hook_subagent_stop([])

    def test_task_subject_logged(self, hive_env):
        """When task_subject is present it appears in the daily log."""
        from keephive.storage import daily_file

        self._call_hook({"task_subject": "Implement auth endpoint"})

        content = daily_file().read_text()
        assert "SUBAGENT-DONE: Implement auth endpoint" in content

    def test_description_fallback(self, hive_env):
        """Falls back to 'description' when 'task_subject' is absent."""
        from keephive.storage import daily_file

        self._call_hook({"description": "Write unit tests for storage module"})

        content = daily_file().read_text()
        assert "SUBAGENT-DONE: Write unit tests for storage module" in content

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
