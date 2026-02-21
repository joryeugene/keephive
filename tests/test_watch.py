"""Tests for keephive.watch: shared --watch infrastructure."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

from keephive.watch import _collect_mtimes, parse_watch_args, watch_loop

# ---- parse_watch_args ----


class TestParseWatchArgs:
    def test_no_flags(self):
        remaining, watch, interval = parse_watch_args(["--json"])
        assert remaining == ["--json"]
        assert watch is False
        assert interval == 2.0

    def test_watch_long(self):
        remaining, watch, interval = parse_watch_args(["--watch"])
        assert remaining == []
        assert watch is True
        assert interval == 2.0

    def test_watch_short(self):
        remaining, watch, interval = parse_watch_args(["-w"])
        assert remaining == []
        assert watch is True

    def test_interval(self):
        remaining, watch, interval = parse_watch_args(["--watch", "--interval", "5"])
        assert remaining == []
        assert watch is True
        assert interval == 5.0

    def test_interval_minimum_clamped(self):
        """Intervals below 0.5s are clamped to 0.5."""
        _, _, interval = parse_watch_args(["--watch", "--interval", "0.1"])
        assert interval == 0.5

    def test_interval_invalid_value(self):
        """Non-numeric interval is kept as regular arg."""
        remaining, watch, interval = parse_watch_args(["--interval", "abc"])
        assert remaining == ["--interval", "abc"]
        assert watch is False
        assert interval == 2.0

    def test_mixed_with_other_args(self):
        remaining, watch, interval = parse_watch_args(["yesterday", "--watch", "--interval", "3"])
        assert remaining == ["yesterday"]
        assert watch is True
        assert interval == 3.0

    def test_empty_args(self):
        remaining, watch, interval = parse_watch_args([])
        assert remaining == []
        assert watch is False
        assert interval == 2.0

    def test_interval_without_value(self):
        """--interval at end of args without a value is kept as regular arg."""
        remaining, watch, _ = parse_watch_args(["--watch", "--interval"])
        # --interval without a following value: just treated as a remaining arg
        assert "--interval" in remaining
        assert watch is True


# ---- _collect_mtimes ----


class TestCollectMtimes:
    def test_existing_files(self, tmp_path: Path):
        f1 = tmp_path / "a.md"
        f2 = tmp_path / "b.md"
        f1.write_text("hello")
        f2.write_text("world")
        mtimes = _collect_mtimes([f1, f2])
        assert len(mtimes) == 2
        assert str(f1) in mtimes
        assert str(f2) in mtimes
        assert all(isinstance(v, float) for v in mtimes.values())

    def test_missing_files_skipped(self, tmp_path: Path):
        f1 = tmp_path / "exists.md"
        f2 = tmp_path / "missing.md"
        f1.write_text("content")
        mtimes = _collect_mtimes([f1, f2])
        assert len(mtimes) == 1
        assert str(f1) in mtimes

    def test_directory_mtime(self, tmp_path: Path):
        """Directories have mtimes too (detects new/deleted files)."""
        mtimes = _collect_mtimes([tmp_path])
        assert len(mtimes) == 1
        assert str(tmp_path) in mtimes

    def test_empty_list(self):
        assert _collect_mtimes([]) == {}


# ---- watch_loop ----


class TestWatchLoop:
    def test_refuses_non_tty(self, capsys):
        """watch_loop prints error when stdout is not a TTY."""
        with patch.object(sys.stdout, "isatty", return_value=False):
            watch_loop(lambda: None, lambda: [], 1.0)
        captured = capsys.readouterr()
        assert "interactive terminal" in captured.err

    def test_keyboard_interrupt_exits_cleanly(self, tmp_path: Path):
        """KeyboardInterrupt produces clean 'Watch stopped' message."""
        call_count = 0

        def render():
            nonlocal call_count
            call_count += 1
            raise KeyboardInterrupt

        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            buf.isatty = lambda: True  # type: ignore[attr-defined]
            watch_loop(render, lambda: [tmp_path], 0.5)

        output = buf.getvalue()
        assert "Watch stopped" in output
        assert call_count == 1

    def test_rerender_on_mtime_change(self, tmp_path: Path):
        """Detects mtime change and re-renders (then raises to exit)."""
        f = tmp_path / "data.md"
        f.write_text("initial")

        render_calls: list[int] = []

        def render():
            render_calls.append(1)
            if len(render_calls) >= 2:
                raise KeyboardInterrupt

        def watch_paths():
            return [f]

        import time as _time

        original_sleep = _time.sleep

        def fast_sleep(seconds: float) -> None:
            # On first sleep (the poll), touch the file to trigger re-render
            if len(render_calls) == 1:
                f.write_text("changed")
            original_sleep(0.01)

        buf = io.StringIO()
        with (
            patch.object(sys, "stdout", buf),
            patch.object(_time, "sleep", fast_sleep),
        ):
            buf.isatty = lambda: True  # type: ignore[attr-defined]
            watch_loop(render, watch_paths, 0.1)

        assert len(render_calls) == 2


# ---- Integration: command dispatch ----


class TestStatusWatchDispatch:
    def test_json_takes_priority(self, hive_env):
        """--json is handled before --watch, so watch is never entered."""
        from keephive.commands.status import cmd_status

        # Should not enter watch loop, should print JSON and return
        with patch("keephive.watch.watch_loop") as mock_loop:
            cmd_status(["--json", "--watch"])
            mock_loop.assert_not_called()

    def test_watch_dispatches_to_loop(self, hive_env):
        """--watch triggers watch_loop with correct args."""
        from keephive.commands.status import cmd_status

        with patch("keephive.watch.watch_loop") as mock_loop:
            cmd_status(["--watch", "--interval", "3"])
            mock_loop.assert_called_once()
            args = mock_loop.call_args
            assert args[0][2] == 3.0  # interval


class TestLogWatchDispatch:
    def test_summarize_returns_early(self, hive_env):
        """'summarize' subcommand returns before watch check."""
        from keephive.commands.log import cmd_log

        with patch("keephive.watch.watch_loop") as mock_loop:
            # summarize will fail gracefully (no entries), but should not enter watch
            cmd_log(["summarize", "--watch"])
            mock_loop.assert_not_called()

    def test_watch_dispatches_to_loop(self, hive_env):
        """--watch triggers watch_loop for log."""
        from keephive.commands.log import cmd_log

        with patch("keephive.watch.watch_loop") as mock_loop:
            cmd_log(["--watch"])
            mock_loop.assert_called_once()


class TestTodoWatchDispatch:
    def test_subcommand_before_watch(self, hive_env):
        """'todo done X --watch' runs done, not watch."""
        from keephive.commands.todo import cmd_todo

        with patch("keephive.watch.watch_loop") as mock_loop:
            cmd_todo(["done", "nonexistent", "--watch"])
            mock_loop.assert_not_called()

    def test_watch_dispatches_to_loop(self, hive_env):
        """--watch triggers watch_loop for todo list."""
        from keephive.commands.todo import cmd_todo

        with patch("keephive.watch.watch_loop") as mock_loop:
            cmd_todo(["--watch"])
            mock_loop.assert_called_once()
