"""hive todo and hive t: TODO lifecycle management."""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime

from keephive.output import console
from keephive.storage import (
    append_to_daily,
    ensure_daily,
    open_todos,
    recent_dones,
    working_dir,
)


def cmd_todo(args: list[str]) -> None:
    """List open TODOs, mark one as done, or manage recurring tasks."""
    if args and args[0] == "done":
        if len(args) > 1 and args[1] == "undo":
            _todo_undo(" ".join(args[2:]))
            return
        _todo_done(" ".join(args[1:]))
        return

    if args and args[0] == "undo":
        _todo_undo(" ".join(args[1:]))
        return

    if args and args[0] == "repeat":
        from keephive.commands.recurring import cmd_recurring
        cmd_recurring(args[1:])
        return

    todos = open_todos()

    # Show due recurring tasks first
    from keephive.storage import due_recurring
    due = due_recurring()
    if due:
        console.print("[bold]Due Recurring:[/bold]")
        for freq, text, overdue in due:
            over_s = f"+{overdue}d" if overdue > 0 else "due"
            console.print(f"  \\[{freq}] \\[{over_s}] {text}")
        console.print()

    # Show open TODOs
    if not todos:
        console.print("[bold]Open TODOs:[/bold]")
        console.print("  No open TODOs")
    else:
        t = date.today()
        console.print("[bold]Open TODOs:[/bold]")
        for d, ts, text in reversed(todos):
            try:
                td = date.fromisoformat(d)
                age = (t - td).days
                if age == 0:
                    age_s = "today"
                elif age == 1:
                    age_s = "1d"
                else:
                    age_s = f"{age}d"
            except ValueError:
                age_s = "?"
            time_part = f" {ts}" if ts else ""
            console.print(f"  \\[{age_s}{time_part}] {text}")

    # Contextual hints
    console.print()
    console.print("  [dim]td <pat>[/dim] done  |  [dim]td undo[/dim]  |  [dim]t <text>[/dim] add  |  [dim]e todo[/dim]  |  [dim]todo repeat daily \"..[/dim]\" recurring")

    # Show recent completions
    dones = recent_dones(days=3)
    if dones:
        console.print()
        console.print("[bold]Recently Done:[/bold]")
        for d, text in reversed(dones[-5:]):
            age = (date.today() - date.fromisoformat(d)).days
            if age == 0:
                age_s = "today"
            elif age == 1:
                age_s = "1d"
            else:
                age_s = f"{age}d"
            console.print(f"  [dim]\\[{age_s}] {text}[/dim]")


def cmd_t(args: list[str]) -> None:
    """Quick TODO shortcut: hive t "fix the thing" logs TODO: fix the thing.

    Subcommands:
      hive t done <pat>  /  hive t d <pat>   Mark TODO matching pattern as done.
    """
    if not args:
        cmd_todo([])
        return

    if args[0] in ("done", "d"):
        if len(args) > 1 and args[1] == "undo":
            _todo_undo(" ".join(args[2:]))
            return
        _todo_done(" ".join(args[1:]))
        return

    # Import the remember command to reuse it
    from keephive.commands.remember import cmd_remember
    cmd_remember([f"TODO: {' '.join(args)}"])

    try:
        from keephive.storage import track_event
        track_event("meta", "todos_created")
    except Exception:
        pass


def edit_todos() -> None:
    """Open TODOs in $EDITOR. Removals become DONE, additions become new TODOs."""
    todos = open_todos()
    edit_path = working_dir() / ".todo-edit.md"

    # Build editable file
    lines = [
        "# Open TODOs",
        "# Remove lines to mark done. Add lines for new TODOs.",
        "",
    ]
    before = set()
    for _, _, text in todos:
        lines.append(f"- {text}")
        before.add(text)

    edit_path.write_text("\n".join(lines) + "\n")

    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(edit_path)])

    # Parse result
    after = set()
    after_list: list[str] = []
    for line in edit_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("- "):
            item = line[2:].strip()
            if item:
                after.add(item)
                after_list.append(item)

    # Diff
    removed = before - after
    added = after - before

    if not removed and not added:
        console.print("  No changes.")
        edit_path.unlink(missing_ok=True)
        return

    ensure_daily()
    ts = datetime.now().strftime("%H:%M:%S")

    for text in sorted(removed):
        append_to_daily(f"- [{ts}] DONE: {text}")
        console.print(f"  [ok]Done:[/ok] {text}")

    for text in after_list:
        if text in added:
            append_to_daily(f"- [{ts}] TODO: {text}")
            console.print(f"  [bold]Added:[/bold] {text}")

    edit_path.unlink(missing_ok=True)

    try:
        from keephive.storage import track_event
        if removed:
            track_event("meta", "todos_completed")
        if added:
            track_event("meta", "todos_created")
    except Exception:
        pass


def _todo_undo(pattern: str) -> None:
    """Reopen the most recent completed TODO matching pattern."""
    from keephive.storage import undo_done
    result = undo_done(pattern)
    if result:
        console.print(f"  [ok]Reopened:[/ok] {result}")
    else:
        if pattern:
            console.print(f'  [warn]No completed TODO matching[/warn] "{pattern}"')
        else:
            console.print("  [warn]No completed TODO to undo[/warn]")
        dones = recent_dones(days=3)
        if dones:
            console.print("  [dim]Recent completions:[/dim]")
            for _, text in dones[-3:]:
                console.print(f"    {text}")


def cmd_td(args: list[str]) -> None:
    """hive td [pat]: mark TODO matching pattern done. td undo [pat]: reopen. No args: list todos."""
    if args:
        if args[0] == "undo":
            _todo_undo(" ".join(args[1:]))
        else:
            _todo_done(" ".join(args))
    else:
        cmd_todo([])


def _todo_done(pattern: str) -> None:
    """Mark first open TODO matching pattern as done."""
    if not pattern:
        console.print("[err]Error: specify a pattern to match[/err]")
        console.print("Usage: hive todo done \"pattern\"")
        return

    todos = open_todos()
    match = None
    for _, _, text in todos:
        if pattern.lower() in text.lower():
            match = text
            break

    if not match:
        # Try recurring tasks before giving up
        from keephive.commands.recurring import _recurring_done
        if _recurring_done(pattern):
            return
        console.print(f"  [warn]No matching TODO for[/warn] \"{pattern}\"")
        console.print("  [dim]Open TODOs:[/dim]")
        if todos:
            for _, _, text in todos[:5]:
                console.print(f"    {text}")
        else:
            console.print("    (none)")
        return

    ensure_daily()
    ts = datetime.now().strftime("%H:%M:%S")
    append_to_daily(f"- [{ts}] DONE: {match}")
    console.print(f"  [ok]Completed:[/ok] {match}")

    try:
        from keephive.storage import track_event
        track_event("meta", "todos_completed")
    except Exception:
        pass
