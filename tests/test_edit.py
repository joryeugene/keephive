"""Tests for edit command (commands/edit.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, call

import pytest


class TestCmdEdit:
    def test_default_shows_targets(self, hive_env, capsys):
        """No args shows available targets instead of opening editor."""
        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit([])
            mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "Edit targets:" in out
        assert "memory" in out
        assert "hive e" in out

    def test_memory_shortcut(self, hive_env):
        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["memory"])
            args = mock_run.call_args[0][0]
            assert args[1].endswith("memory.md")

    def test_rules_shortcut(self, hive_env):
        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["rules"])
            args = mock_run.call_args[0][0]
            assert args[1].endswith("rules.md")

    def test_note_shortcut(self, hive_env):
        """'note' opens active slot file."""
        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["note"])
            args = mock_run.call_args[0][0]
            assert "note-" in args[1]

    def test_draft_shortcut(self, hive_env):
        """'draft' opens active slot file (backward compat)."""
        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["draft"])
            args = mock_run.call_args[0][0]
            assert "note-" in args[1]

    def test_today_shortcut(self, hive_env):
        """'today' opens daily log."""
        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["today"])
            args = mock_run.call_args[0][0]
            assert "daily" in args[1]

    def test_search_path_fallback_guides(self, hive_env):
        """Search path finds file in guides dir."""
        gd = hive_env / "knowledge" / "guides"
        (gd / "my-guide.md").write_text("# Guide\n")

        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["my-guide.md"])
            args = mock_run.call_args[0][0]
            assert "my-guide.md" in args[1]

    def test_todo_target_calls_edit_todos(self, hive_env):
        """'todo' target delegates to edit_todos()."""
        with patch("keephive.commands.todo.edit_todos") as mock_edit:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["todo"])
            mock_edit.assert_called_once()

    def test_default_shows_todo_target(self, hive_env, capsys):
        """Help output lists 'todo' as an edit target."""
        with patch("subprocess.run"):
            from keephive.commands.edit import cmd_edit
            cmd_edit([])
        out = capsys.readouterr().out
        assert "todo" in out

    def test_unknown_shows_error(self, hive_env, capsys):
        """Unknown target shows error message."""
        with patch("subprocess.run") as mock_run:
            from keephive.commands.edit import cmd_edit
            cmd_edit(["nonexistent-xyz-abc"])
            mock_run.assert_not_called()
