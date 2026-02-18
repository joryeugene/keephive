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
    """Prompt user for a single-char choice. Returns lowercase."""
    while True:
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return valid[-1]
        if answer in valid:
            return answer
        if not answer:
            return valid[-1]
