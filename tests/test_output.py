"""Test that Rich console output works in non-TTY (piped) environments."""

from __future__ import annotations


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
