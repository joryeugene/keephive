"""hive stats: usage statistics with per-project breakdown, streaks, and sparklines."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from rich.table import Table

from keephive.output import console
from keephive.storage import (
    count_log_entries_with_prefix,
    last_log_entry_with_prefix,
    parse_date_arg,
    read_stats,
)


def cmd_stats(args: list[str]) -> None:
    """Display usage statistics."""
    # Parse args
    output_json = "--json" in args
    project_filter = ""
    date_filter = ""

    i = 0
    while i < len(args):
        if args[i] == "--json":
            i += 1
            continue
        if args[i] == "-p" and i + 1 < len(args):
            project_filter = args[i + 1]
            i += 2
            continue
        if not args[i].startswith("-"):
            date_filter = args[i]
        i += 1

    data = read_stats()

    if output_json:
        if project_filter:
            result = _project_data(data, project_filter, date_filter)
        elif date_filter:
            result = _day_data(data, date_filter)
        else:
            result = data
        print(json.dumps(result, indent=2))
        return

    if project_filter:
        _display_project(data, project_filter, date_filter)
    elif date_filter:
        _display_day(data, date_filter)
    else:
        _display_full(data)


# ---- Aggregation helpers ----

_parse_date_arg = parse_date_arg


def _sum_counters(days_data: dict, category: str) -> dict[str, int]:
    """Sum counters across all days for a category."""
    totals: dict[str, int] = defaultdict(int)
    for day_data in days_data.values():
        if category in day_data:
            for name, count in day_data[category].items():
                totals[name] += count
    return dict(totals)


def _sum_sources(days_data: dict) -> dict[str, int]:
    """Sum source counters across all days."""
    totals: dict[str, int] = defaultdict(int)
    for day_data in days_data.values():
        for src, count in day_data.get("sources", {}).items():
            totals[src] += count
    return dict(totals)


def _count_category(day_data: dict, category: str) -> int:
    """Count total events in a category for one day."""
    return sum(day_data.get(category, {}).values())


def _all_projects(days_data: dict) -> dict[str, dict]:
    """Aggregate project data across all days."""
    projects: dict[str, dict] = {}
    for day_str, day_data in sorted(days_data.items()):
        for proj_key, proj_data in day_data.get("projects", {}).items():
            if proj_key not in projects:
                projects[proj_key] = {
                    "commands": 0,
                    "sessions": 0,
                    "days_active": 0,
                    "first_seen": day_str,
                    "last_seen": day_str,
                    "by_command": defaultdict(int),
                    "active_days": set(),
                    "daily_counts": {},
                    "sources": defaultdict(int),
                }
            p = projects[proj_key]
            p["commands"] += proj_data.get("commands", 0)
            p["sessions"] += proj_data.get("sessions", 0)
            p["last_seen"] = day_str
            p["active_days"].add(day_str)
            p["daily_counts"][day_str] = proj_data.get("commands", 0)
            for cmd, cnt in proj_data.get("by_command", {}).items():
                p["by_command"][cmd] += cnt

    # Finalize
    for p in projects.values():
        p["days_active"] = len(p["active_days"])
        del p["active_days"]
        p["by_command"] = dict(p["by_command"])
        p["sources"] = dict(p["sources"])

    return projects


def _match_project(projects: dict, pattern: str) -> str | None:
    """Find a project key matching a pattern (exact > suffix > substring)."""
    keys = list(projects.keys())
    # Exact match
    if pattern in keys:
        return pattern
    # Suffix match (e.g. "keephive" matches "~/Documents/GitHub/keephive")
    for k in keys:
        if k.endswith("/" + pattern) or k.endswith(pattern):
            return k
    # Substring match
    for k in keys:
        if pattern.lower() in k.lower():
            return k
    return None


def _calculate_streak(days_data: dict) -> tuple[int, int]:
    """Calculate current streak and longest streak in days.

    Returns (current_streak, longest_streak).
    """
    if not days_data:
        return 0, 0

    active_dates = set()
    for day_str in days_data:
        try:
            active_dates.add(date.fromisoformat(day_str))
        except ValueError:
            continue

    if not active_dates:
        return 0, 0

    sorted_dates = sorted(active_dates)

    # Longest streak
    longest = 1
    current = 1
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1

    # Current streak (counting back from today)
    today = date.today()
    curr_streak = 0
    check = today
    while check in active_dates:
        curr_streak += 1
        check -= timedelta(days=1)

    return curr_streak, longest


def _sparkline(daily_counts: dict[str, int], days: int = 7) -> list[tuple[str, int]]:
    """Build a sparkline of daily activity for the last N days.

    Returns list of (date_label, count) tuples.
    """
    result = []
    today = date.today()
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        day_str = d.isoformat()
        label = d.strftime("%b %d")
        count = daily_counts.get(day_str, 0)
        result.append((label, count))
    return result


def _bar(value: int, max_value: int, width: int = 20) -> str:
    """Render a simple bar chart character."""
    if max_value == 0:
        return ""
    chars = max(1, int(value / max_value * width)) if value > 0 else 0
    return "\u2588" * chars


def _relative_day(day_str: str) -> str:
    """Convert a date string to a relative label."""
    try:
        d = date.fromisoformat(day_str)
    except ValueError:
        return day_str
    delta = (date.today() - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return f"{delta}d ago"


# ---- Data extraction ----


def _day_data(data: dict, date_arg: str) -> dict:
    """Extract data for a specific day."""
    day_str = _parse_date_arg(date_arg)
    return data.get("days", {}).get(day_str, {})


def _project_data(data: dict, project_filter: str, date_filter: str = "") -> dict:
    """Extract project-specific data."""
    projects = _all_projects(data.get("days", {}))
    key = _match_project(projects, project_filter)
    if not key:
        return {
            "error": f"No project matching '{project_filter}'",
            "available": list(projects.keys()),
        }
    proj = projects[key]
    proj["path"] = key
    if date_filter:
        day_str = _parse_date_arg(date_filter)
        day_data = data.get("days", {}).get(day_str, {})
        proj_day = day_data.get("projects", {}).get(key, {})
        proj["day"] = day_str
        proj["day_data"] = proj_day
    return proj


# ---- Display functions ----


def _display_full(data: dict) -> None:
    """Display full stats overview."""
    days_data = data.get("days", {})

    if not days_data:
        console.print("[dim]No stats recorded yet.[/dim]")
        console.print("  Stats are tracked automatically as you use hive commands.")
        return

    today_str = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    # Today / This week / All time summary
    today_data = days_data.get(today_str, {})
    today_cmds = _count_category(today_data, "commands")

    week_cmds = 0
    all_cmds = 0

    for day_str, day_data in days_data.items():
        cmds = _count_category(day_data, "commands")
        all_cmds += cmds
        if day_str >= week_ago:
            week_cmds += cmds

    curr_streak, longest_streak = _calculate_streak(days_data)

    # Single-line header
    console.print(
        f"[bold]keephive[/bold]  "
        f"today [bold]{today_cmds}[/bold] cmds  ·  "
        f"week [bold]{week_cmds}[/bold]  ·  "
        f"all time [bold]{all_cmds}[/bold]  ·  "
        f"streak [bold]{curr_streak}d[/bold] (best: {longest_streak}d)"
    )

    # 7-day activity chart
    daily_cmd_counts: dict[str, int] = {}
    for i in range(6, -1, -1):
        d = date.today() - timedelta(days=i)
        day_s = d.isoformat()
        dd = days_data.get(day_s, {})
        daily_cmd_counts[day_s] = _count_category(dd, "commands")

    spark = _sparkline(daily_cmd_counts, days=7)
    max_c = max((c for _, c in spark), default=1) or 1

    console.print()
    console.print("[dim]Activity · last 7 days[/dim]")
    for i, (label, count) in enumerate(spark):
        bar = _bar(count, max_c, width=20) if count > 0 else ""
        bar_display = bar if bar else "─"
        today_marker = "  ← today" if i == len(spark) - 1 else ""
        # Get weekday abbreviation from label (e.g. "Feb 13" -> "Thu")
        try:
            d = date.today() - timedelta(days=len(spark) - 1 - i)
            day_name = d.strftime("%a")
        except Exception:
            day_name = "   "
        console.print(f"  {label}  {day_name:<3}  {bar_display:<20}  {count:>3}{today_marker}")

    # Two-column: top commands (left) + sources + hooks (right)
    commands = _sum_counters(days_data, "commands")
    sources = _sum_sources(days_data)
    hooks = _sum_counters(days_data, "hooks")

    if commands or sources or hooks:
        grid = Table.grid(padding=(0, 4))
        grid.add_column()
        grid.add_column()

        left: list[str] = ["[dim]Top Commands[/dim]"]
        max_cmd = max(commands.values(), default=1) or 1
        for name, count in sorted(commands.items(), key=lambda x: -x[1])[:8]:
            bar = _bar(count, max_cmd, width=12)
            left.append(f"  {name:<12}  {count:>4}  {bar}")

        right: list[str] = ["[dim]Sources[/dim]"]
        total_src = sum(sources.values()) or 1
        for src, count in sorted(sources.items(), key=lambda x: -x[1]):
            pct = int(count / total_src * 100)
            right.append(f"  {src.replace('_', ' '):<10}  {pct:>3}%")
        if hooks:
            right.append("")
            right.append("[dim]Hooks[/dim]")
            for name, count in sorted(hooks.items(), key=lambda x: -x[1])[:4]:
                right.append(f"  {name:<16}  {count:>4}")

        max_rows = max(len(left), len(right))
        left += [""] * (max_rows - len(left))
        right += [""] * (max_rows - len(right))
        for l_row, r_row in zip(left, right):
            grid.add_row(l_row, r_row)

        console.print()
        console.print(grid)

    # Projects: 1 line each
    projects = _all_projects(days_data)
    if projects:
        console.print()
        console.print("[dim]Projects[/dim]")
        for key, proj in sorted(projects.items(), key=lambda x: -x[1]["commands"]):
            last = _relative_day(proj["last_seen"])
            console.print(
                f"  {key}  [bold]{proj['commands']}[/bold] cmds · "
                f"{proj['sessions']} sessions · {proj['days_active']}d active · last: {last}"
            )

    # Quality section
    meta = _sum_counters(days_data, "meta")


    standup_count = count_log_entries_with_prefix("STANDUP:")
    health_last = last_log_entry_with_prefix("HEALTH:")

    accepted = meta.get("insights_accepted", 0)
    dismissed = meta.get("insights_dismissed", 0)
    todos_done = meta.get("todos_completed", 0)

    quality_lines = []
    if accepted or dismissed or todos_done:
        quality_lines.append(
            f"  insights: {accepted} kept · {dismissed} dismissed    todos: {todos_done} completed"
        )
    if standup_count or health_last:
        parts_q = []
        if standup_count:
            parts_q.append(f"standups: {standup_count} logged")
        if health_last:
            parts_q.append(f"health: {health_last}")
        quality_lines.append("  " + "    ".join(parts_q))
    quality_lines.append(f"  streak: {curr_streak}d current · {longest_streak}d best")

    console.print()
    console.print("[dim]Quality[/dim]")
    for line in quality_lines:
        console.print(line)


def _display_day(data: dict, date_arg: str) -> None:
    """Display stats for a specific day."""
    day_str = _parse_date_arg(date_arg)
    day_data = data.get("days", {}).get(day_str, {})

    if not day_data:
        console.print(f"[dim]No stats for {day_str}[/dim]")
        return

    cmds = _count_category(day_data, "commands")
    hooks = _count_category(day_data, "hooks")
    projects = len(day_data.get("projects", {}))

    console.print(f"[bold]keephive stats: {day_str}[/bold]")
    console.print()
    console.print(f"  {cmds} commands | {hooks} hooks | {projects} projects")

    # Sources
    sources = day_data.get("sources", {})
    if sources:
        total_src = sum(sources.values())
        console.print()
        console.print("[bold]Sources:[/bold]")
        for src, count in sorted(sources.items(), key=lambda x: -x[1]):
            pct = int(count / total_src * 100) if total_src else 0
            label = src.replace("_", " ").title()
            console.print(f"  {label:<14} {count:>5}  ({pct}%)")

    # Commands
    commands = day_data.get("commands", {})
    if commands:
        console.print()
        console.print("[bold]Commands:[/bold]")
        for name, count in sorted(commands.items(), key=lambda x: -x[1]):
            console.print(f"  {name:<14} {count:>5}")

    # Hooks
    hooks_data = day_data.get("hooks", {})
    if hooks_data:
        console.print()
        console.print("[bold]Hooks:[/bold]")
        for name, count in sorted(hooks_data.items(), key=lambda x: -x[1]):
            console.print(f"  {name:<20} {count:>5}")

    # Projects
    projects_data = day_data.get("projects", {})
    if projects_data:
        console.print()
        console.print("[bold]Projects:[/bold]")
        for key, proj in sorted(projects_data.items(), key=lambda x: -x[1].get("commands", 0)):
            console.print(f"  {key}  ({proj.get('commands', 0)} commands)")


def _display_project(data: dict, project_filter: str, date_filter: str = "") -> None:
    """Display project-specific stats."""
    days_data = data.get("days", {})
    projects = _all_projects(days_data)
    key = _match_project(projects, project_filter)

    if not key:
        console.print(f"[warn]No project matching '{project_filter}'[/warn]")
        if projects:
            console.print()
            console.print("  Available:")
            for k in sorted(projects.keys()):
                console.print(f"    {k}")
        return

    proj = projects[key]

    # If date filter, show just that day
    if date_filter:
        day_str = _parse_date_arg(date_filter)
        day_data = days_data.get(day_str, {})
        proj_day = day_data.get("projects", {}).get(key, {})
        if not proj_day:
            console.print(f"[dim]No activity for {key} on {day_str}[/dim]")
            return
        console.print(f"[bold]keephive stats: {key} on {day_str}[/bold]")
        console.print()
        console.print(f"  {proj_day.get('commands', 0)} commands")
        by_cmd = proj_day.get("by_command", {})
        if by_cmd:
            console.print()
            console.print("[bold]Commands:[/bold]")
            for name, count in sorted(by_cmd.items(), key=lambda x: -x[1]):
                console.print(f"  {name:<14} {count:>5}")
        return

    # Full project view
    console.print(f"[bold]keephive stats: {key}[/bold]")
    console.print()
    console.print(
        f"  {proj['commands']} commands | {proj['sessions']} sessions | "
        f"{proj['days_active']} days active | first: {proj['first_seen']}"
    )

    # Commands
    by_cmd = proj["by_command"]
    if by_cmd:
        console.print()
        console.print("[bold]Commands:[/bold]")
        for name, count in sorted(by_cmd.items(), key=lambda x: -x[1]):
            console.print(f"  {name:<14} {count:>5}")

    # Activity sparkline (last 7 days)
    daily_counts = proj.get("daily_counts", {})
    if daily_counts:
        spark = _sparkline(daily_counts, days=7)
        max_val = max(c for _, c in spark) if spark else 0
        if max_val > 0:
            console.print()
            console.print("[bold]Activity (last 7 days):[/bold]")
            for label, count in spark:
                bar_str = _bar(count, max_val)
                console.print(f"  {label}  {bar_str}  {count}")

    # Records
    console.print()
    console.print("[bold]Records:[/bold]")
    if daily_counts:
        peak_day = max(daily_counts, key=daily_counts.get)
        console.print(f"  Peak day:        {peak_day} ({daily_counts[peak_day]} commands)")


def stats_text(project: str = "", date_arg: str = "") -> str:
    """Generate stats as text (for MCP tool)."""
    data = read_stats()

    # Build output by capturing console prints
    # Simpler approach: build it manually for MCP
    days_data = data.get("days", {})

    if not days_data:
        return "No stats recorded yet."

    if project:
        projects = _all_projects(days_data)
        key = _match_project(projects, project)
        if not key:
            available = ", ".join(sorted(projects.keys()))
            return f"No project matching '{project}'. Available: {available}"

        proj = projects[key]
        lines = [f"keephive stats: {key}"]
        lines.append(
            f"{proj['commands']} commands | {proj['sessions']} sessions | "
            f"{proj['days_active']} days active | first: {proj['first_seen']}"
        )
        by_cmd = proj["by_command"]
        if by_cmd:
            lines.append("\nCommands:")
            for name, count in sorted(by_cmd.items(), key=lambda x: -x[1]):
                lines.append(f"  {name:<14} {count:>5}")
        return "\n".join(lines)

    if date_arg:
        day_str = _parse_date_arg(date_arg)
        day_data = days_data.get(day_str, {})
        if not day_data:
            return f"No stats for {day_str}"
        cmds = _count_category(day_data, "commands")
        hooks = _count_category(day_data, "hooks")
        lines = [f"keephive stats: {day_str}", f"{cmds} commands | {hooks} hooks"]
        commands = day_data.get("commands", {})
        if commands:
            lines.append("\nCommands:")
            for name, count in sorted(commands.items(), key=lambda x: -x[1]):
                lines.append(f"  {name:<14} {count:>5}")
        return "\n".join(lines)

    # Full summary
    today_str = date.today().isoformat()

    today_data = days_data.get(today_str, {})
    today_cmds = _count_category(today_data, "commands")
    today_hooks = _count_category(today_data, "hooks")

    all_cmds = all_hooks = 0
    for day_data in days_data.values():
        all_cmds += _count_category(day_data, "commands")
        all_hooks += _count_category(day_data, "hooks")

    curr_streak, longest_streak = _calculate_streak(days_data)
    commands = _sum_counters(days_data, "commands")

    lines = [
        "keephive stats",
        f"Today: {today_cmds} commands | {today_hooks} hooks",
        f"All time: {all_cmds} commands | {all_hooks} hooks",
    ]

    if commands:
        lines.append("\nTop commands:")
        for name, count in sorted(commands.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {name:<14} {count:>5}")

    lines.append(f"\nCurrent streak: {curr_streak} days | Longest: {longest_streak} days")
    return "\n".join(lines)
