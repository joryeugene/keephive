"""hive e: edit files with $EDITOR."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from keephive.output import console
from keephive.storage import (
    active_slot,
    daily_file,
    ensure_daily,
    guides_dir,
    knowledge_dir,
    prompts_dir,
    slot_file,
    working_dir,
)


def cmd_edit(args: list[str]) -> None:
    target = args[0] if args else ""
    editor = os.environ.get("EDITOR", "vi")

    shortcuts = {
        "": working_dir() / "memory.md",
        "memory": working_dir() / "memory.md",
        "rules": working_dir() / "rules.md",
        "note": slot_file(active_slot()),
        "draft": slot_file(active_slot()),
        "settings": Path.home() / ".claude" / "settings.json",
        "local": Path.home() / ".claude" / "settings.local.json",
    }

    if target in shortcuts:
        _open_editor(editor, shortcuts[target])
        return

    if target == "claude":
        # Walk up from cwd looking for CLAUDE.md
        d = Path.cwd()
        while d != d.parent:
            if (d / "CLAUDE.md").exists():
                _open_editor(editor, d / "CLAUDE.md")
                return
            d = d.parent
        _open_editor(editor, Path.home() / ".claude" / "CLAUDE.md")
        return

    if target == "claude-root":
        _open_editor(editor, Path.home() / ".claude" / "CLAUDE.md")
        return

    if target in ("today", "daily"):
        ensure_daily()
        _open_editor(editor, daily_file())
        return

    # Try various locations
    t = Path(target)
    search_paths = [
        t,
        working_dir() / target,
        working_dir() / f"{target}.md",
        Path(str(daily_file()).replace(daily_file().name, target)),
        knowledge_dir() / target,
        knowledge_dir() / f"{target}.md",
        guides_dir() / target,
        guides_dir() / f"{target}.md",
        prompts_dir() / target,
        prompts_dir() / f"{target}.md",
    ]

    for p in search_paths:
        if p.exists():
            _open_editor(editor, p)
            return

    console.print(f"[err]File not found:[/err] {target}")
    console.print("Shortcuts: memory, rules, claude, claude-root, settings, local, today")


def _open_editor(editor: str, path: Path) -> None:
    subprocess.run([editor, str(path)])
