"""Test that Rich console output works in non-TTY (piped) environments."""

from __future__ import annotations

import os


class FakeTTY:
    """Simulates a TTY stdin returning a single character."""

    def __init__(self, char: str):
        self._char = char
        self._fd = os.open("/dev/null", os.O_RDONLY)

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._fd

    def read(self, n: int) -> str:
        return self._char[:n]


class FakeNonTTY:
    """Simulates piped (non-TTY) stdin."""

    def isatty(self) -> bool:
        return False


class TestPromptYn:
    def _patch(self, monkeypatch, char: str):
        fake = FakeTTY(char)
        monkeypatch.setattr("sys.stdin", fake)
        monkeypatch.setattr("termios.tcgetattr", lambda _fd: [])
        monkeypatch.setattr("termios.tcsetattr", lambda _fd, _when, _attrs: None)
        monkeypatch.setattr("tty.setraw", lambda _fd: None)

    def test_y_returns_true(self, monkeypatch):
        self._patch(monkeypatch, "y")
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?") is True

    def test_uppercase_y_returns_true(self, monkeypatch):
        self._patch(monkeypatch, "Y")
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?") is True

    def test_n_returns_false(self, monkeypatch):
        self._patch(monkeypatch, "n")
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?") is False

    def test_enter_accepts_default_yes(self, monkeypatch):
        self._patch(monkeypatch, "\r")
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?") is True

    def test_enter_accepts_default_no(self, monkeypatch):
        self._patch(monkeypatch, "\r")
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?", default_yes=False) is False

    def test_space_accepts_default_yes(self, monkeypatch):
        self._patch(monkeypatch, " ")
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?") is True

    def test_piped_returns_default_yes(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", FakeNonTTY())
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?") is True

    def test_piped_returns_default_no(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", FakeNonTTY())
        from keephive.output import prompt_yn

        assert prompt_yn("Continue?", default_yes=False) is False

    def test_ctrl_c_raises_keyboard_interrupt(self, monkeypatch):
        self._patch(monkeypatch, "\x03")
        import pytest

        from keephive.output import prompt_yn

        with pytest.raises(KeyboardInterrupt):
            prompt_yn("Continue?")


def test_console_force_terminal(capsys):
    """Console with force_terminal=True outputs to stdout even when piped."""
    from keephive.output import console

    console.print("hello from keephive")
    out = capsys.readouterr().out
    assert "hello from keephive" in out


def test_console_rich_markup_renders(capsys):
    """Rich markup renders without crashing (may strip tags in non-TTY)."""
    from keephive.output import console

    console.print("[bold]bold text[/bold]")
    out = capsys.readouterr().out
    assert "bold text" in out
