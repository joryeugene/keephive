"""hive n / hive note: Tot-style multi-slot scratchpad with clipboard."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from keephive.output import console, copy_to_clipboard, prompt_yn
from keephive.storage import (
    NOTE_SLOT_COUNT,
    active_slot,
    drafts_dir,
    ensure_dirs,
    notes_dir,
    prompts_dir,
    set_active_slot,
    slot_file,
    working_dir,
)


def _ensure_migration() -> None:
    """Migrate draft.md -> note-1.md and drafts/ -> notes/ if needed."""
    old_draft = working_dir() / "draft.md"
    new_slot1 = slot_file(1)

    # Migrate draft.md -> note-1.md
    if old_draft.exists() and not new_slot1.exists():
        text = old_draft.read_text()
        if text.strip():
            new_slot1.write_text(text)
        old_draft.unlink()
    elif old_draft.exists() and new_slot1.exists():
        # Both exist: keep note-1.md, remove old draft if empty
        if not old_draft.read_text().strip():
            old_draft.unlink()

    # Migrate drafts/ -> notes/
    old_dir = drafts_dir()
    new_dir = notes_dir()
    if old_dir.exists() and list(old_dir.glob("*.md")):
        new_dir.mkdir(parents=True, exist_ok=True)
        for f in old_dir.glob("*.md"):
            dest = new_dir / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
        # Remove old dir if empty
        if not list(old_dir.iterdir()):
            old_dir.rmdir()


def _note_file(slot: int | None = None) -> Path:
    """Return path to note file for given slot (or active slot)."""
    return slot_file(slot or active_slot())


def cmd_note(args: list[str]) -> None:
    """Router: no args=edit, copy/show/clear/list/digit/template."""
    ensure_dirs()
    _ensure_migration()

    if not args:
        _note_edit()
        return

    sub = args[0]
    if sub in ("copy", "c"):
        _note_copy()
    elif sub in ("show", "s"):
        _note_show()
    elif sub == "clear":
        _note_clear()
    elif sub in ("list", "l"):
        _note_list()
    elif sub.isdigit():
        digit = int(sub)
        slot = 10 if digit == 0 else digit
        if 1 <= slot <= NOTE_SLOT_COUNT:
            set_active_slot(slot)
            cmd_note_slot(slot, args[1:])
        else:
            console.print(f"[err]Invalid slot: {sub}[/err] (1-9, 0=10)")
    else:
        _note_from_template(sub)


def cmd_note_copy(args: list[str]) -> None:
    """Direct alias for note copy."""
    ensure_dirs()
    _ensure_migration()
    _note_copy()


def cmd_note_slot(slot: int, args: list[str]) -> None:
    """Handle n.N commands: switch to slot, then dispatch subcommand."""
    ensure_dirs()
    _ensure_migration()
    set_active_slot(slot)

    if not args:
        _note_edit()
        return

    sub = args[0]
    if sub in ("copy", "c"):
        _note_copy()
    elif sub in ("show", "s"):
        _note_show()
    elif sub == "clear":
        _note_clear()
    else:
        _note_edit()


def _note_edit(slot: int | None = None) -> None:
    """Open $EDITOR on note slot. Auto-copy if changed."""
    path = _note_file(slot)
    if not path.exists():
        path.write_text("")

    before = path.read_text()
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)])
    after = path.read_text()

    lines = sum(1 for line in after.splitlines() if line.strip())
    slot_num = slot or active_slot()
    if after != before and after.strip():
        if copy_to_clipboard(after):
            console.print(
                f"[ok]Note [bold]\\[{slot_num}][/bold] saved[/ok] ({lines}L) and copied to clipboard"
            )
        else:
            console.print(f"[ok]Note [bold]\\[{slot_num}][/bold] saved[/ok] ({lines}L)")
    elif after.strip():
        console.print(f"[dim]Note \\[{slot_num}] unchanged ({lines}L)[/dim]")
    else:
        console.print(f"[dim]Note \\[{slot_num}] is empty[/dim]")

    _print_slot_bar()


def _note_from_template(name: str) -> None:
    """Populate from a prompt template, open editor."""
    pd = prompts_dir()

    from keephive.commands.knowledge import _resolve_file

    match = _resolve_file(name, pd)

    if not match:
        console.print(f"[err]Prompt not found:[/err] {name}")
        if pd.exists():
            available = sorted(f.stem for f in pd.glob("*.md"))
            if available:
                console.print("Available prompts:")
                for n in available:
                    console.print(f"  {n}")
        return

    path = _note_file()
    path.write_text(match.read_text())
    console.print(f"[ok]Loaded template:[/ok] {match.stem}")

    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)])

    after = path.read_text()
    lines = sum(1 for line in after.splitlines() if line.strip())
    slot_num = active_slot()
    if after.strip():
        if copy_to_clipboard(after):
            console.print(
                f"[ok]Note [bold]\\[{slot_num}][/bold] saved[/ok] ({lines}L) and copied to clipboard"
            )
        else:
            console.print(f"[ok]Note [bold]\\[{slot_num}][/bold] saved[/ok] ({lines}L)")

    _print_slot_bar()


def _note_copy(slot: int | None = None) -> None:
    """Copy note to clipboard, or print to stdout if no clipboard tool."""
    path = _note_file(slot)
    if not path.exists() or not path.read_text().strip():
        console.print("[dim]No note to copy. Run: hive n[/dim]")
        return

    text = path.read_text()
    slot_num = slot or active_slot()
    if copy_to_clipboard(text):
        lines = sum(1 for line in text.splitlines() if line.strip())
        console.print(f"[ok]Copied[/ok] note \\[{slot_num}] to clipboard ({lines}L)")
    else:
        print(text)


def _note_show(slot: int | None = None) -> None:
    """Print note with stats header."""
    path = _note_file(slot)
    if not path.exists() or not path.read_text().strip():
        console.print("[dim]No note. Run: hive n[/dim]")
        return

    text = path.read_text()
    lines = sum(1 for line in text.splitlines() if line.strip())
    size = len(text.encode())
    slot_num = slot or active_slot()
    console.print(f"[bold]Note \\[{slot_num}][/bold] ({lines}L, {size}B)\n")
    print(text)
    if sys.stdout.isatty():
        if copy_to_clipboard(text):
            console.print(f"[dim]Copied note [{slot_num}] to clipboard[/dim]")


def _note_clear(slot: int | None = None) -> None:
    """Clear the note slot."""
    path = _note_file(slot)
    slot_num = slot or active_slot()
    if path.exists() and path.read_text().strip():
        if not prompt_yn(f"  Clear note [{slot_num}]?"):
            return
    if path.exists():
        path.write_text("")
    console.print(f"[ok]Note \\[{slot_num}] cleared[/ok]")
    _print_slot_bar()


def _note_list() -> None:
    """Show slot bar with previews."""
    # Slot overview
    console.print("[bold]Note Slots[/bold]\n")
    _print_slot_bar()
    console.print()

    # Per-slot previews
    for n in range(1, NOTE_SLOT_COUNT + 1):
        path = slot_file(n)
        if path.exists() and path.read_text().strip():
            text = path.read_text()
            lines = sum(1 for line in text.splitlines() if line.strip())
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            if len(first_line) > 60:
                first_line = first_line[:57] + "..."
            label = n if n < 10 else 0
            active = active_slot()
            if n == active:
                console.print(f"  [bold]{label}.[/bold] {first_line}  [dim]({lines}L)[/dim]")
            else:
                console.print(f"  [dim]{label}.[/dim] {first_line}  [dim]({lines}L)[/dim]")


def _slot_bar() -> str:
    """Build slot bar string: 1  2  [3]  4  5  6  7  8  9  0"""
    active = active_slot()
    parts = []
    for n in range(1, NOTE_SLOT_COUNT + 1):
        label = str(n) if n < 10 else "0"
        path = slot_file(n)
        has_content = path.exists() and path.read_text().strip()

        if n == active:
            parts.append(f"[bold]\\[{label}][/bold]")
        elif has_content:
            parts.append(label)
        else:
            parts.append(f"[dim]{label}[/dim]")
    return "  ".join(parts)


def _print_slot_bar() -> None:
    """Print the slot bar to console."""
    console.print(f"  {_slot_bar()}")
