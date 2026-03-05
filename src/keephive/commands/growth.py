"""hive growth: visible compounding metrics over time.

Shows how keephive gets smarter with use. 30-day trend window with
sparklines, week-over-week deltas, and impact attribution.

Usage: hive growth [--json]
"""

from __future__ import annotations

import json
import sys

from rich.console import Console

from keephive.storage import growth_snapshot

_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _sparkline(values: list[int | float], width: int = 30) -> str:
    """Render a sparkline from a list of numeric values."""
    if not values:
        return ""
    mx = max(values)
    if mx == 0:
        return " " * min(len(values), width)
    # Take last `width` values
    vals = values[-width:]
    return "".join(_SPARK_CHARS[min(8, round(v / mx * 8))] for v in vals)


def _delta_str(current: int, previous: int) -> str:
    """Format a week-over-week delta as +N or -N with color."""
    diff = current - previous
    if diff > 0:
        return f"[green]+{diff}[/green]"
    elif diff < 0:
        return f"[red]{diff}[/red]"
    return "[dim]0[/dim]"


def _pct_str(value: float) -> str:
    """Format a percentage with color coding."""
    if value >= 70:
        return f"[green]{value:.0f}%[/green]"
    elif value >= 40:
        return f"[yellow]{value:.0f}%[/yellow]"
    return f"[red]{value:.0f}%[/red]"


def cmd_growth(args: list[str]) -> None:
    """Display growth and compounding metrics."""
    if "--json" in args:
        snap = growth_snapshot()
        json.dump(snap, sys.stdout, indent=2)
        print()
        return

    console = Console()
    snap = growth_snapshot()
    trend = snap["trend_30d"]

    if not trend or all(d["log_entries"] == 0 for d in trend):
        console.print(
            "[dim]Not enough data yet. Use keephive for a few days to see growth trends.[/dim]"
        )
        return

    # Header
    console.print("[bold]keephive[/bold]  ·  growth")
    console.print()

    # Current state
    console.print("[bold]Knowledge State[/bold]")
    console.print(f"  Facts in memory     {snap['fact_count']}")
    console.print(f"  Fact freshness      {_pct_str(snap['fact_freshness'])}")
    console.print(f"  Knowledge guides    {snap['guide_count']}")
    if snap["recall_total"] > 0:
        console.print(
            f"  Recall hit rate     {_pct_str(snap['recall_rate'])}  ({snap['recall_hits']}/{snap['recall_total']})"
        )
    console.print()

    # 30-day sparklines
    console.print("[bold]30-Day Trends[/bold]")
    metrics = [
        ("Log entries", [d["log_entries"] for d in trend]),
        ("Guide hits", [d["guide_hits"] for d in trend]),
        ("TODOs done", [d["todos_done"] for d in trend]),
        ("Corrections", [d["corrections"] for d in trend]),
        ("Daemon runs", [d["daemon_runs"] for d in trend]),
        ("Commands", [d["commands"] for d in trend]),
    ]

    for label, values in metrics:
        total = sum(values)
        if total == 0:
            continue
        spark = _sparkline(values)
        console.print(f"  {label:<16s}{spark}  {total:>4d}")
    console.print()

    # Week-over-week comparison
    wk = snap["week_totals"]
    prev = snap["prev_week_totals"]
    console.print("[bold]This Week vs Last Week[/bold]")
    comparisons = [
        ("Log entries", wk["log_entries"], prev["log_entries"]),
        ("Guide hits", wk["guide_hits"], prev["guide_hits"]),
        ("TODOs done", wk["todos_done"], prev["todos_done"]),
        ("Corrections", wk["corrections"], prev["corrections"]),
        ("Daemon runs", wk["daemon_runs"], prev["daemon_runs"]),
        ("Commands", wk["commands"], prev["commands"]),
    ]

    for label, current, previous in comparisons:
        if current == 0 and previous == 0:
            continue
        delta = _delta_str(current, previous)
        console.print(f"  {label:<16s}{current:>4d}  {delta}")
    console.print()

    # Growth story (deterministic template)
    _print_growth_story(console, snap)


def _print_growth_story(console: Console, snap: dict) -> None:
    """Print a deterministic growth narrative from template patterns."""
    observations: list[str] = []

    wk = snap["week_totals"]
    prev = snap["prev_week_totals"]

    # Capture velocity
    if wk["log_entries"] > prev["log_entries"] and prev["log_entries"] > 0:
        pct = round((wk["log_entries"] - prev["log_entries"]) / prev["log_entries"] * 100)
        observations.append(f"Capture velocity up {pct}% this week.")
    elif wk["log_entries"] < prev["log_entries"] and prev["log_entries"] > 0:
        observations.append("Capture velocity slowed this week.")

    # TODO completion
    if wk["todos_done"] > 0:
        observations.append(f"{wk['todos_done']} TODOs completed this week.")

    # Corrections trending
    if wk["corrections"] < prev["corrections"] and prev["corrections"] > 0:
        observations.append("Fewer corrections needed. Knowledge accuracy is improving.")
    elif wk["corrections"] > prev["corrections"] and wk["corrections"] > 2:
        observations.append("More corrections this week. Active knowledge refinement.")

    # Guide engagement
    if wk["guide_hits"] > 0:
        observations.append(f"Guides injected {wk['guide_hits']} times. Knowledge is being reused.")

    # Freshness
    if snap["fact_freshness"] >= 80:
        observations.append("Fact freshness is high. Verification cycle is healthy.")
    elif snap["fact_freshness"] < 40 and snap["fact_count"] > 10:
        observations.append("Many facts are aging. Consider running hive verify.")

    if observations:
        console.print("[bold]Growth Story[/bold]")
        for obs in observations:
            console.print(f"  {obs}")
