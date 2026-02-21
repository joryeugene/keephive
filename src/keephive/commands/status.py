"""hive s / hive status: show status overview."""

from __future__ import annotations

import json
from datetime import date, timedelta

from keephive import __version__
from keephive.output import console
from keephive.storage import (
    NOTE_SLOT_COUNT,
    active_slot,
    count_daily_entries,
    count_stale_facts,
    daily_dir,
    due_recurring,
    ensure_dirs,
    get_meaningful_entries,
    guides_dir,
    hive_dir,
    memory_file,
    open_todos,
    slot_file,
    yesterday,
)


def _suggest_next() -> tuple[str, str]:
    """Compute highest-priority next action.

    Returns (command, reason) tuple. command="" means clean state.
    """
    import re

    # 1. Critical stale (>60d)
    mem = memory_file()
    critical_stale = 0
    if mem.exists():
        cutoff_60 = (date.today() - timedelta(days=60)).isoformat()
        for line in mem.read_text().splitlines():
            m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
            if m and m.group(1) < cutoff_60:
                critical_stale += 1
    if critical_stale > 0:
        s = "s" if critical_stale != 1 else ""
        return ("hive v", f"{critical_stale} fact{s} unverified 60+ days")

    # 2. Critical TODOs (>3d old)
    todos = open_todos()
    old_todos = 0
    if todos:
        cutoff_3d = (date.today() - timedelta(days=3)).isoformat()
        for d, _, _ in todos:
            if d < cutoff_3d:
                old_todos += 1
    if old_todos > 0:
        s = "s" if old_todos != 1 else ""
        return ("hive todo", f"{old_todos} TODO{s} older than 3 days")

    # 3. Pending rule suggestions
    pending_rules = hive_dir() / ".pending-rules.md"
    if pending_rules.exists() and pending_rules.read_text().strip():
        n = sum(1 for ln in pending_rules.read_text().splitlines() if ln.strip().startswith("- "))
        if n > 0:
            s = "s" if n != 1 else ""
            return ("hive rule review", f"{n} pending rule suggestion{s}")

    # 4. Memory bloat (>40 facts)
    fact_count = 0
    if mem.exists():
        fact_count = sum(1 for line in mem.read_text().splitlines() if line.startswith("- "))
    if fact_count > 40:
        return ("hive rf", f"{fact_count} facts in memory, consolidate")

    # 5. No entries today
    if count_daily_entries() == 0:
        return ("hive r", "no entries logged today")

    # 6. Stale facts (>30d)
    stale = count_stale_facts()
    if stale > 0:
        s = "s" if stale != 1 else ""
        return ("hive v", f"{stale} fact{s} unverified 30+ days")

    # 7. Due recurring tasks
    due = due_recurring()
    if due:
        s = "s" if len(due) != 1 else ""
        return ("hive todo", f"{len(due)} due recurring task{s}")

    # 8. TODOs piling up (>10)
    if todos and len(todos) > 10:
        return ("hive go todo", f"{len(todos)} open TODOs, triage session")

    # 9. No audit in 7+ days
    try:
        found_recent_audit = False
        for days_ago in range(7):
            day_str = (date.today() - timedelta(days=days_ago)).isoformat()
            log_path = daily_dir() / f"{day_str}.md"
            if log_path.exists():
                content = log_path.read_text()
                if "audit" in content.lower() or "quality pulse" in content.lower():
                    found_recent_audit = True
                    break
        if not found_recent_audit:
            return ("hive a", "no audit in 7+ days")
    except Exception:
        pass

    # 10. Clean state
    return ("", "looking good")


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
        working_lines = sum(1 for line in text.splitlines() if line.strip())
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
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            disk_usage = r.stdout.split()[0]
    except Exception:
        pass

    # Health indicators
    from keephive.health import check_anthropic_memory, health_summary

    hooks_ok, mcp_ok, data_ok = health_summary()
    anthropic_mem = check_anthropic_memory()

    if json_mode:
        print(
            json.dumps(
                {
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
                    "anthropic_memory": anthropic_mem,
                }
            )
        )
        return

    console.print()
    console.print(f"[bold]keephive[/bold] v{__version__}")

    # Health indicators
    def _dot(ok: bool, label: str) -> str:
        return f"[ok]\u25cf[/ok] {label}" if ok else f"[dim]\u25cb[/dim] {label}"

    health_parts = [_dot(hooks_ok, "hooks"), _dot(mcp_ok, "mcp"), _dot(data_ok, "data")]
    if anthropic_mem == "active":
        health_parts.append("[dim]anthropic-mem[/dim]")
    health_line = f"  {'  '.join(health_parts)}"
    console.print(health_line)
    if not all([hooks_ok, mcp_ok, data_ok]):
        console.print("  Run: [bold]hive setup[/bold]")
    console.print()

    # Stats line
    verified_ok = total_verified - stale
    parts = []
    if total_verified > 0:
        if stale > 0:
            parts.append(
                f"[bold]{total_verified} facts[/bold] ({verified_ok} ok, [warn]{stale} stale[/warn])"
            )
        else:
            parts.append(f"[bold]{total_verified} facts[/bold] ({verified_ok} ok)")
    parts.append(f"{today_entries} today")
    parts.append(f"{yesterday_entries} yesterday")
    parts.append(f"{guide_count} guides")
    parts.append(disk_usage)
    console.print(f"  {' | '.join(parts)}")

    # Activity line (commands / streak / hourly sparkline)
    try:
        from keephive.commands.stats import _calculate_streak, _hourly_sparkline
        from keephive.storage import read_stats

        stats = read_stats()
        days_data = stats.get("days", {})
        today_str = date.today().isoformat()
        today_data_st = days_data.get(today_str, {})
        today_cmds = sum(today_data_st.get("commands", {}).values())
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        week_cmds = sum(
            sum(dd.get("commands", {}).values()) for ds, dd in days_data.items() if ds >= week_ago
        )
        curr_streak, _ = _calculate_streak(days_data)
        activity_parts = [
            f"[bold]{today_cmds}[/bold] cmds today",
            f"{week_cmds} this week",
        ]
        if curr_streak > 0:
            activity_parts.append(f"{curr_streak}d streak")
        today_hours = today_data_st.get("hours", {})
        if today_hours:
            activity_parts.append(_hourly_sparkline(today_hours))
        console.print(f"  {' \u00b7 '.join(activity_parts)}")
    except Exception:
        pass  # Stats unavailable, skip gracefully

    console.print()

    # Stale warning
    if stale > 0:
        console.print(f"  [warn]{stale} stale fact(s)[/warn]  ->  [bold]hive v[/bold]")
        console.print()

    # Quality Pulse score (moved from sessionstart injection)
    try:
        from keephive.commands.audit import (
            _analyze_cleaner,
            _analyze_strategist,
            _analyze_vault,
            _check_previous_play,
            _compute_score,
        )

        vault = _analyze_vault()
        cleaner = _analyze_cleaner()
        strategist = _analyze_strategist()
        pulse_score = _compute_score(vault, cleaner, strategist)

        if pulse_score < 70:
            console.print(
                f"  [warn]Quality Pulse: {pulse_score}/100[/warn]  ->  [bold]hive audit[/bold]"
            )
            console.print()

        prev_play = _check_previous_play()
        if prev_play and not prev_play["completed"] and prev_play["age_days"] >= 2:
            console.print(
                f"  [info]Unfinished Play ({prev_play['date']}): {prev_play['action']}[/info]"
            )
            console.print()
    except Exception:
        pass

    # Guide update notification (moved from sessionstart injection)
    try:
        from keephive.commands.setup import check_bundled_updates as _cbu

        _stale_guides = _cbu()
        if _stale_guides > 0:
            console.print(
                f"  [info]{_stale_guides} bundled guide(s) have updates[/info]"
                "  ->  [bold]hive setup[/bold]"
            )
            console.print()
    except Exception:
        pass

    # Memory accumulation warnings (moved from sessionstart injection)
    if mem.exists():
        from keephive.hooks.sessionstart import _accumulation_warnings

        mem_content = mem.read_text()
        acc_warnings = _accumulation_warnings(mem_content)
        for w in acc_warnings:
            console.print(f"  [warn]{w}[/warn]")
        if acc_warnings:
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
        console.print("    -> [bold]hive rf apply[/bold]")
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
            console.print("    [warn]Consider: hive todo done <pat> | hive dr[/warn]")
        console.print()

    # Pending rule suggestions nudge
    pending_rules_path = hive_dir() / ".pending-rules.md"
    if pending_rules_path.exists() and pending_rules_path.read_text().strip():
        n = sum(
            1 for ln in pending_rules_path.read_text().splitlines() if ln.strip().startswith("- ")
        )
        if n > 0:
            console.print(
                f"  [yellow]⚡ {n} rule suggestion(s) pending[/yellow]  →  hive rule review"
            )
            console.print()

    # Due recurring tasks
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

    # Note slot indicator (single location, enhanced format)
    current_slot = active_slot()
    slot_path = slot_file(current_slot)
    if slot_path.exists() and slot_path.read_text().strip():
        text = slot_path.read_text()
        words = len(text.split())
        flat = text.replace("\n", " ")
        preview = flat[:40] + ("..." if len(flat) > 40 else "")
        console.print(
            f'  [info]Active draft: slot {current_slot} · "{preview}" ({words} words)[/info]'
            f"  ->  [bold]hive nc[/bold]"
        )
        # Show slot bar if multiple slots have content
        filled = sum(
            1
            for n in range(1, NOTE_SLOT_COUNT + 1)
            if slot_file(n).exists() and slot_file(n).read_text().strip()
        )
        if filled > 1:
            from keephive.commands.note import _slot_bar

            console.print(f"  {_slot_bar()}")
        console.print()

    # Next action suggestion
    next_cmd, next_reason = _suggest_next()
    if next_cmd:
        console.print(f"  [bold]Next:[/bold] [info]{next_cmd}[/info]  ({next_reason})")
    else:
        console.print(f"  [bold]Next:[/bold] [ok]{next_reason}[/ok]")
    console.print()

    # Contextual footer
    todo_count = len(todos) if todos else 0
    if stale > 0:
        console.print("  [dim]hive v | hive session verify (interactive) | hive help[/dim]")
    elif todo_count > 5:
        console.print("  [dim]hive session todo (triage) | hive dr (duplicates) | hive help[/dim]")
    else:
        console.print(
            "  [dim]hive go (session) | hive l (log) | hive rf (reflect) | hive help[/dim]"
        )
