"""Rich console output for keephive.

Replaces the ANSI escape codes in the bash version with Rich markup.
Respects NO_COLOR environment variable.
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.theme import Theme

# Custom theme matching the bash version's color scheme
_theme = Theme(
    {
        "ok": "green",
        "warn": "yellow",
        "err": "red",
        "info": "cyan",
        "dim": "dim",
        "tier.working": "green",
        "tier.knowledge": "blue",
        "tier.daily": "cyan",
        "tier.archive": "dim",
    }
)

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


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    import shutil
    import subprocess

    encoded = text.encode()
    for prog, extra_args in [
        ("pbcopy", []),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    ]:
        if shutil.which(prog):
            try:
                subprocess.run([prog, *extra_args], input=encoded, check=True, timeout=5)
                return True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return False
    return False


def prompt_yn(prompt: str, default_yes: bool = True) -> bool:
    """Y/n confirmation. Returns True for yes. Enter accepts the default."""
    import sys

    if not sys.stdin.isatty():
        return default_yes

    import termios
    import tty

    hint = "(Y/n)" if default_yes else "(y/N)"
    sys.stdout.write(f"{prompt} {hint} ")
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1).lower()
    except (EOFError, KeyboardInterrupt):
        ch = ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    if ch == "y":
        sys.stdout.write("y\n")
        return True
    if ch == "n":
        sys.stdout.write("n\n")
        return False
    # Enter (\r), space, or any other key → honor the default
    default_char = "Y" if default_yes else "N"
    sys.stdout.write(f"{default_char}\n")
    return default_yes
