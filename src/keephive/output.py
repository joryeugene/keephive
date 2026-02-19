"""Rich console output for keephive.

Replaces the ANSI escape codes in the bash version with Rich markup.
Respects NO_COLOR environment variable.
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.theme import Theme

# Custom theme matching the bash version's color scheme
_theme = Theme({
    "ok": "green",
    "warn": "yellow",
    "err": "red",
    "info": "cyan",
    "dim": "dim",
    "tier.working": "green",
    "tier.knowledge": "blue",
    "tier.daily": "cyan",
    "tier.archive": "dim",
})

# Respect NO_COLOR
_no_color = "NO_COLOR" in os.environ
console = Console(theme=_theme, no_color=_no_color, force_terminal=not _no_color)


def prompt_choice(prompt: str, valid: list[str]) -> str:
    """Prompt for single-char choice. Instant keypress on TTY, no Enter needed."""
    import sys

    if not sys.stdin.isatty():
        # Piped input: fall back to input()
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return valid[-1]
        return answer if answer in valid else valid[-1]

    import termios
    import tty

    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1).lower()
    except (EOFError, KeyboardInterrupt):
        ch = valid[-1]
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write(ch + "\n")
    return ch if ch in valid else valid[-1]
