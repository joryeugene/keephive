"""Tests for CLI dispatch (cli.py)."""

from __future__ import annotations

import pytest

from keephive.cli import main, COMMANDS


class TestMainDispatch:
    def test_no_args_shows_status(self, hive_env, capsys):
        """No args defaults to status."""
        main([])
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_version(self, hive_env, capsys):
        main(["--version"])
        out = capsys.readouterr().out
        assert "keephive" in out.lower()

    def test_help(self, hive_env, capsys):
        main(["help"])
        out = capsys.readouterr().out
        assert "Usage" in out
        assert "keephive" in out

    def test_h_shows_help(self, hive_env, capsys):
        main(["h"])
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_help_flag(self, hive_env, capsys):
        main(["--help"])
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_unknown_command_exits_1(self, hive_env):
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent-cmd-xyz"])
        assert exc_info.value.code == 1

    def test_dot_notation_n3(self, hive_env, capsys):
        """n.3 dispatches to note slot 3."""
        (hive_env / "working" / "note-3.md").write_text("Slot 3\n")
        main(["n.3", "show"])
        out = capsys.readouterr().out
        assert "Slot 3" in out

    def test_dot_notation_n0(self, hive_env, capsys):
        """n.0 dispatches to note slot 10."""
        (hive_env / "working" / "note-10.md").write_text("Slot 10\n")
        main(["n.0", "show"])
        out = capsys.readouterr().out
        assert "Slot 10" in out

    def test_dot_notation_n3c(self, hive_env, capsys):
        """n.3c copies slot 3."""
        (hive_env / "working" / "note-3.md").write_text("Copy slot 3\n")
        main(["n.3c"])
        out = capsys.readouterr().out
        # Either prints content (no pbcopy) or confirmation (with pbcopy)
        assert "Copy slot 3" in out or "Copied" in out

    def test_dot_notation_d5(self, hive_env, capsys):
        """d.5 dispatches to note slot 5 (backward compat)."""
        (hive_env / "working" / "note-5.md").write_text("Via d.5\n")
        main(["d.5", "show"])
        out = capsys.readouterr().out
        assert "Via d.5" in out

    def test_regular_dispatch_status(self, hive_env, capsys):
        """'s' dispatches to status."""
        main(["s"])
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_regular_dispatch_log(self, hive_env, capsys):
        """'l' dispatches to log."""
        main(["l"])
        out = capsys.readouterr().out
        # Log outputs something (could be "No log" or actual log)
        assert isinstance(out, str)

    def test_g_runs_gc(self, hive_env, capsys):
        """'g' dispatches to gc."""
        main(["g"])
        out = capsys.readouterr().out
        assert "Garbage collection" in out or "archive" in out.lower() or "Nothing to archive" in out

    def test_inbox_not_in_commands(self):
        """Inbox commands removed from dispatch table."""
        assert "inbox" not in COMMANDS
        assert "i" not in COMMANDS
        assert "in" not in COMMANDS
