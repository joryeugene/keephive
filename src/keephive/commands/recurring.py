"""hive todo repeat: recurring task management."""

from __future__ import annotations

import re

from keephive.output import console
from keephive.storage import (
    due_recurring,
    ensure_dirs,
    is_valid_freq,
    mark_recurring_done,
    parse_freq,
    recurring_file,
    safe_read_text,
)

# Regex that matches any valid frequency tag in brackets
_TASK_RE = re.compile(r"^- \[([^\]]+)\]\s*(.*)")


def cmd_recurring(args: list[str]) -> None:
    """Manage recurring tasks.

    Usage:
        hive todo repeat list
        hive todo repeat daily "Check open TODOs"
        hive todo repeat weekly "Review stale facts: hive v"
        hive todo repeat monthly "Reflect on daily logs: hive rf"
        hive todo repeat 2d "Run test suite"
        hive todo repeat 12h "Check build status"
        hive todo repeat rm "stale"
        hive todo repeat done "stale"
    """
    if not args:
        _recurring_list()
        return

    subcmd = args[0]
    rest = " ".join(args[1:])

    if subcmd == "list":
        _recurring_list()
    elif subcmd == "rm":
        _recurring_rm(rest)
    elif subcmd == "done":
        if not _recurring_done(rest):
            if not rest:
                console.print("[err]Error: specify a pattern to match[/err]")
            else:
                console.print(f'[warn]No recurring task matching "{rest}"[/warn]')
    elif is_valid_freq(subcmd):
        _recurring_add(subcmd, rest)
    else:
        console.print(f"[err]Unknown frequency or subcommand:[/err] {subcmd}")
        console.print("Usage: hive todo repeat [list|daily|weekly|monthly|Nd|Nh|rm|done] [text]")
        console.print("  Examples: daily, weekly, 2d (every 2 days), 12h (every 12 hours)")


def _ensure_recurring() -> None:
    """Create recurring.md if it doesn't exist."""
    ensure_dirs()
    rf = recurring_file()
    if not rf.exists():
        rf.write_text("# Recurring Tasks\n\n## Last Completed\n\n")


def _freq_display(freq: str) -> str:
    """Human-readable display for a frequency."""
    aliases = {"daily": "every day", "weekly": "every 7 days", "monthly": "every 30 days"}
    if freq in aliases:
        return aliases[freq]
    try:
        days = parse_freq(freq)
        if days < 1:
            hours = int(days * 24)
            return f"every {hours}h"
        if days == int(days):
            return f"every {int(days)}d"
        return f"every {days:.1f}d"
    except ValueError:
        return freq


def _recurring_list() -> None:
    """Show all recurring tasks with due status."""
    rf = recurring_file()
    if not rf.exists():
        console.print("[bold]Recurring Tasks:[/bold]")
        console.print("  [dim](none)[/dim]")
        console.print()
        console.print('  -> [dim]hive todo repeat daily "task"[/dim]  or  [dim]hive todo repeat 2d "task"[/dim]')
        return

    content = safe_read_text(rf)
    tasks: list[tuple[str, str]] = []
    for line in content.splitlines():
        m = _TASK_RE.match(line)
        if m and is_valid_freq(m.group(1)):
            tasks.append((m.group(1), m.group(2).strip()))

    console.print("[bold]Recurring Tasks:[/bold]")
    if not tasks:
        console.print("  [dim](none)[/dim]")
    else:
        due = due_recurring()
        due_texts = {text.lower() for _, text, _ in due}
        for freq, text in tasks:
            due_marker = " [warn](DUE)[/warn]" if text.lower() in due_texts else ""
            display = _freq_display(freq)
            console.print(f"  \\[{freq}] {text}  [dim]({display}){due_marker}[/dim]")

    console.print()
    console.print('  -> [dim]hive todo repeat daily "task"[/dim] to add  |  [dim]hive todo repeat done "pattern"[/dim] to mark done')


def _recurring_add(freq: str, text: str) -> None:
    """Add a recurring task."""
    if not text:
        console.print("[err]Error: specify task text[/err]")
        console.print(f'Usage: hive todo repeat {freq} "task description"')
        return

    _ensure_recurring()
    rf = recurring_file()
    content = safe_read_text(rf)

    # Insert before "## Last Completed" section
    lines = content.splitlines(keepends=True)
    insert_idx = len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("## Last Completed"):
            insert_idx = i
            break

    new_line = f"- [{freq}] {text}\n"
    lines.insert(insert_idx, new_line)
    rf.write_text("".join(lines))
    display = _freq_display(freq)
    console.print(f"[ok]Added[/ok] \\[{freq}] {text}  ({display})")


def _recurring_rm(pattern: str) -> None:
    """Remove a recurring task matching pattern."""
    if not pattern:
        console.print("[err]Error: specify a pattern to match[/err]")
        return

    rf = recurring_file()
    if not rf.exists():
        console.print("[warn]No recurring tasks[/warn]")
        return

    content = safe_read_text(rf)
    lines = content.splitlines(keepends=True)
    new_lines = []
    removed = None

    for line in lines:
        m = _TASK_RE.match(line)
        if m and not removed and is_valid_freq(m.group(1)) and pattern.lower() in m.group(2).lower():
            removed = m.group(2).strip()
            continue
        new_lines.append(line)

    if removed:
        rf.write_text("".join(new_lines))
        console.print(f"[ok]Removed[/ok] {removed}")
    else:
        console.print(f'[warn]No recurring task matching "{pattern}"[/warn]')


def _recurring_done(pattern: str) -> bool:
    """Mark a recurring task as done (updates last completed date/time).

    Returns True if a matching recurring task was found and marked done.
    """
    if not pattern:
        return False

    rf = recurring_file()
    if not rf.exists():
        return False

    result = mark_recurring_done(pattern)
    if not result:
        return False

    match_text, _ = result
    console.print(f"[ok]Done[/ok] {match_text} (next due per schedule)")
    return True
