"""Tests for hive off / hive on (power toggle)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from keephive.storage import disabled_file, is_disabled, set_disabled


class TestStorageHelpers:
    """Flag file CRUD in storage.py."""

    def test_disabled_file_path(self, hive_env):
        assert disabled_file().name == ".hive-disabled"

    def test_default_not_disabled(self, hive_env):
        assert not is_disabled()

    def test_set_disabled_creates_flag(self, hive_env):
        set_disabled(True)
        assert disabled_file().exists()
        assert is_disabled()

    def test_set_disabled_false_removes_flag(self, hive_env):
        set_disabled(True)
        set_disabled(False)
        assert not disabled_file().exists()
        assert not is_disabled()

    def test_roundtrip(self, hive_env):
        """off -> on is a perfect roundtrip with no residue."""
        assert not is_disabled()
        set_disabled(True)
        assert is_disabled()
        set_disabled(False)
        assert not is_disabled()

    def test_idempotent_off(self, hive_env):
        set_disabled(True)
        set_disabled(True)
        assert is_disabled()

    def test_idempotent_on(self, hive_env):
        set_disabled(False)
        assert not is_disabled()


class TestCmdOff:
    """hive off command."""

    def test_off_creates_flag(self, hive_env):
        from keephive.commands.power import cmd_off

        cmd_off([])
        assert is_disabled()

    def test_off_already_off(self, hive_env, capsys):
        set_disabled(True)
        from keephive.commands.power import cmd_off

        cmd_off([])
        out = capsys.readouterr().out
        assert "already off" in out

    def test_off_warns_active_loops(self, hive_env, capsys):
        """Active loops block hive off without --force."""
        from keephive.storage import hive_dir

        loop_file = hive_dir() / ".loop-test123.json"
        loop_file.write_text(json.dumps({"task": "test"}))

        from keephive.commands.power import cmd_off

        cmd_off([])
        assert not is_disabled()  # Did not disable
        out = capsys.readouterr().out
        assert "active loop" in out.lower()

    def test_off_force_bypasses_loop_warning(self, hive_env):
        from keephive.storage import hive_dir

        loop_file = hive_dir() / ".loop-test123.json"
        loop_file.write_text(json.dumps({"task": "test"}))

        from keephive.commands.power import cmd_off

        cmd_off(["--force"])
        assert is_disabled()

    def test_off_stops_daemon(self, hive_env):
        from keephive.commands.power import cmd_off

        stopped = []
        with (
            patch("keephive.commands.daemon._is_running", return_value=True),
            patch("keephive.commands.daemon._stop", side_effect=lambda: stopped.append(True)),
        ):
            cmd_off([])
        assert is_disabled()
        assert stopped


class TestCmdOn:
    """hive on command."""

    def test_on_removes_flag(self, hive_env):
        set_disabled(True)
        from keephive.commands.power import cmd_on

        cmd_on([])
        assert not is_disabled()

    def test_on_already_on(self, hive_env, capsys):
        from keephive.commands.power import cmd_on

        cmd_on([])
        out = capsys.readouterr().out
        assert "already on" in out


class TestHookGate:
    """cli.py main() early-returns for hooks when disabled."""

    def test_hook_exits_silently_when_disabled(self, hive_env, capsys, monkeypatch):
        set_disabled(True)
        monkeypatch.setattr("sys.stdin", StringIO('{"source":"test","cwd":"/tmp"}'))
        from keephive.cli import main

        main(["hook-sessionstart"])
        out = capsys.readouterr().out
        assert out == ""

    def test_hook_runs_normally_when_enabled(self, hive_env, monkeypatch):
        """Hooks produce output when keephive is on."""
        monkeypatch.setattr("sys.stdin", StringIO('{"source":"test","cwd":"/tmp"}'))
        from keephive.cli import main

        main(["hook-sessionstart"])
        # SessionStart always produces JSON output when enabled


class TestDaemonGate:
    """_execute_task returns False when disabled."""

    def test_daemon_task_skipped_when_disabled(self, hive_env):
        set_disabled(True)
        from keephive.commands.daemon import _execute_task

        result = _execute_task("soul-update")
        assert result is False


class TestMcpGate:
    """MCP tools return disabled message when off."""

    def test_mcp_tool_returns_disabled_msg(self, hive_env):
        set_disabled(True)
        from keephive.mcp_server import _DISABLED_MSG, hive_status

        result = hive_status()
        assert result == _DISABLED_MSG

    def test_mcp_tool_works_when_enabled(self, hive_env):
        from keephive.mcp_server import hive_status

        result = hive_status()
        assert "keephive" in result.lower()


class TestStatusDisplay:
    """hive s shows disabled banner."""

    def test_status_json_includes_disabled(self, hive_env, capsys):
        set_disabled(True)
        from keephive.commands.status import cmd_status

        cmd_status(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["disabled"] is True

    def test_status_json_disabled_false(self, hive_env, capsys):
        from keephive.commands.status import cmd_status

        cmd_status(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["disabled"] is False


class TestCheckupJson:
    """hive checkup --json includes disabled field."""

    def test_checkup_json_disabled(self, hive_env, capsys):
        set_disabled(True)
        from keephive.commands.checkup import cmd_checkup

        cmd_checkup(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["disabled"] is True

    def test_checkup_json_not_disabled(self, hive_env, capsys):
        from keephive.commands.checkup import cmd_checkup

        cmd_checkup(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["disabled"] is False
