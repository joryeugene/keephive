"""hive n / hive note: Tot-style multi-slot scratchpad with clipboard."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from keephive.claude import ClaudePipeError, run_claude_pipe
from keephive.models import NoteExtractResponse
from keephive.output import console, copy_to_clipboard, notify_sound, prompt_yn
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
    elif sub == "todo":
        _note_extract_todos(active_slot())
        return
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
    elif sub in ("list", "l"):
        _note_list()
    elif sub == "todo":
        _note_extract_todos(slot)
        return
    else:
        text = " ".join(args)
        if text.strip():
            _note_append_text(slot, text.strip())
        else:
            _note_edit()


def _note_append_text(slot: int, text: str) -> None:
    """Append free text to a note slot without opening editor."""
    path = slot_file(slot)
    existing = path.read_text() if path.exists() else ""
    sep = "\n" if existing and not existing.endswith("\n") else ""
    path.write_text(existing + sep + text + "\n")
    console.print(f"[ok]→ note [{slot}]:[/ok] {text}")


def _extract_structured_items(content: str) -> list[str]:
    """Extract bullet/checkbox/numbered items from structured note content."""
    lines = content.splitlines()
    items = []

    # Check for ## todo / # todo section first — collect both plain lines and bullets
    in_todo_section = False
    plain_items: list[str] = []
    bullet_items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().lstrip("#").strip() in ("todo", "todos", "to do", "to-do"):
            in_todo_section = True
            continue
        if in_todo_section:
            if stripped.startswith("#"):
                break  # next section
            if stripped.startswith(("- [ ] ", "- ", "* ", "• ")):
                text = stripped.lstrip("-*•").lstrip("[ ]x").strip()
                if text and len(text) <= 120:
                    bullet_items.append(text)
            elif stripped and len(stripped) <= 120:
                plain_items.append(stripped)

    if plain_items or bullet_items:
        seen: set[str] = set()
        result: list[str] = []
        for item in plain_items + bullet_items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    # Fallback: checkboxes (skip completed)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- [ ] "):
            text = stripped[6:].strip()
            if text:
                items.append(text)
        # Skip - [x] (completed)

    if items:
        return [item for item in items if len(item) <= 120]

    # Plain bullets if >50% of non-empty lines are bullet lines
    non_empty = [line.strip() for line in lines if line.strip()]
    bullets = [line for line in non_empty if line.startswith(("- ", "* ", "• "))]
    if non_empty and len(bullets) / len(non_empty) >= 0.5:
        for line in bullets:
            text = line.lstrip("-*• ").strip()
            if text:
                items.append(text)

    return [item for item in items if len(item) <= 120]


def _build_todo_buffer(note_content: str, candidates: set[str]) -> str:
    """Build edit buffer from full note, pre-marking extracted candidates with '- '.

    Non-candidate bullet lines are stripped of their prefix (shown as plain text
    context). Lines starting with '- ' in the result become TODOs after editing.
    Unmatched candidates (from LLM extraction) are appended after '---'.
    """
    lines = note_content.splitlines()
    result = [
        'Lines starting with "- " become TODOs. Delete any you don\'t want. Save and quit to confirm.',
        "",
    ]
    matched: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue

        # Extract text content regardless of bullet prefix
        if stripped.startswith(("- [ ] ", "- [x] ", "- [X] ")):
            text = stripped[6:].strip()
        elif stripped.startswith(("- ", "* ", "• ")):
            text = stripped.lstrip("-*•").strip()
        else:
            text = stripped

        if text in candidates:
            result.append(f"- {text}")
            matched.add(text)
        elif stripped.startswith("#"):
            result.append(line)  # section headers: keep as-is
        elif stripped.startswith(("- ", "* ", "• ")):
            result.append(text)  # non-candidate bullets: strip prefix (context)
        else:
            result.append(line)  # plain text: keep as-is

    # Append unmatched candidates (LLM-extracted items not found in note)
    unmatched = [c for c in candidates if c not in matched]
    if unmatched:
        result.append("")
        result.append("---")
        for item in unmatched:
            result.append(f"- {item}")

    return "\n".join(result)


def _note_extract_todos(slot: int) -> None:
    """Extract action items from note slot and offer to add as TODOs."""
    from datetime import datetime as _datetime

    from keephive.storage import append_to_daily, ensure_daily

    path = slot_file(slot)
    if not path.exists() or not path.read_text().strip():
        console.print(f"[dim]Slot {slot} is empty.[/dim]")
        return

    content = path.read_text().strip()

    # Tier 1: structured extraction
    items = _extract_structured_items(content)

    # Tier 2: LLM fallback if no structure and content is substantial
    if not items and len(content) >= 50 and not os.environ.get("HIVE_SKIP_LLM"):
        console.print("[dim]No bullet points found. Scanning for action items...[/dim]")
        prompt = (
            "Extract specific actionable TODO items from this note. "
            "Return only concrete tasks—things someone would actually do. "
            "Omit context, background, or metadata. "
            "Each item should be a brief imperative action. "
            "Return empty list if no clear action items exist.\n\n"
            f"Note content:\n{content}"
        )
        try:
            response = run_claude_pipe(prompt, NoteExtractResponse)
            items = response.items
        except ClaudePipeError:
            notify_sound(False)
            items = []

    if not items:
        console.print(f"[dim]No action items found in slot {slot}.[/dim]")
        return

    if len(items) == 1:
        console.print(f"\n  1. {items[0]}\n")
        if not prompt_yn("Add as TODO?"):
            console.print("[dim]Cancelled.[/dim]")
            return
        selected = items
    else:
        review_path = working_dir() / ".todo-candidates.md"
        buffer = _build_todo_buffer(content, set(items))
        review_path.write_text(buffer)
        mtime_before = review_path.stat().st_mtime
        editor = os.environ.get("EDITOR", "vi")
        subprocess.run([editor, str(review_path)])
        if not review_path.exists():
            console.print("[dim]Cancelled.[/dim]")
            return
        if review_path.stat().st_mtime == mtime_before:
            console.print("[dim]Cancelled.[/dim]")
            review_path.unlink(missing_ok=True)
            return
        raw = review_path.read_text()
        review_path.unlink(missing_ok=True)
        selected = [
            ln[2:].strip() for ln in raw.splitlines() if ln.startswith("- ") and ln[2:].strip()
        ]

    if not selected:
        console.print("[dim]No items added.[/dim]")
        return

    ensure_daily()
    ts = _datetime.now().strftime("%H:%M:%S")
    for item in selected:
        append_to_daily(f"- [{ts}] TODO: {item}")
    console.print(f"\n  Added {len(selected)} TODO(s).")
    notify_sound(True)


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
