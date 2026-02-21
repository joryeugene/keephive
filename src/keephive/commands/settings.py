"""hive set: view and change persistent settings."""

from __future__ import annotations

from pathlib import Path

from keephive.output import console
from keephive.settings import BUILTIN_SOUNDS, DEFAULTS, DESCRIPTIONS, get_setting, read_settings, set_setting


def _parse_bool(val: str) -> bool | None:
    """Parse a boolean string. Returns None if not recognized."""
    low = val.lower()
    if low in ("on", "true", "1", "yes"):
        return True
    if low in ("off", "false", "0", "no"):
        return False
    return None


def cmd_set(args: list[str]) -> None:
    """View or change settings.

    No args: show all settings.
    hive set <key>: show single setting.
    hive set <key> <value>: set and persist.
    """
    if not args:
        _show_all()
        return

    key = args[0]

    if key not in DEFAULTS:
        console.print(f"[err]Unknown setting:[/err] {key}")
        valid = ", ".join(sorted(DEFAULTS))
        console.print(f"  Valid keys: {valid}")
        return

    if len(args) < 2:
        _show_one(key)
        return

    raw_val = args[1]

    # Boolean settings
    if isinstance(DEFAULTS[key], bool):
        parsed = _parse_bool(raw_val)
        if parsed is None:
            console.print(f"[err]Invalid value:[/err] {raw_val}")
            console.print("  Use: on/off, true/false, 1/0, yes/no")
            return
        set_setting(key, parsed)
        label = "on" if parsed else "off"
        console.print(f"  {key}  [bold]{label}[/bold]")
    else:
        if key in ("sound_success", "sound_error"):
            if raw_val not in BUILTIN_SOUNDS and not Path(raw_val).exists():
                console.print(f"[warn]Warning:[/warn] '{raw_val}' is not a builtin sound or existing file")
                console.print(f"  Builtin: {', '.join(BUILTIN_SOUNDS)}")
        set_setting(key, raw_val)
        console.print(f"  {key}  [bold]{raw_val}[/bold]")


def _show_all() -> None:
    """Display all settings as a table."""
    settings = read_settings()
    console.print("[bold]Settings[/bold]")
    console.print()
    for key in sorted(DEFAULTS):
        val = settings.get(key, DEFAULTS[key])
        desc = DESCRIPTIONS.get(key, "")
        if isinstance(val, bool):
            label = "[ok]on[/ok]" if val else "[dim]off[/dim]"
        else:
            label = str(val)
        console.print(f"  {key:16s} {label:16s} {desc}")
    console.print()
    console.print("  [dim]hive set <key> on/off[/dim]")


def _show_one(key: str) -> None:
    """Display a single setting."""
    val = get_setting(key)
    desc = DESCRIPTIONS.get(key, "")
    if isinstance(val, bool):
        label = "on" if val else "off"
    else:
        label = str(val)
    console.print(f"  {key}  [bold]{label}[/bold]  {desc}")


def cmd_sound_test(args: list[str]) -> None:
    """Play the configured notification sounds for testing.

    hive sound-test          Play success sound
    hive sound-test error    Play error sound
    """
    import platform
    import shutil
    import subprocess

    if platform.system() != "Darwin":
        console.print("[warn]Sound only supported on macOS[/warn]")
        return
    if not shutil.which("afplay"):
        console.print("[err]afplay not found[/err]")
        return

    success = not (args and args[0] in ("error", "fail", "false"))
    name = get_setting("sound_success") if success else get_setting("sound_error")

    if name in BUILTIN_SOUNDS:
        sound = f"/System/Library/Sounds/{name}.aiff"
    else:
        sound = str(name)

    if not Path(sound).exists():
        console.print(f"[err]Sound file not found:[/err] {sound}")
        return

    label = "success" if success else "error"
    console.print(f"  Playing {label} sound: [bold]{name}[/bold]")
    subprocess.run(["afplay", sound], check=False)
