"""Tests for undo_done (storage) and _todo_undo (command)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path


class TestUndoDone:
    def _make_daily(self, hive_env: Path, entries: list[str]) -> Path:
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        lines = [f"# Daily Log: {today}\n"]
        lines.extend(entries)
        daily.write_text("\n".join(lines) + "\n")
        return daily

    def test_undo_done_removes_last_done_entry(self, hive_env):
        """undo_done removes the most recent DONE line from the daily file."""
        from keephive.storage import undo_done

        self._make_daily(
            hive_env,
            [
                "- [10:00:00] TODO: Fix tests",
                "- [10:01:00] DONE: Fix tests",
            ],
        )
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        undo_done("Fix tests")
        content = daily.read_text()
        assert "DONE: Fix tests" not in content

    def test_undo_done_returns_text(self, hive_env):
        """undo_done returns the text of the removed entry."""
        from keephive.storage import undo_done

        self._make_daily(
            hive_env,
            [
                "- [10:01:00] DONE: Fix tests",
            ],
        )
        result = undo_done("Fix tests")
        assert result == "Fix tests"

    def test_undo_done_with_pattern(self, hive_env):
        """undo_done matches by substring pattern."""
        from keephive.storage import undo_done

        self._make_daily(
            hive_env,
            [
                "- [10:00:00] DONE: Run the full test suite",
                "- [10:01:00] DONE: Update documentation",
            ],
        )
        result = undo_done("documentation")
        assert result == "Update documentation"
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        content = daily.read_text()
        assert "Update documentation" not in content
        # Other DONE entry is untouched
        assert "Run the full test suite" in content

    def test_undo_done_no_match_returns_none(self, hive_env):
        """undo_done returns None when pattern has no match."""
        from keephive.storage import undo_done

        self._make_daily(
            hive_env,
            [
                "- [10:00:00] DONE: Fix tests",
            ],
        )
        result = undo_done("zzznomatch")
        assert result is None

    def test_undo_done_no_pattern_only_searches_today(self, hive_env):
        """Without pattern, undo_done only searches today's file."""
        from datetime import timedelta

        from keephive.storage import undo_done

        # Write a DONE entry to yesterday's file only
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        yesterday_file = hive_env / "daily" / f"{yesterday}.md"
        yesterday_file.write_text(f"# Daily Log: {yesterday}\n- [10:00:00] DONE: Yesterday task\n")
        # Today has no DONE entries
        self._make_daily(hive_env, ["- [09:00:00] TODO: Today task"])
        result = undo_done("")
        assert result is None

    def test_undo_done_no_daily_dir_returns_none(self, tmp_path, monkeypatch):
        """undo_done returns None when daily dir doesn't exist."""
        monkeypatch.setenv("HIVE_HOME", str(tmp_path / "empty_hive"))
        from keephive.storage import undo_done

        result = undo_done("anything")
        assert result is None

    def test_undo_done_removes_last_matching_entry(self, hive_env):
        """With multiple matching DONE entries, undo_done removes the last one."""
        from keephive.storage import undo_done

        self._make_daily(
            hive_env,
            [
                "- [09:00:00] DONE: Check logs",
                "- [10:00:00] DONE: Check logs",
            ],
        )
        result = undo_done("Check logs")
        assert result == "Check logs"
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        content = daily.read_text()
        # First entry remains
        assert content.count("DONE: Check logs") == 1
        assert "09:00:00" in content


class TestTodoUndoCommand:
    def _make_daily(self, hive_env: Path, entries: list[str]) -> Path:
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        lines = [f"# Daily Log: {today}\n"]
        lines.extend(entries)
        daily.write_text("\n".join(lines) + "\n")
        return daily

    def test_todo_undo_reopens_todo(self, hive_env, capsys):
        """Mark done then undo: TODO reappears as open."""
        from keephive.commands.todo import _todo_done, _todo_undo
        from keephive.storage import append_to_daily, ensure_daily, open_todos

        # Create a TODO (must use HH:MM:SS timestamp format for collect_todos to recognize it)
        ensure_daily()
        ts = datetime.now().strftime("%H:%M:%S")
        append_to_daily(f"- [{ts}] TODO: test undo feature")
        # Mark it done
        _todo_done("test undo feature")
        # Confirm it's closed
        todos = open_todos()
        assert not any("test undo feature" in t for _, _, t in todos)
        # Undo it
        _todo_undo("test undo feature")
        captured = capsys.readouterr()
        assert "Reopened" in captured.out

    def test_todo_undo_no_match_shows_warning(self, hive_env, capsys):
        """When no matching DONE, _todo_undo prints a warning."""
        from keephive.commands.todo import _todo_undo

        _todo_undo("zzznomatch")
        captured = capsys.readouterr()
        assert "No completed TODO matching" in captured.out

    def test_todo_undo_no_pattern_warns_when_empty(self, hive_env, capsys):
        """When no pattern and no DONE entries, shows 'no completed TODO' message."""
        from keephive.commands.todo import _todo_undo

        _todo_undo("")
        captured = capsys.readouterr()
        assert "No completed TODO to undo" in captured.out

    def test_cmd_todo_done_undo_routing(self, hive_env, capsys):
        """hive todo done undo <pat> routes to _todo_undo."""
        from keephive.commands.todo import cmd_todo
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        append_to_daily("- [10:00:00] DONE: routing test")
        cmd_todo(["done", "undo", "routing test"])
        captured = capsys.readouterr()
        assert "Reopened" in captured.out

    def test_cmd_todo_undo_routing(self, hive_env, capsys):
        """hive todo undo <pat> routes to _todo_undo."""
        from keephive.commands.todo import cmd_todo
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        append_to_daily("- [10:00:00] DONE: undo routing test")
        cmd_todo(["undo", "undo routing test"])
        captured = capsys.readouterr()
        assert "Reopened" in captured.out

    def test_cmd_td_undo_routing(self, hive_env, capsys):
        """hive td undo <pat> routes to _todo_undo."""
        from keephive.commands.todo import cmd_td
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        append_to_daily("- [10:00:00] DONE: td undo test")
        cmd_td(["undo", "td undo test"])
        captured = capsys.readouterr()
        assert "Reopened" in captured.out
