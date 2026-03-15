"""hive growth: visible compounding metrics over time.

Shows how keephive gets smarter with use. 30-day trend window with
sparklines, week-over-week deltas, and impact attribution.

Usage: hive growth [--json]
"""

from __future__ import annotations

import json
import sys

from rich.console import Console

from keephive.storage import (
    experiment_results,
    growth_snapshot,
    improvement_history_stats,
)

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
        return f"[ok]+{diff}[/ok]"
    elif diff < 0:
        return f"[err]{diff}[/err]"
    return "[dim]0[/dim]"


def _pct_str(value: float) -> str:
    """Format a percentage with color coding."""
    if value >= 70:
        return f"[ok]{value:.0f}%[/ok]"
    elif value >= 40:
        return f"[warn]{value:.0f}%[/warn]"
    return f"[err]{value:.0f}%[/err]"


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

    # Comprehension coverage
    _print_comprehension_section(console, snap.get("comprehension", {}))

    # Improvement velocity
    _print_improvement_section(console)

    # Experiment results
    _print_experiment_section(console)

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


def _dark_str(dark_pct: float) -> str:
    """Format dark knowledge percentage with color coding (inverse of coverage)."""
    if dark_pct == 0:
        return "[ok]0%[/ok]"
    elif dark_pct < 20:
        return f"[ok]{dark_pct:.0f}%[/ok] dark"
    elif dark_pct <= 50:
        return f"[warn]{dark_pct:.0f}%[/warn] dark"
    return f"[err]{dark_pct:.0f}%[/err] dark"


def _print_comprehension_section(console: Console, cov: dict) -> None:
    """Print the Comprehension Coverage section."""
    total = cov.get("total", 0)
    if total == 0:
        return

    verified = cov.get("verified", 0)
    auto_only = cov.get("auto_only", 0)
    user_owned = cov.get("user_owned", 0)
    dark_pct = cov.get("dark_pct", 0.0)
    coverage_pct = cov.get("coverage_pct", 0.0)

    console.print("[bold]Comprehension Coverage[/bold]")
    console.print(f"  Total facts         {total}")
    console.print(f"  Verified            {verified:<4d}  [dim](user-confirmed)[/dim]")
    if auto_only > 0:
        console.print(f"  Auto-captured       {auto_only:<4d}  {_dark_str(dark_pct)}")
    if user_owned > 0:
        console.print(f"  User-owned          {user_owned:<4d}  [dim](manually written)[/dim]")
    console.print(f"  Coverage            {_pct_str(coverage_pct)}")
    console.print()


def _print_improvement_section(console: Console) -> None:
    """Print KingBee improvement velocity section."""
    stats = improvement_history_stats()
    total = stats["total_applied"] + stats["total_dismissed"]
    if total == 0:
        return

    console.print("[bold]KingBee Effectiveness[/bold]")
    console.print(f"  Applied             {stats['total_applied']}")
    console.print(f"  Dismissed           {stats['total_dismissed']}")
    console.print(f"  Acceptance rate     {_pct_str(stats['acceptance_rate'] * 100)}")
    if stats["by_type"]:
        type_parts = [f"{t}={c}" for t, c in sorted(stats["by_type"].items(), key=lambda x: -x[1])]
        console.print(f"  By type             {', '.join(type_parts)}")
    console.print()


def _print_experiment_section(console: Console) -> None:
    """Print rule experiment results section."""
    results = experiment_results()
    if not results:
        return

    console.print("[bold]Rule Experiments[/bold]")
    for exp in results[:5]:
        rule_text = exp.get("rule_text", "")[:50]
        delta = exp.get("friction_delta")
        if delta is not None:
            sign = "+" if delta > 0 else ""
            color = "err" if delta > 0 else "ok"
            console.print(f"  {rule_text}  [{color}]{sign}{delta:.0f}%[/{color}]")
        else:
            console.print(f"  {rule_text}  [dim]no baseline[/dim]")
    console.print()


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

    # Comprehension debt
    cov = snap.get("comprehension", {})
    dark_pct = cov.get("dark_pct", 0.0)
    auto_only = cov.get("auto_only", 0)
    if dark_pct > 50:
        observations.append(
            "More than half your knowledge is auto-captured and unreviewed."
            " Run hive verify to close the comprehension gap."
        )
    elif dark_pct > 0 and dark_pct < 20:
        observations.append(
            f"Auto-captured facts are mostly reviewed ({auto_only} unverified)."
            " Low comprehension debt."
        )

    # Improvement velocity
    hist = improvement_history_stats()
    if hist["total_applied"] > 0:
        recent = hist.get("recent", [])
        week_cutoff = snap["trend_30d"][-7]["date"] if len(snap["trend_30d"]) >= 7 else ""
        this_week = sum(1 for r in recent if week_cutoff and r.get("applied_at", "") >= week_cutoff)
        if this_week > 0:
            observations.append(f"{this_week} improvements applied this week.")
        elif hist["total_applied"] > 3:
            observations.append(
                f"{hist['total_applied']} improvements applied total. KingBee is learning what works."
            )

    if observations:
        console.print("[bold]Growth Story[/bold]")
        for obs in observations:
            console.print(f"  {obs}")
