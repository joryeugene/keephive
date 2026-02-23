"""Self-tests for the tmux terminal driver itself.

Validates that the Terminal class correctly:
- Types commands and reads output
- Persists shell environment between commands
- Isolates HIVE_HOME to temp directory
- Overrides dates via HIVE_DATE
- Records command history
- Handles timeouts

Run: uv run pytest -m terminal tests/test_terminal_driver.py -v -o "addopts="
"""

from __future__ import annotations

import json
import time

import pytest
from terminal import Screen


@pytest.mark.terminal
class TestDriverBasics:
    def test_echo(self, term):
        """Basic: type echo, read output."""
        term.type("echo hello world").has("hello world")

    def test_tty_detection(self, term):
        """Terminal environment reports as a real TTY ([ -t 0 ])."""
        term.type("[ -t 0 ] && echo 'IS_TTY' || echo 'NOT_TTY'").has("IS_TTY")

    def test_exit_code_zero(self, term):
        """Successful command returns clean output."""
        screen = term.type("python -m keephive --version")
        screen.has("keephive")

    def test_env_persistence(self, term):
        """Shell environment persists between commands."""
        term.type("export MY_TEST_VAR=persistence_check")
        term.type("echo $MY_TEST_VAR").has("persistence_check")

    def test_hive_home_isolated(self, term):
        """HIVE_HOME points to temp directory, not real ~/.keephive/hive."""
        screen = term.type("echo $HIVE_HOME")
        screen.has("hive")
        screen.lacks(".claude")

    def test_date_override(self, term):
        """HIVE_DATE env var persists after set_date()."""
        term.set_date("2026-06-15")
        term.type("echo $HIVE_DATE").has("2026-06-15")

    def test_daily_file_uses_overridden_date(self, term):
        """Commands create files using HIVE_DATE, not real date."""
        term.set_date("2026-06-15")
        term.type("python -m keephive r 'FACT: summer test'")
        assert term.file_exists("daily/2026-06-15.md")

    def test_ansi_codes_present(self, term):
        """Rich outputs ANSI to real terminal (xterm-256color)."""
        term._send("unset NO_COLOR")
        time.sleep(0.1)
        term.type("python -m keephive s").has_ansi()

    def test_screen_read_without_typing(self, term):
        """screen() reads current state without sending a command."""
        term.type("echo visible")
        s = term.screen()
        assert isinstance(s, Screen)

    def test_history_recording(self, term):
        """Every type() call recorded in _history."""
        term.type("echo one")
        term.type("echo two")
        assert len(term._history) == 2
        assert term._history[0]["command"] == "echo one"
        assert term._history[1]["command"] == "echo two"

    def test_save_history_artifact(self, term, tmp_path):
        """save_history() writes JSON artifact."""
        term.type("echo artifact")
        out = tmp_path / "history.json"
        term.save_history(out)
        data = json.loads(out.read_text())
        assert len(data) == 1
        assert data[0]["command"] == "echo artifact"

    def test_timeout_raises(self, term):
        """Command that never completes raises TimeoutError."""
        with pytest.raises(TimeoutError):
            term.type("sleep 999", timeout=0.5)

    def test_long_output(self, term):
        """Commands producing many lines captured correctly."""
        term.type("seq 1 100").line_count_between(99, 101)

    def test_special_chars_in_echo(self, term):
        """Quotes and special chars pass through tmux send-keys."""
        term.type("echo 'hello world'").has("hello world")

    def test_advance_days(self, term):
        """advance_days() calculates correct date from base."""
        term.advance_days(5, "2026-01-10")
        term.type("echo $HIVE_DATE").has("2026-01-15")

    def test_read_file(self, term):
        """read_file() reads from hive_home directory."""
        term.set_date("2026-07-01")
        term.type("python -m keephive r 'FACT: file read test'").has("Remembered")
        content = term.read_file("daily/2026-07-01.md")
        assert "file read test" in content

    def test_file_exists(self, term):
        """file_exists() checks hive_home directory."""
        assert term.file_exists("working")
        assert not term.file_exists("nonexistent/path.md")

    def test_screen_has_chaining(self, term):
        """Screen assertion methods return self for chaining."""
        screen = term.type("echo 'alpha beta gamma'")
        result = screen.has("alpha").has("beta").lacks("delta")
        assert result is screen

    def test_screen_contains(self, term):
        """Screen supports `in` operator."""
        screen = term.type("echo findme")
        assert "findme" in screen
        assert "nothere" not in screen

    def test_screen_matches_regex(self, term):
        """Screen.matches() works with regex patterns."""
        term.type("echo 'version 1.2.3'").matches(r"version \d+\.\d+\.\d+")
