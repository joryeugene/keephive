"""hive stats: usage statistics with pipeline health, session productivity, and knowledge metrics."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta

from rich.table import Table

from keephive.clock import get_today
from keephive.output import console, show_hint
from keephive.storage import (
    count_log_entries_by_prefix,
    count_log_entries_by_prefix_daily,
    memory_file,
    parse_date_arg,
    read_evidence,
    read_stats,
    recall_stats_file,
    score_fact_decay,
    session_metrics,
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
    today = get_today()
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
    today = get_today()
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        day_str = d.isoformat()
        label = d.strftime("%b %d")
        count = daily_counts.get(day_str, 0)
        result.append((label, count))
    return result


_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _hourly_sparkline(hours: dict[str, int]) -> str:
    """Render 24-char sparkline from hourly counts."""
    vals = [hours.get(f"{h:02d}", 0) for h in range(24)]
    mx = max(vals) or 1
    return "".join(_SPARK_CHARS[min(8, round(v / mx * 8))] for v in vals)


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
    delta = (get_today() - d).days
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


# ---- Pipeline health metrics ----


def _knowledge_health() -> dict:
    """Compute knowledge lifecycle health metrics from evidence + memory.

    Returns dict with:
      total_facts, verified_count, corrected_count, uncertain_count,
      fresh, aging, stale (counts), fresh_pct, aging_pct, stale_pct,
      capture_recall_ratio, fact_survival_rate,
      corrected_this_week
    """
    mem = memory_file()
    evidence = read_evidence()

    # Count facts and bucket by freshness
    total_facts = 0
    fresh = aging = stale_count = 0

    if mem.exists():
        for line in mem.read_text().splitlines():
            m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
            if m:
                total_facts += 1
                fact_text = (
                    re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line).lstrip("- ").strip()
                )
                score = score_fact_decay(fact_text, m.group(1))
                if score > 0.6:
                    fresh += 1
                elif score > 0.3:
                    aging += 1
                else:
                    stale_count += 1

    # Aggregate evidence stats
    verified_count = 0
    corrected_count = 0
    uncertain_count = 0
    first_valid = 0
    first_total = 0
    corrected_this_week = 0
    week_ago = (get_today() - timedelta(days=7)).isoformat()

    for entry in evidence.values():
        if not isinstance(entry, dict) or "verify_count" not in entry:
            continue
        verified_count += entry.get("verify_count", 0)
        corrected_count += entry.get("correction_count", 0)

        # Fact survival: VALID on first verification
        history = entry.get("history", [])
        if history:
            first_total += 1
            if history[0].get("verdict") == "VALID":
                first_valid += 1

        # Corrections this week
        for h in history:
            if h.get("verdict") == "STALE" and h.get("date", "") >= week_ago:
                corrected_this_week += 1

        if entry.get("last_verdict") == "UNCERTAIN":
            uncertain_count += 1

    # Capture-to-recall ratio
    recall_sf = recall_stats_file()
    facts_recalled = 0
    if recall_sf.exists():
        try:
            recall_data = json.loads(recall_sf.read_text())
            for k, v in recall_data.items():
                if k == "_meta":
                    continue
                if isinstance(v, dict) and v.get("count", 0) > 0:
                    facts_recalled += 1
        except (json.JSONDecodeError, OSError):
            pass

    capture_recall_ratio = (facts_recalled / total_facts * 100) if total_facts > 0 else 0.0
    fact_survival_rate = (first_valid / first_total * 100) if first_total > 0 else 0.0

    total_bucketed = fresh + aging + stale_count
    fresh_pct = (fresh / total_bucketed * 100) if total_bucketed > 0 else 0.0
    aging_pct = (aging / total_bucketed * 100) if total_bucketed > 0 else 0.0
    stale_pct = (stale_count / total_bucketed * 100) if total_bucketed > 0 else 0.0

    return {
        "total_facts": total_facts,
        "verified_count": verified_count,
        "corrected_count": corrected_count,
        "uncertain_count": uncertain_count,
        "fresh": fresh,
        "aging": aging,
        "stale": stale_count,
        "fresh_pct": fresh_pct,
        "aging_pct": aging_pct,
        "stale_pct": stale_pct,
        "capture_recall_ratio": capture_recall_ratio,
        "fact_survival_rate": fact_survival_rate,
        "corrected_this_week": corrected_this_week,
    }


def _capture_mix(days_back: int = 7) -> dict:
    """Compute capture category breakdown and consistency.

    Returns dict with:
      counts: {FACT: N, DECISION: N, ...}
      total: total entries
      daily_counts: [(date_str, count), ...]
      consistency: 0-100 (inverse of coefficient of variation)
      sparkline_str: rendered sparkline
    """
    counts = count_log_entries_by_prefix(days_back=days_back)
    daily = count_log_entries_by_prefix_daily(days_back=14)
    total = sum(counts.values())

    # Consistency: 1 - CoV (coefficient of variation) over 14 days
    daily_vals = [c for _, c in daily]
    if daily_vals:
        mean = sum(daily_vals) / len(daily_vals)
        if mean > 0:
            variance = sum((x - mean) ** 2 for x in daily_vals) / len(daily_vals)
            std_dev = variance**0.5
            cov = std_dev / mean
            consistency = max(0, min(100, int((1 - cov) * 100)))
        else:
            consistency = 0
    else:
        consistency = 0

    # Build sparkline from daily counts
    vals = daily_vals[-7:]  # last 7 days
    mx = max(vals) if vals else 0
    if mx > 0:
        sparkline_str = "".join(_SPARK_CHARS[min(8, round(v / mx * 8))] for v in vals)
    else:
        sparkline_str = " " * len(vals)

    return {
        "counts": counts,
        "total": total,
        "daily_counts": daily,
        "consistency": consistency,
        "sparkline_str": sparkline_str,
    }


def _session_productivity(days_back: int = 30) -> dict:
    """Compute session productivity metrics.

    Returns dict with:
      prompts_today, prompts_week, convos_today, convos_week,
      avg_prompts_per_convo, median_prompts, prompts_trend_sparkline,
      depth_shallow, depth_medium, depth_deep,
      tool_distribution: [(tool, pct, trend_str), ...],
      compaction_rate
    """
    from keephive.storage import read_sessions

    sm = session_metrics(days_back=days_back)
    sessions = read_sessions(days_back=days_back)

    today_str = get_today().isoformat()
    week_ago = (get_today() - timedelta(days=7)).isoformat()

    # Prompts today/week
    prompts_today = sum(s.get("prompts", 0) for s in sessions if s.get("day") == today_str)
    prompts_week = sum(s.get("prompts", 0) for s in sessions if s.get("day", "") >= week_ago)

    # Session depth buckets
    shallow = medium = deep = 0
    for s in sessions:
        if s.get("day", "") < week_ago:
            continue
        prompts = s.get("prompts", 0)
        unique_tools = len(s.get("tools", {}))
        compacted = s.get("compacted", False)
        if prompts >= 40 and unique_tools >= 4 and compacted:
            deep += 1
        elif prompts >= 20 or unique_tools >= 3:
            medium += 1
        else:
            shallow += 1

    # Prompts-per-convo 14-day sparkline
    trend_data: list[float] = []
    for i in range(13, -1, -1):
        d = get_today() - timedelta(days=i)
        day_s = d.isoformat()
        day_sessions = [s for s in sessions if s.get("day") == day_s]
        if day_sessions:
            avg = sum(s.get("prompts", 0) for s in day_sessions) / len(day_sessions)
            trend_data.append(avg)
        else:
            trend_data.append(0)

    mx = max(trend_data) if trend_data else 0
    if mx > 0:
        trend_sparkline = "".join(_SPARK_CHARS[min(8, round(v / mx * 8))] for v in trend_data)
    else:
        trend_sparkline = ""

    # Tool distribution with week-over-week trend
    prev_week_start = (get_today() - timedelta(days=13)).isoformat()
    tools_this: dict[str, int] = {}
    tools_prev: dict[str, int] = {}
    for s in sessions:
        day = s.get("day", "")
        for tool, count in s.get("tools", {}).items():
            if day >= week_ago:
                tools_this[tool] = tools_this.get(tool, 0) + count
            elif day >= prev_week_start:
                tools_prev[tool] = tools_prev.get(tool, 0) + count

    total_this = sum(tools_this.values()) or 1
    total_prev = sum(tools_prev.values()) or 1
    tool_dist: list[tuple[str, int, str]] = []
    for tool in sorted(tools_this, key=lambda t: -tools_this[t])[:5]:
        pct = int(tools_this[tool] / total_this * 100)
        prev_pct = int(tools_prev.get(tool, 0) / total_prev * 100)
        delta = pct - prev_pct
        if abs(delta) >= 2:
            trend_str = f"\u25b2{delta}%" if delta > 0 else f"\u25bc{abs(delta)}%"
        else:
            trend_str = ""
        tool_dist.append((tool, pct, trend_str))

    return {
        "prompts_today": prompts_today,
        "prompts_week": prompts_week,
        "convos_today": sm["sessions_today"],
        "convos_week": sm["sessions_this_week"],
        "avg_prompts_per_convo": sm["avg_prompts_per_session"],
        "median_prompts": sm["median_prompts_per_session"],
        "prompts_trend_sparkline": trend_sparkline,
        "depth_shallow": shallow,
        "depth_medium": medium,
        "depth_deep": deep,
        "tool_distribution": tool_dist,
        "compaction_rate": sm["compaction_rate"],
    }


def _weekly_trends(data: dict) -> dict:
    """Compute this-week vs. last-week comparison for key metrics.

    Returns dict with list of {label, this, prev, delta_pct, trend} items.
    """
    days_data = data.get("days", {})
    today_d = get_today()
    this_week_start = (today_d - timedelta(days=6)).isoformat()
    prev_week_start = (today_d - timedelta(days=13)).isoformat()
    prev_week_end = (today_d - timedelta(days=7)).isoformat()

    # Commands
    cmds_this = cmds_prev = 0
    for day_str, day_data in days_data.items():
        total = sum(day_data.get("commands", {}).values())
        if day_str >= this_week_start:
            cmds_this += total
        elif prev_week_start <= day_str < prev_week_end:
            cmds_prev += total

    # Sessions
    from keephive.storage import read_sessions

    sessions = read_sessions(14)
    sess_this = [s for s in sessions if s["day"] >= this_week_start]
    sess_prev = [s for s in sessions if prev_week_start <= s["day"] < prev_week_end]

    def _avg_p(slist: list) -> float:
        if not slist:
            return 0.0
        return sum(s.get("prompts", 0) for s in slist) / len(slist)

    # Insights (categorized entries this/prev week from daily logs)
    insights_this = insights_prev = 0
    todos_done_this = todos_done_prev = 0
    verified_this = verified_prev = 0
    for day_str, day_data in days_data.items():
        meta = day_data.get("meta", {})
        if day_str >= this_week_start:
            insights_this += meta.get("insights_accepted", 0)
            todos_done_this += meta.get("todos_completed", 0)
        elif prev_week_start <= day_str < prev_week_end:
            insights_prev += meta.get("insights_accepted", 0)
            todos_done_prev += meta.get("todos_completed", 0)

    # Count verify events from daily log categories
    from keephive.storage import daily_dir, safe_read_text

    dd = daily_dir()
    if dd.exists():
        for f in sorted(dd.glob("*.md")):
            day_str = f.stem
            if day_str >= this_week_start:
                content = safe_read_text(f)
                verified_this += content.count("VERIFY:")
            elif prev_week_start <= day_str < prev_week_end:
                content = safe_read_text(f)
                verified_prev += content.count("VERIFY:")

    def _delta(curr: float, prev: float) -> tuple[str, str]:
        if prev == 0:
            trend = "up" if curr > 0 else "flat"
            pct = "+100%" if curr > 0 else ""
        else:
            ratio = curr / prev
            if ratio > 1.05:
                trend = "up"
                pct = f"+{int((ratio - 1) * 100)}%"
            elif ratio < 0.95:
                trend = "down"
                pct = f"{int((ratio - 1) * 100)}%"
            else:
                trend = "flat"
                pct = ""
        return pct, trend

    metrics = []
    for label, this, prev, fmt in [
        (
            "Prompts",
            sum(s.get("prompts", 0) for s in sess_this),
            sum(s.get("prompts", 0) for s in sess_prev),
            "d",
        ),
        ("Convos", len(sess_this), len(sess_prev), "d"),
        ("P/convo", _avg_p(sess_this), _avg_p(sess_prev), ".0f"),
        ("Insights", insights_this, insights_prev, "d"),
        ("TODOs done", todos_done_this, todos_done_prev, "d"),
        ("Verified", verified_this, verified_prev, "d"),
    ]:
        pct, trend = _delta(float(this), float(prev))
        metrics.append(
            {
                "label": label,
                "this": this,
                "prev": prev,
                "delta_pct": pct,
                "trend": trend,
                "fmt": fmt,
            }
        )

    return {"metrics": metrics}


def _most_recalled() -> list[dict]:
    """Return top 3 most-recalled facts with text and count.

    Cross-references recall-stats.json with memory.md to get fact text.
    """
    rsf = recall_stats_file()
    if not rsf.exists():
        return []
    try:
        recall_data = json.loads(rsf.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    # Build hash-to-count map (skip _meta)
    hash_counts: list[tuple[str, int, str]] = []
    for key, entry in recall_data.items():
        if key == "_meta" or not isinstance(entry, dict):
            continue
        count = entry.get("count", 0)
        last = entry.get("last", "")
        if count > 0:
            hash_counts.append((key, count, last))

    if not hash_counts:
        return []

    hash_counts.sort(key=lambda x: -x[1])

    # Cross-reference with memory.md to get fact text
    import hashlib

    mem = memory_file()
    if not mem.exists():
        return []

    fact_by_hash: dict[str, str] = {}
    for line in mem.read_text().splitlines():
        if line.startswith("- "):
            clean = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line).strip()
            h = hashlib.sha256(line.strip().encode()).hexdigest()[:16]
            fact_by_hash[h] = clean

    results = []
    for h, count, last in hash_counts[:3]:
        text = fact_by_hash.get(h, "(fact removed from memory)")
        # Truncate long facts
        if len(text) > 80:
            text = text[:77] + "..."
        results.append({"text": text, "count": count, "last": last})

    return results


def _productivity_pulse(
    health: dict | None = None,
    capture: dict | None = None,
    sessions: dict | None = None,
    data: dict | None = None,
) -> dict:
    """Compute composite 0-100 productivity pulse score.

    Components:
      Memory freshness: 25%
      Capture-recall ratio: 20%
      Session depth: 20%
      TODO completion rate: 15%
      Verification cadence: 10%
      Streak bonus: 10%

    Returns {score, delta, components}.
    """
    if health is None:
        health = _knowledge_health()
    if capture is None:
        capture = _capture_mix()
    if sessions is None:
        sessions = _session_productivity()
    if data is None:
        data = read_stats()

    # 1. Memory freshness (25%) - fresh_pct normalized to 0-1
    freshness_score = health["fresh_pct"] / 100.0

    # 2. Capture-recall ratio (20%) - capped at 100%
    recall_score = min(1.0, health["capture_recall_ratio"] / 100.0)

    # 3. Session depth (20%) - weighted by depth quality
    total_depth = sessions["depth_shallow"] + sessions["depth_medium"] + sessions["depth_deep"]
    if total_depth > 0:
        depth_score = (
            sessions["depth_deep"] * 1.0
            + sessions["depth_medium"] * 0.5
            + sessions["depth_shallow"] * 0.1
        ) / total_depth
    else:
        depth_score = 0.0

    # 4. TODO completion rate (15%)
    from keephive.storage import open_todos, recent_dones

    todos = open_todos()
    dones = recent_dones(days=7)
    total_todo_activity = len(todos) + len(dones)
    todo_rate = len(dones) / total_todo_activity if total_todo_activity > 0 else 0.0

    # 5. Verification cadence (10%) - days since last verify vs threshold
    evidence = read_evidence()
    last_verify_date = ""
    for entry in evidence.values():
        if isinstance(entry, dict) and entry.get("last_date", "") > last_verify_date:
            last_verify_date = entry.get("last_date", "")
    if last_verify_date:
        try:
            days_since = (get_today() - date.fromisoformat(last_verify_date)).days
            verify_score = max(0.0, 1.0 - days_since / 30.0)
        except ValueError:
            verify_score = 0.0
    else:
        verify_score = 0.0

    # 6. Streak bonus (10%)
    days_data = data.get("days", {})
    curr_streak, _ = _calculate_streak(days_data)
    streak_score = min(1.0, curr_streak / 7.0)

    # Weighted composite
    score = int(
        (
            freshness_score * 25
            + recall_score * 20
            + depth_score * 20
            + todo_rate * 15
            + verify_score * 10
            + streak_score * 10
        )
    )
    score = max(0, min(100, score))

    # Delta: compare to previous week's computed pulse
    # Simple approximation: use streak and freshness difference
    # A full delta would require persisted weekly scores
    delta = 0  # placeholder until we have history

    return {
        "score": score,
        "delta": delta,
        "components": {
            "freshness": int(freshness_score * 100),
            "recall": int(recall_score * 100),
            "depth": int(depth_score * 100),
            "todo_rate": int(todo_rate * 100),
            "verify": int(verify_score * 100),
            "streak": int(streak_score * 100),
        },
    }


# ---- Display functions ----


def _reflect_ctx() -> str:
    """Context string for reflect action: number of daily logs available."""
    from keephive.storage import daily_dir

    dd = daily_dir()
    if not dd.exists():
        return ""
    log_count = len(list(dd.glob("*.md")))
    return f"{log_count} daily logs" if log_count > 0 else ""


def _display_full(data: dict) -> None:
    """Display full stats overview with pipeline health metrics."""
    days_data = data.get("days", {})

    if not days_data:
        console.print("[dim]No stats recorded yet.[/dim]")
        console.print("  Stats are tracked automatically as you use hive commands.")
        return

    curr_streak, longest_streak = _calculate_streak(days_data)

    # Compute metrics
    health = _knowledge_health()
    capture = _capture_mix()
    sess_prod = _session_productivity()
    trends = _weekly_trends(data)
    recalled = _most_recalled()

    # Header
    console.print("[bold]keephive[/bold]  ·  knowledge health")

    # Pipeline section
    console.print()
    console.print("[dim]Pipeline[/dim]")
    pipeline_parts = []
    if health["total_facts"] > 0:
        pipeline_parts.append(f"  {health['total_facts']} facts verified")
        if health["corrected_this_week"] > 0:
            pipeline_parts[-1] += f" ({health['corrected_this_week']} corrected this week)"
        if health["stale"] > 0:
            pipeline_parts[-1] += f" · [warn]{health['stale']} stale[/warn]"
    else:
        pipeline_parts.append("  No facts verified yet")

    if health["total_facts"] > 0:
        pipeline_parts.append(
            f"  [ok]{health['fresh_pct']:.0f}% fresh[/ok] · "
            f"{health['aging_pct']:.0f}% aging · "
            f"{health['stale_pct']:.0f}% stale"
        )
        if health["capture_recall_ratio"] > 0 or health["fact_survival_rate"] > 0:
            pipeline_parts.append(
                f"  capture-to-recall: {health['capture_recall_ratio']:.0f}%"
                f" · fact survival: {health['fact_survival_rate']:.0f}%"
            )

    for line in pipeline_parts:
        console.print(line)

    # Capture section
    if capture["total"] > 0:
        console.print()
        console.print("[dim]Capture (last 7 days)[/dim]")
        cat_parts = []
        for cat in ("FACT", "DECISION", "INSIGHT", "TODO", "DONE"):
            c = capture["counts"].get(cat, 0)
            if c > 0:
                cat_parts.append(f"{c} {cat.lower()}s")
        done_count = capture["counts"].get("DONE", 0)
        todo_count = capture["counts"].get("TODO", 0)
        # Merge DONE into TODO display
        filtered = [p for p in cat_parts if "done" not in p.lower()]
        if todo_count > 0 and done_count > 0:
            filtered = [
                f"{todo_count} todos ({done_count} done)" if "todo" in p else p for p in filtered
            ]
        console.print(f"  {capture['sparkline_str']}  {' · '.join(filtered)}")
        if capture["consistency"] > 0:
            console.print(f"  consistency: {capture['consistency']}%")

    # Sessions section
    console.print()
    console.print("[dim]Sessions[/dim]")
    console.print(
        f"  today [bold]{sess_prod['convos_today']}[/bold]  ·  "
        f"week [bold]{sess_prod['convos_week']}[/bold]  ·  "
        f"streak [bold]{curr_streak}d[/bold] (best: {longest_streak}d)"
    )
    if sess_prod["prompts_today"] > 0 or sess_prod["prompts_week"] > 0:
        console.print(
            f"  {sess_prod['prompts_today']} prompts today · {sess_prod['prompts_week']} this week"
        )
    if sess_prod["avg_prompts_per_convo"] > 0:
        line = (
            f"  avg [bold]{sess_prod['avg_prompts_per_convo']:.0f}[/bold] prompts/convo"
            f" · median {sess_prod['median_prompts']:.0f}"
        )
        if sess_prod["prompts_trend_sparkline"]:
            line += f" · {sess_prod['prompts_trend_sparkline']}"
        console.print(line)

    depth_parts = []
    if sess_prod["depth_deep"] > 0:
        depth_parts.append(f"{sess_prod['depth_deep']} deep")
    if sess_prod["depth_medium"] > 0:
        depth_parts.append(f"{sess_prod['depth_medium']} medium")
    if sess_prod["depth_shallow"] > 0:
        depth_parts.append(f"{sess_prod['depth_shallow']} shallow")
    if depth_parts:
        line = f"  {' · '.join(depth_parts)}"
        if sess_prod["compaction_rate"] > 0:
            line += f" · compaction: {int(sess_prod['compaction_rate'] * 100)}%"
        console.print(line)

    if sess_prod["tool_distribution"]:
        tool_parts = []
        for tool, pct, trend_str in sess_prod["tool_distribution"]:
            part = f"{tool} {pct}%"
            if trend_str:
                part += f" {trend_str}"
            tool_parts.append(part)
        console.print(f"  tools: {'  ·  '.join(tool_parts)}")

    # Trends section
    if trends["metrics"]:
        console.print()
        console.print("[dim]Trends (vs. last week)[/dim]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(min_width=12)
        grid.add_column(justify="right", min_width=6)
        grid.add_column(justify="right", min_width=6)
        grid.add_column(min_width=8)
        for m in trends["metrics"]:
            fmt = m.get("fmt", "d")
            if fmt == "d":
                this_str = str(int(m["this"]))
                prev_str = str(int(m["prev"]))
            else:
                this_str = f"{m['this']:{fmt}}"
                prev_str = f"{m['prev']:{fmt}}"
            delta = m.get("delta_pct", "")
            if m["trend"] == "up":
                delta_display = f"[ok]{delta}[/ok]"
            elif m["trend"] == "down":
                delta_display = f"[warn]{delta}[/warn]"
            else:
                delta_display = "[dim]=[/dim]"
            grid.add_row(f"  {m['label']}", this_str, prev_str, delta_display)
        console.print(grid)

    # Activity chart (7-day bars)
    daily_cmd_counts: dict[str, int] = {}
    for i in range(6, -1, -1):
        d = get_today() - timedelta(days=i)
        day_s = d.isoformat()
        dd = days_data.get(day_s, {})
        daily_cmd_counts[day_s] = _count_category(dd, "commands")

    spark = _sparkline(daily_cmd_counts, days=7)
    max_c = max((c for _, c in spark), default=1) or 1

    # Two-column: activity + sources/hooks
    sources = _sum_sources(days_data)
    hooks = _sum_counters(days_data, "hooks")

    console.print()

    grid = Table.grid(padding=(0, 4))
    grid.add_column()
    grid.add_column()

    left: list[str] = ["[dim]Activity · last 7 days[/dim]"]
    for i, (label, count) in enumerate(spark):
        bar = _bar(count, max_c, width=20) if count > 0 else ""
        bar_display = bar if bar else "\u2500"
        today_marker = "  \u2190" if i == len(spark) - 1 else ""
        try:
            d = get_today() - timedelta(days=len(spark) - 1 - i)
            day_name = d.strftime("%a")
        except Exception:
            day_name = "   "
        left.append(f"  {label}  {day_name:<3}  {bar_display:<20}  {count:>3}{today_marker}")

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

    console.print(grid)

    # Most recalled
    recalled = [
        r
        for r in recalled
        if r.get("text", "").strip() and "fact removed" not in r.get("text", "").lower()
    ]
    if recalled:
        console.print()
        console.print("[dim]Most Recalled[/dim]")
        for r in recalled:
            console.print(f'  "{r["text"]}" ({r["count"]}\u00d7)')

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

    # Pipeline actions: show last-run status for key maintenance commands
    console.print()
    console.print("[dim]Pipeline Actions[/dim]")
    pipeline_cmds = {
        "verify": {"hint": "hive v", "ctx_fn": lambda: f"{health['total_facts']} facts to check"},
        "reflect": {"hint": "hive rf", "ctx_fn": lambda: _reflect_ctx()},
        "audit": {"hint": "hive a", "ctx_fn": lambda: ""},
    }
    for cmd, meta in pipeline_cmds.items():
        last_date = None
        for day in sorted(days_data.keys(), reverse=True):
            if cmd in days_data[day].get("commands", {}):
                last_date = day
                break
        ctx = meta["ctx_fn"]()
        if last_date is None:
            status = "[warn]not yet run[/warn]"
        else:
            try:
                days_ago = (get_today() - date.fromisoformat(last_date)).days
                if days_ago == 0:
                    status = "[ok]today[/ok]"
                elif days_ago == 1:
                    status = "[ok]yesterday[/ok]"
                elif days_ago <= 7:
                    status = f"[dim]{days_ago}d ago[/dim]"
                else:
                    status = f"[warn]{days_ago}d ago[/warn]"
            except ValueError:
                status = f"[dim]{last_date}[/dim]"
        ctx_str = f"  [dim]{ctx}[/dim]" if ctx else ""
        console.print(f"  {meta['hint']:<10} {status}{ctx_str}")

    show_hint("hive s", "live status")


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

    # Hourly sparkline
    day_hours = day_data.get("hours", {})
    if day_hours:
        spark_line = _hourly_sparkline(day_hours)
        console.print(f"  hourly  {spark_line}  (0h{'─' * 22}23h)")

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

    # Sessions for this day
    sessions_data = day_data.get("sessions", {})
    if sessions_data:
        console.print()
        console.print("[bold]Sessions:[/bold]")
        for sid, sdata in sorted(sessions_data.items(), key=lambda x: x[1].get("started", "")):
            prompts = sdata.get("prompts", 0)
            proj = sdata.get("project", "")
            proj_label = f"  {proj}" if proj else ""
            tools = sdata.get("tools", {})
            tool_str = ", ".join(f"{t}:{c}" for t, c in sorted(tools.items(), key=lambda x: -x[1]))
            tool_label = f"  [{tool_str}]" if tool_str else ""
            compact_label = "  compacted" if sdata.get("compacted") else ""
            console.print(
                f"  {sid[:12]}...  {prompts} prompts{proj_label}{tool_label}{compact_label}"
            )


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

    # Hourly sparkline (for today only)
    today_str = get_today().isoformat()
    today_proj_data = days_data.get(today_str, {})
    today_hours = today_proj_data.get("hours", {})
    if today_hours:
        spark_line = _hourly_sparkline(today_hours)
        console.print(f"  hourly  {spark_line}  (0h{'─' * 22}23h)")

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
    today_str = get_today().isoformat()

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

    try:
        sm = session_metrics(days_back=30)
        if sm["total_sessions"] > 0:
            lines.append(
                f"\nSessions: {sm['sessions_today']} today | {sm['sessions_this_week']} this week | "
                f"{sm['total_sessions']} total"
            )
            lines.append(
                f"Avg {sm['avg_prompts_per_session']:.0f} prompts/session | "
                f"Avg {sm['avg_duration_minutes']:.0f}m duration"
            )
            if sm["compaction_rate"] > 0:
                lines.append(f"Compaction rate: {int(sm['compaction_rate'] * 100)}%")
    except Exception:
        pass

    return "\n".join(lines)
