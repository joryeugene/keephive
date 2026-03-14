"""Rich console output for keephive.

Replaces the ANSI escape codes in the bash version with Rich markup.
Respects NO_COLOR environment variable.
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.theme import Theme

# Amber Terminal: single source of truth for keephive's visual identity.
# Both the Rich CLI theme and the serve.py CSS :root block derive from this dict.
# To change a color across the entire product, edit it here.
DESIGN_TOKENS: dict[str, str] = {
    # Primary (amber/honey)
    "primary": "#D4A04A",
    "primary-dim": "#9A7235",
    "primary-bright": "#E8C882",
    "primary-bg": "#2D2518",
    # Neutrals (warm-tinted)
    "bg": "#1A1714",
    "surface": "#23201B",
    "surface-2": "#2C2823",
    "surface-3": "#36312A",
    "hover": "#282420",
    "border": "#403930",
    "border-subtle": "#332E27",
    # Text
    "text": "#D9CFC0",
    "text-bright": "#E8E0D4",
    "text-secondary": "#9E9486",
    "text-tertiary": "#766E63",
    "text-dim": "#5E5549",
    # Semantic
    "ok": "#4CB060",
    "ok-dim": "#3A8548",
    "ok-bg": "#152B1A",
    "ok-bright": "#60B870",
    "warn": "#C9A030",
    "warn-dim": "#A88020",
    "warn-bg": "#2D2710",
    "err": "#D05040",
    "err-dim": "#8A3028",
    "err-bg": "#2E1510",
    "err-bright": "#E06858",
    "info": "#5E9EC4",
    "info-bg": "#152028",
    # Categories
    "correction": "#D08050",
    "correction-bg": "#2E1D10",
    "decision": "#C490D0",
    "decision-bg": "#2A1E30",
    "fact": "#D4A04A",
    "fact-bg": "#2D2518",
}

_theme = Theme(
    {
        "ok": DESIGN_TOKENS["ok"],
        "warn": DESIGN_TOKENS["warn"],
        "err": DESIGN_TOKENS["err"],
        "info": DESIGN_TOKENS["info"],
        "dim": DESIGN_TOKENS["text-tertiary"],
        "accent": DESIGN_TOKENS["primary"],
        "tier.working": DESIGN_TOKENS["ok"],
        "tier.knowledge": DESIGN_TOKENS["info"],
        "tier.daily": DESIGN_TOKENS["primary"],
        "tier.archive": DESIGN_TOKENS["text-tertiary"],
    }
)

# Respect NO_COLOR
_no_color = "NO_COLOR" in os.environ
console = Console(theme=_theme, no_color=_no_color, force_terminal=not _no_color)


def prompt_choice(prompt: str, valid: list[str], default: str | None = None) -> str:
    """Prompt for single-char choice. Instant keypress on TTY, no Enter needed.

    If the input is cancelled or not recognized, return the provided default
    choice. When no default is supplied, fall back to the last valid option.
    """
    import sys

    fallback = default if default in valid else valid[-1]

    if not sys.stdin.isatty():
        # Piped input: fall back to input()
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return fallback
        return answer if answer in valid else fallback

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
        ch = fallback
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x03":  # Ctrl+C in raw mode (tty.setraw disables ISIG)
        sys.stdout.write("\n")
        raise KeyboardInterrupt
    if ch == "\x1b":  # Escape = cancel
        sys.stdout.write("cancelled\n")
        return fallback
    sys.stdout.write(ch + "\n")
    return ch if ch in valid else fallback


def prompt_review_item(
    label: str = "Accept?",
    edit_label: str = "Edit",
    indent: str = "  ",
) -> tuple[str, str | None]:
    """Interactive y/n/s/e review prompt with per-choice feedback.

    Returns (action, text):
        ("accept", None)         - user pressed y
        ("accept", "edited...")  - user edited and provided text
        ("dismiss", None)        - user pressed n/Enter (reject, remove from pending)
        ("skip", None)           - user pressed s (defer to next review)
        ("defer", None)          - edit started but empty input
    """
    choice = prompt_choice(
        f"{indent}{label} (y)es (N)o (s)kip (e)dit ? ",
        ["y", "n", "s", "e"],
        default="n",
    )
    if choice == "y":
        console.print(f"{indent}[ok]\u2713 accepted[/ok]")
        return ("accept", None)
    if choice == "e":
        edited = input(f"{indent}{edit_label}: ").strip()
        if edited:
            console.print(f"{indent}[ok]\u2713 accepted (edited)[/ok]")
            return ("accept", edited)
        console.print(f"{indent}[dim]deferred (empty edit)[/dim]")
        return ("defer", None)
    if choice == "s":
        console.print(f"{indent}[dim]skipped[/dim]")
        return ("skip", None)
    console.print(f"{indent}[dim]dismissed[/dim]")
    return ("dismiss", None)


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


def notify_sound(success: bool = True) -> None:
    """Play a short audio notification. Silent on non-macOS, missing binary, or sound=off."""
    import platform
    import shutil
    import subprocess
    from pathlib import Path

    if platform.system() != "Darwin":
        return
    if not shutil.which("afplay"):
        return
    from keephive.settings import BUILTIN_SOUNDS, get_setting  # lazy to avoid circular

    if not get_setting("sound"):
        return
    name = get_setting("sound_success") if success else get_setting("sound_error")
    if name in BUILTIN_SOUNDS:
        sound = f"/System/Library/Sounds/{name}.aiff"
    else:
        sound = str(name)  # custom file path
    if not Path(sound).exists():
        return
    subprocess.Popen(["afplay", sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_hint(command: str, reason: str = "") -> None:
    """Show a next-action hint after command output."""
    if reason:
        console.print(f"\n  [dim]{command}[/dim]  [dim]({reason})[/dim]")
    else:
        console.print(f"\n  [dim]{command}[/dim]")


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

    if ch == "\x03":  # Ctrl+C in raw mode (tty.setraw disables ISIG)
        sys.stdout.write("\n")
        raise KeyboardInterrupt
    if ch == "\x1b":  # Escape = cancel
        sys.stdout.write("cancelled\n")
        return False
    if ch == "y":
        sys.stdout.write("y\n")
        return True
    if ch == "n":
        sys.stdout.write("n\n")
        return False
    if ch in ("\r", "\n", " "):
        # Enter or space → honor the default
        default_char = "Y" if default_yes else "N"
        sys.stdout.write(f"{default_char}\n")
        return default_yes
    # Any other unrecognized key → cancel (no)
    sys.stdout.write("cancelled\n")
    return False
