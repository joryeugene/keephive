"""hive profile: manage data profiles (demo, work, personal, etc.)."""

from __future__ import annotations

import re
import shutil
import sys

from keephive.output import console, show_hint
from keephive.storage import (
    active_profile,
    ensure_dirs,
    hive_dir,
    list_profiles,
    profile_dir,
    profile_exists,
    set_active_profile,
)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")


def _validate_name(name: str) -> str | None:
    """Validate profile name. Returns error message or None."""
    if name == "default":
        return None
    if not _NAME_RE.match(name):
        return (
            f"Invalid profile name: {name!r}. Use lowercase letters, digits, hyphens. Max 30 chars."
        )
    return None


def cmd_profile(args: list[str]) -> None:
    """Profile management: list, use, create, delete."""
    if not args:
        _list_profiles()
        return

    sub = args[0]

    if sub == "list":
        _list_profiles()
    elif sub == "use":
        if len(args) < 2:
            console.print("[err]Usage: hive profile use <name>[/err]")
            sys.exit(1)
        _use_profile(args[1])
    elif sub == "create":
        if len(args) < 2:
            console.print("[err]Usage: hive profile create <name> [--seed][/err]")
            sys.exit(1)
        seed = "--seed" in args
        _create_profile(args[1], seed=seed)
    elif sub == "delete":
        if len(args) < 2:
            console.print("[err]Usage: hive profile delete <name>[/err]")
            sys.exit(1)
        _delete_profile(args[1])
    else:
        # Bare name: shortcut for "use"
        _use_profile(sub)


def _list_profiles() -> None:
    """Show all profiles with active indicator."""
    profiles = list_profiles()
    console.print("[bold]Profiles[/bold]")
    for p in profiles:
        marker = " [ok]*[/ok]" if p["active"] else "  "
        exists = "" if p["exists"] else " [dim](not created)[/dim]"
        console.print(f"{marker} {p['name']}{exists}")

    current = active_profile()
    if current:
        console.print(
            f"\n[dim]Active: {current}  |  hive profile use default  to switch back[/dim]"
        )
    else:
        console.print(f"\n[dim]Active: default  |  Data: {hive_dir()}[/dim]")

    show_hint("hive profile use <name>")


def _use_profile(name: str) -> None:
    """Switch to a profile."""
    err = _validate_name(name)
    if err:
        console.print(f"[err]{err}[/err]")
        sys.exit(1)

    if name == "default":
        set_active_profile(None)
        console.print("[ok]Switched to default profile[/ok]")
        console.print(f"[dim]Data: {hive_dir()}[/dim]")
        return

    if not profile_exists(name):
        console.print(f"[err]Profile '{name}' does not exist. Create it first:[/err]")
        console.print(f"  hive profile create {name}")
        sys.exit(1)

    set_active_profile(name)
    console.print(f"[ok]Switched to profile: {name}[/ok]")
    console.print(f"[dim]Data: {hive_dir()}[/dim]")


def _create_profile(name: str, seed: bool = False) -> None:
    """Create a new profile directory."""
    err = _validate_name(name)
    if err:
        console.print(f"[err]{err}[/err]")
        sys.exit(1)

    if name == "default":
        console.print("[err]Cannot create 'default' profile (it already exists)[/err]")
        sys.exit(1)

    if profile_exists(name):
        target = profile_dir(name)
        console.print(f"[warn]Profile '{name}' already exists at {target}[/warn]")
        return

    target = profile_dir(name)
    # Create directory structure
    target.mkdir(parents=True, exist_ok=True)
    # Temporarily switch to new profile to use ensure_dirs
    old_profile = active_profile()
    set_active_profile(name)
    try:
        ensure_dirs()
    finally:
        # Restore previous profile
        set_active_profile(old_profile)

    console.print(f"[ok]Created profile: {name}[/ok]")
    console.print(f"[dim]Data: {target}[/dim]")
    show_hint(f"hive profile use {name}")

    if seed:
        try:
            from keephive.commands.seed import cmd_seed

            # Temporarily switch to seed into the new profile
            set_active_profile(name)
            try:
                cmd_seed(["--force"])
            finally:
                set_active_profile(old_profile)
            console.print("[ok]Seeded with demo data[/ok]")
        except ImportError:
            console.print("[warn]Seed module not available[/warn]")


def _delete_profile(name: str) -> None:
    """Delete a profile and its data."""
    err = _validate_name(name)
    if err:
        console.print(f"[err]{err}[/err]")
        sys.exit(1)

    if name == "default":
        console.print("[err]Cannot delete the default profile[/err]")
        sys.exit(1)

    current = active_profile()
    if current == name:
        console.print(f"[err]Cannot delete active profile '{name}'. Switch first:[/err]")
        console.print("  hive profile use default")
        sys.exit(1)

    target = profile_dir(name)
    if not target.exists():
        console.print(f"[warn]Profile '{name}' does not exist[/warn]")
        return

    from keephive.output import prompt_yn

    if not prompt_yn(f"Delete profile '{name}' and ALL its data at {target}?", default_yes=False):
        console.print("[dim]Cancelled[/dim]")
        return

    shutil.rmtree(target)
    console.print(f"[ok]Deleted profile: {name}[/ok]")
