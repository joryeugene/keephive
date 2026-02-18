"""hive todo and hive t: TODO lifecycle management."""

from __future__ import annotations

from datetime import date, datetime

from keephive.output import console
from keephive.storage import (
    append_to_daily,
    ensure_daily,
    open_todos,
    recent_dones,
)


def cmd_todo(args: list[str]) -> None:
    """List open TODOs, mark one as done, or manage recurring tasks."""
    if args and args[0] == "done":
        _todo_done(" ".join(args[1:]))
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
    console.print("  [dim]hive todo done <pat>[/dim] complete  |  [dim]hive t <text>[/dim] add  |  [dim]hive todo repeat daily \"text\"[/dim] recurring")

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
    """Quick TODO shortcut: hive t "fix the thing" logs TODO: fix the thing."""
    if not args:
        cmd_todo([])
        return

    # Import the remember command to reuse it
    from keephive.commands.remember import cmd_remember
    cmd_remember([f"TODO: {' '.join(args)}"])

    try:
        from keephive.storage import track_event
        track_event("meta", "todos_created")
    except Exception:
        pass


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
