"""hive s / hive status: show status overview."""

from __future__ import annotations

import json
from datetime import date

from keephive import __version__
from keephive.output import console
from keephive.storage import (
    active_slot,
    count_daily_entries,
    count_stale_facts,
    ensure_dirs,
    get_meaningful_entries,
    guides_dir,
    hive_dir,
    memory_file,
    NOTE_SLOT_COUNT,
    open_todos,
    slot_file,
    yesterday,
)


def cmd_status(args: list[str]) -> None:
    ensure_dirs()
    json_mode = "--json" in args

    # Gather stats
    mem = memory_file()
    working_lines = 0
    total_verified = 0
    stale = 0

    if mem.exists():
        import re
        text = mem.read_text()
        working_lines = sum(1 for l in text.splitlines() if l.strip())
        total_verified = len(re.findall(r"\[verified:", text))
        stale = count_stale_facts()

    guide_count = sum(1 for _ in guides_dir().glob("*.md")) if guides_dir().exists() else 0
    today_entries = count_daily_entries()
    yesterday_entries = count_daily_entries(yesterday())

    # Disk usage
    disk_usage = "?"
    try:
        import subprocess
        r = subprocess.run(
            ["du", "-sh", str(hive_dir())],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            disk_usage = r.stdout.split()[0]
    except Exception:
        pass

    # Health indicators
    from keephive.health import health_summary
    hooks_ok, mcp_ok, data_ok = health_summary()

    if json_mode:
        print(json.dumps({
            "version": __version__,
            "working_lines": working_lines,
            "verified_facts": total_verified,
            "stale_facts": stale,
            "guides": guide_count,
            "today_entries": today_entries,
            "yesterday_entries": yesterday_entries,
            "disk_usage": disk_usage,
            "hive_dir": str(hive_dir()),
            "hooks_ok": hooks_ok,
            "mcp_ok": mcp_ok,
            "data_ok": data_ok,
        }))
        return

    console.print()
    console.print(f"[bold]keephive[/bold] v{__version__}")

    # Health indicators
    def _dot(ok: bool, label: str) -> str:
        return f"[ok]\u25cf[/ok] {label}" if ok else f"[dim]\u25cb[/dim] {label}"

    health_line = f"  {_dot(hooks_ok, 'hooks')}  {_dot(mcp_ok, 'mcp')}  {_dot(data_ok, 'data')}"
    console.print(health_line)
    if not all([hooks_ok, mcp_ok, data_ok]):
        console.print("  Run: [bold]hive setup[/bold]")
    console.print()

    # Stats line
    verified_ok = total_verified - stale
    parts = []
    if total_verified > 0:
        if stale > 0:
            parts.append(f"[bold]{total_verified} facts[/bold] ({verified_ok} ok, [warn]{stale} stale[/warn])")
        else:
            parts.append(f"[bold]{total_verified} facts[/bold] ({verified_ok} ok)")
    parts.append(f"{today_entries} today")
    parts.append(f"{yesterday_entries} yesterday")
    parts.append(f"{guide_count} guides")
    parts.append(disk_usage)
    console.print(f"  {' | '.join(parts)}")
    console.print()

    # Stale warning
    if stale > 0:
        console.print(f"  [warn]{stale} stale fact(s)[/warn]  ->  [bold]hive v[/bold]")
        console.print()

    # Reflect analysis nudge
    from keephive.commands.reflect import get_pending_analysis
    pending = get_pending_analysis()
    if pending:
        add_count, contra_count = pending
        parts_nudge = []
        if add_count:
            parts_nudge.append(f"{add_count} addition{'s' if add_count != 1 else ''}")
        if contra_count:
            parts_nudge.append(f"{contra_count} contradiction{'s' if contra_count != 1 else ''}")
        console.print(f"  [info]\\[reflect] {', '.join(parts_nudge)} ready[/info]")
        console.print(f"    -> [bold]hive rf apply[/bold]")
        console.print()

    # Open TODOs
    todos = open_todos()
    if todos:
        t = date.today()
        shown = list(reversed(todos[-3:]))
        console.print(f"  [bold]{len(todos)} open TODO(s):[/bold]")
        for d, _ts, text in shown:
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
            console.print(f"    [info]\\[{age_s}] {text}[/info]")
        if len(todos) > 3:
            console.print(f"    [dim]... and {len(todos) - 3} more (hive todo)[/dim]")
        if len(todos) > 10:
            console.print(f"    [warn]Consider: hive todo done <pat> | hive dr[/warn]")
        console.print()

    # Due recurring tasks
    from keephive.storage import due_recurring
    due = due_recurring()
    if due:
        console.print(f"  [bold]{len(due)} due recurring task(s):[/bold]")
        for freq, text, overdue in due:
            over_s = f"+{overdue}d" if overdue > 0 else "due"
            console.print(f"    [warn]\\[{freq}] \\[{over_s}] {text}[/warn]")
        console.print()

    # Today's entries
    entries = get_meaningful_entries()
    if entries:
        console.print("  [bold]Today:[/bold]")
        for e in reversed(entries):
            console.print(e)
        console.print()
    else:
        console.print("  [bold]Today:[/bold] [dim](no entries yet)[/dim]")
        console.print()

    # Lookback when today is thin
    if today_entries < 3 and yesterday_entries > 0:
        yday = yesterday()
        yday_entries = get_meaningful_entries(yday, limit=5)
        if yday_entries:
            console.print(f"  [bold]Yesterday[/bold] ({yday}):")
            for e in reversed(yday_entries):
                console.print(e)
            console.print()

    # Note slot indicator
    current_slot = active_slot()
    slot_path = slot_file(current_slot)
    if slot_path.exists() and slot_path.read_text().strip():
        text = slot_path.read_text()
        lines = sum(1 for l in text.splitlines() if l.strip())
        size = len(text.encode())
        console.print(f"  [info]Note \\[{current_slot}] ready ({lines}L, {size}B)[/info]  ->  [bold]hive nc[/bold] to copy")
        # Show slot bar if multiple slots have content
        filled = sum(1 for n in range(1, NOTE_SLOT_COUNT + 1) if slot_file(n).exists() and slot_file(n).read_text().strip())
        if filled > 1:
            from keephive.commands.note import _slot_bar
            console.print(f"  {_slot_bar()}")
        console.print()

    # Contextual footer
    todo_count = len(todos) if todos else 0
    if stale > 0:
        console.print("  [dim]hive v | hive session verify (interactive) | hive help[/dim]")
    elif todo_count > 5:
        console.print("  [dim]hive session todo (triage) | hive dr (duplicates) | hive help[/dim]")
    else:
        console.print("  [dim]hive go (session) | hive l (log) | hive rf (reflect) | hive help[/dim]")
