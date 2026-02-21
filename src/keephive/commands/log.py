"""hive l: view daily log with date navigation."""

from __future__ import annotations

from keephive.output import console, notify_sound
from keephive.storage import (
    count_daily_entries,
    daily_dir,
    daily_file,
    ensure_daily,
    parse_date_arg,
)

# Re-export for backwards compatibility (used by mcp_server.py)
_parse_date_arg = parse_date_arg


def _nearby_logs(limit: int = 5) -> list[tuple[str, int]]:
    """Find the most recent daily logs with entry counts.

    Returns list of (date_str, entry_count), most recent first.
    """
    dd = daily_dir()
    if not dd.exists():
        return []

    files = sorted(dd.glob("*.md"), reverse=True)
    results = []
    for f in files[:limit]:
        day_str = f.stem
        count = count_daily_entries(day_str)
        results.append((day_str, count))
    return results


def _log_summarize() -> None:
    """Summarize today's log via LLM."""
    import os
    import sys

    from keephive.storage import daily_file, today

    df = daily_file()
    content = df.read_text() if df.exists() else ""
    if not any(line.startswith("- ") for line in content.splitlines()):
        console.print("[dim]No entries today to summarize[/dim]")
        return

    if os.environ.get("HIVE_SKIP_LLM"):
        console.print("[dim]HIVE_SKIP_LLM set, skipping summarize[/dim]")
        return

    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.models import DailySummaryResponse

    log_text = df.read_text()
    prompt = (
        f"Summarize the following daily log in 3-5 concise bullet points. "
        f"Focus on decisions made, facts learned, and tasks completed. "
        f"Be specific, not generic.\n\n{log_text}"
    )
    try:
        with console.status("  Summarizing...", spinner="dots"):
            resp = run_claude_pipe(prompt, DailySummaryResponse, model="haiku")
    except ClaudePipeError as e:
        notify_sound(False)
        print(f"[keephive] summarize failed: {e}", file=sys.stderr)
        return

    plain = "\n".join(f"• {b}" for b in resp.bullets)
    if sys.stdout.isatty():
        console.print(f"[bold]Today ({today()}) -- summary:[/bold]")
        for bullet in resp.bullets:
            console.print(f"  • {bullet}")
        from keephive.output import copy_to_clipboard

        if copy_to_clipboard(plain):
            console.print("[dim]Copied to clipboard[/dim]")
    else:
        print(plain)

    # Persist summary to daily log
    from keephive.storage import append_to_daily

    summary_text = " | ".join(resp.bullets)
    append_to_daily(f"SUMMARY: {summary_text}")
    notify_sound(True)


def cmd_log(args: list[str]) -> None:
    ensure_daily()

    if args and args[0] == "summarize":
        _log_summarize()
        return

    date_arg = args[0] if args else ""
    target_date = _parse_date_arg(date_arg)

    df = daily_file(target_date)
    if df.exists():
        # Show stats header if available
        try:
            from keephive.storage import read_stats

            stats = read_stats()
            day_data = stats.get("days", {}).get(target_date, {})
            if day_data:
                projects = day_data.get("projects", {})
                if projects:
                    proj_parts = [
                        f"{k} ({v.get('commands', 0)} commands)" for k, v in projects.items()
                    ]
                    console.print(f"[dim]{', '.join(proj_parts)}[/dim]")
                hooks_count = sum(day_data.get("hooks", {}).values())
                sessions = sum(p.get("sessions", 0) for p in projects.values())
                if sessions or hooks_count:
                    console.print(f"[dim]Sessions: {sessions} | Hooks: {hooks_count}[/dim]")
                console.print()
        except Exception:
            pass

        print(df.read_text())
        console.print(
            '  [dim]hive r "insight"[/dim] add  |  [dim]hive l summarize[/dim] AI summary  |  [dim]hive rf[/dim] reflect'
        )
    else:
        console.print(f"[dim]No log for {target_date}[/dim]")
        console.print()

        nearby = _nearby_logs()
        if nearby:
            console.print("  Nearby:")
            for day_str, count in nearby:
                entry_word = "entry" if count == 1 else "entries"
                console.print(f"    {day_str}  ({count} {entry_word})")
            console.print()

        console.print('  -> [dim]hive r "insight"[/dim] to start logging')
