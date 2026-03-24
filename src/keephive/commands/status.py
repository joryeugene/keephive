"""hive s / hive status: show status overview."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from keephive import __version__
from keephive.clock import get_today
from keephive.llm import available_backends, get_backend_state
from keephive.output import console
from keephive.platforms import platform_specs
from keephive.settings import get_setting
from keephive.storage import (
    NOTE_SLOT_COUNT,
    active_profile,
    active_slot,
    count_daily_entries,
    count_stale_facts,
    daily_dir,
    due_recurring,
    ensure_dirs,
    get_meaningful_entries,
    guides_dir,
    hive_dir,
    is_disabled,
    is_force_cli,
    is_llm_paused,
    memory_file,
    open_todos,
    read_stats,
    recurring_file,
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
        cutoff_60 = (get_today() - timedelta(days=60)).isoformat()
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
        cutoff_3d = (get_today() - timedelta(days=3)).isoformat()
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

    # 3b. Pending facts (auto-captured, awaiting review)
    pending_facts = hive_dir() / ".pending-facts.md"
    if pending_facts.exists() and pending_facts.read_text().strip():
        n = sum(1 for ln in pending_facts.read_text().splitlines() if ln.strip().startswith("- "))
        if n > 0:
            s = "s" if n != 1 else ""
            return ("hive mem review", f"{n} fact{s} pending review")

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
            day_str = (get_today() - timedelta(days=days_ago)).isoformat()
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


def _watch_paths_status() -> list[Path]:
    """Paths to watch for status changes."""
    from keephive.storage import daily_file

    paths: list[Path] = [
        memory_file(),
        daily_dir(),
        daily_file(),
        daily_file(yesterday()),
        hive_dir() / ".pending-rules.md",
        hive_dir() / ".pending-facts.md",
        hive_dir() / ".stats.json",
        recurring_file(),
        guides_dir(),
    ]
    for n in range(1, NOTE_SLOT_COUNT + 1):
        paths.append(slot_file(n))
    return paths


_SPARK = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _cli_gauge(pct: int, width: int = 20) -> str:
    """Return exactly `width` chars: filled + empty blocks."""
    filled = round(pct / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _cli_sparkline(values: list[int], width: int = 24) -> str:
    """Return exactly `width` chars of sparkline."""
    if not values:
        return " " * width
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    elif len(values) < width:
        values = values + [0] * (width - len(values))
    mx = max(values) or 1
    return "".join(_SPARK[min(8, round(v / mx * 8))] for v in values)


def _render_status() -> None:
    """Render the status display using Rich Panels for card-based layout."""
    import re

    # ── Phase 1: Gather ALL data before rendering ──────────────────────
    mem = memory_file()
    stale = count_stale_facts()
    today_entries = count_daily_entries()
    yesterday_entries = count_daily_entries(yesterday())

    from keephive.health import health_summary

    hooks_ok, mcp_ok, data_ok = health_summary()
    prof = active_profile()

    llm_state = get_backend_state()
    settings_pref = get_setting("llm_backend") or "auto"
    env_override = os.environ.get("HIVE_LLM_BACKEND")
    backend_rows: list[tuple] = []
    healthy_backend_found = False
    for backend in available_backends():
        available, reason = backend.detect()
        backend_rows.append((backend, available, reason))
        if available and backend.name != "none":
            healthy_backend_found = True

    # Knowledge health
    kh: dict = {}
    try:
        from keephive.commands.stats import _calculate_streak, _knowledge_health

        kh = _knowledge_health()
    except Exception:
        pass

    # Stats + streak
    stats = read_stats()
    days_data = stats.get("days", {})
    curr_streak = 0
    try:
        from keephive.commands.stats import _calculate_streak

        curr_streak, _ = _calculate_streak(days_data)
    except Exception:
        pass

    # Session metrics from Claude Code session-meta (real data)
    msgs_today = 0
    msgs_week = 0
    lines_added = 0
    lines_removed = 0
    git_commits = 0
    try:
        from keephive.storage import session_metrics as _sm

        sm = _sm(days_back=7)
        msgs_today = sum(
            s.get("user_messages", 0)
            for s in __import__("keephive.storage", fromlist=["read_cc_sessions"]).read_cc_sessions(
                days_back=1
            )
        )
        msgs_week = sum(
            s.get("user_messages", 0)
            for s in __import__("keephive.storage", fromlist=["read_cc_sessions"]).read_cc_sessions(
                days_back=7
            )
        )
        lines_added = sm.get("lines_added_week", 0)
        lines_removed = sm.get("lines_removed_week", 0)
        git_commits = sm.get("git_commits_week", 0)
    except Exception:
        pass

    # Hourly sparkline for today
    today_str = get_today().isoformat()
    today_data = days_data.get(today_str, {})
    hourly_spark = ""
    try:
        from keephive.commands.stats import _hourly_sparkline

        today_hours = today_data.get("hours", {})
        if today_hours:
            hourly_spark = _hourly_sparkline(today_hours)
    except Exception:
        pass

    # Pulse score
    pulse_score: int | None = None
    try:
        from keephive.commands.audit import (
            _analyze_cleaner,
            _analyze_strategist,
            _analyze_vault,
            _compute_score,
        )

        vault = _analyze_vault()
        cleaner = _analyze_cleaner()
        strategist = _analyze_strategist()
        pulse_score = _compute_score(vault, cleaner, strategist)
    except Exception:
        pass

    # Collect warnings for Attention panel
    warnings: list[tuple[str, str]] = []

    if not healthy_backend_found:
        warnings.append(
            ("\u26a0", "No operational LLM backend detected (configure claude CLI or API key)")
        )

    if stale > 0:
        s = "s" if stale != 1 else ""
        warnings.append(("\u26a0", f"{stale} stale fact{s} (30+ days). Run: hive v"))

    # Old TODOs
    todos = open_todos()
    old_todos = 0
    if todos:
        cutoff_3d = (get_today() - timedelta(days=3)).isoformat()
        for d, _, _ in todos:
            if d < cutoff_3d:
                old_todos += 1
    if old_todos > 0:
        s = "s" if old_todos != 1 else ""
        warnings.append(("\u26a0", f"{old_todos} TODO{s} older than 3 days. Run: hive todo"))

    # Pending facts
    pending_facts_count = 0
    pending_facts_path = hive_dir() / ".pending-facts.md"
    if pending_facts_path.exists() and pending_facts_path.read_text().strip():
        pending_facts_count = sum(
            1 for ln in pending_facts_path.read_text().splitlines() if ln.strip().startswith("- ")
        )
    if pending_facts_count > 0:
        s = "s" if pending_facts_count != 1 else ""
        warnings.append(
            ("\u26a1", f"{pending_facts_count} fact{s} pending review. Run: hive mem review")
        )

    # Pending rules
    pending_rules_count = 0
    pending_rules_path = hive_dir() / ".pending-rules.md"
    if pending_rules_path.exists() and pending_rules_path.read_text().strip():
        pending_rules_count = sum(
            1 for ln in pending_rules_path.read_text().splitlines() if ln.strip().startswith("- ")
        )
    if pending_rules_count > 0:
        s = "s" if pending_rules_count != 1 else ""
        warnings.append(
            ("\u26a1", f"{pending_rules_count} rule suggestion{s} pending. Run: hive rule review")
        )

    # Quality pulse
    if pulse_score is not None and pulse_score < 70:
        warnings.append(("\u2726", f"Quality pulse {pulse_score}/100. Run: hive a"))

    # Guide updates
    try:
        from keephive.commands.setup import check_bundled_updates as _cbu

        _stale_guides = _cbu()
        if _stale_guides > 0:
            warnings.append(
                ("\u26a1", f"{_stale_guides} bundled guide(s) have updates. Run: hive setup")
            )
    except Exception:
        pass

    # Memory accumulation
    if mem.exists():
        from keephive.hooks.sessionstart import _accumulation_warnings

        for w in _accumulation_warnings(mem.read_text()):
            warnings.append(("\u26a0", w))

    # Reflect analysis
    try:
        from keephive.commands.reflect import get_pending_analysis

        pending_analysis = get_pending_analysis()
        if pending_analysis:
            add_count, contra_count = pending_analysis
            parts_nudge = []
            if add_count:
                parts_nudge.append(f"{add_count} addition{'s' if add_count != 1 else ''}")
            if contra_count:
                parts_nudge.append(
                    f"{contra_count} contradiction{'s' if contra_count != 1 else ''}"
                )
            warnings.append(
                ("\u26a1", f"reflect: {', '.join(parts_nudge)} ready. Run: hive rf apply")
            )
    except Exception:
        pass

    # Due recurring tasks
    due = due_recurring()
    if due:
        for freq, text, overdue in due:
            over_s = f"+{overdue}d overdue" if overdue > 0 else "due today"
            warnings.append(("\u26a0", f"[{freq}] [{over_s}] {text}. Run: hive recurring done"))

    # Unfinished audit action
    try:
        from keephive.commands.audit import _check_previous_play

        prev_play = _check_previous_play()
        if prev_play and not prev_play["completed"] and prev_play["age_days"] >= 2:
            warnings.append(
                (
                    "\u26a0",
                    f"Unfinished audit action ({prev_play['date']}): {prev_play['action']}. Run: hive a",
                )
            )
    except Exception:
        pass

    # ── Phase 2: Render ────────────────────────────────────────────────

    # Header (no panel, just version + health dots)
    console.print()
    prof_tag = f"  [dim]profile: {prof}[/dim]" if prof else ""
    console.print(f"[bold]keephive[/bold] v{__version__}{prof_tag}")

    def _dot(ok: bool, label: str) -> str:
        return f"[ok]\u25cf[/ok] {label}" if ok else f"[dim]\u25cb[/dim] {label}"

    health_parts = [_dot(hooks_ok, "hooks"), _dot(mcp_ok, "mcp"), _dot(data_ok, "data")]
    console.print(f"{'  '.join(health_parts)}")

    if is_disabled():
        console.print()
        console.print("  [bold red]⚠ DISABLED[/bold red]  [dim](hive off)[/dim]")
        console.print(
            "  [dim]All hooks, MCP tools, and daemon tasks are off. Run [bold]hive on[/bold] to re-enable.[/dim]"
        )
    elif is_llm_paused():
        console.print()
        console.print("  [bold yellow]⚠ LLM PAUSED[/bold yellow]  [dim](hive privacy on)[/dim]")
        console.print("  [dim]All API calls blocked. Run `hive privacy off` to resume.[/dim]")
    elif is_force_cli():
        console.print()
        console.print(
            "  [bold cyan]🔐 CLI-ONLY[/bold cyan]  [dim](hive privacy off to allow API)[/dim]"
        )
        console.print("  [dim]LLM calls route through claude -p only. API backends blocked.[/dim]")

    llm_table = Table(show_header=False, show_edge=False, box=None, padding=(0, 1))
    llm_table.add_column(justify="left")
    llm_table.add_column(justify="left")
    llm_table.add_row("[dim]settings[/dim]", f"[info]{settings_pref}[/info]")
    if is_force_cli():
        llm_table.add_row(
            "[dim]policy[/dim]",
            "[info]CLI-only[/info] [dim](API backends blocked)[/dim]",
        )
    if env_override:
        llm_table.add_row("[dim]env override[/dim]", env_override)
    if llm_state:
        sel = llm_state.get("selected", "?")
        src = llm_state.get("source", "auto")
        reason = llm_state.get("reason", "")
        llm_table.add_row("[dim]selected[/dim]", f"{sel} ({src}) {reason}")
        if llm_state.get("status") == "error" and not healthy_backend_found:
            err = llm_state.get("error", "unknown error")
            llm_table.add_row("[warn]last error[/warn]", err)
    for backend, available, reason in backend_rows:
        if backend.name == "none":
            continue
        status = "[ok]\u25cf[/ok]" if available else "[dim]\u25cb[/dim]"
        suffix = " [dim](tools)[/dim]" if backend.supports_tools else ""
        llm_table.add_row(f"{status} {backend.name}{suffix}", reason or "")
    specs = platform_specs()
    for slug, spec in specs.items():
        marker = "[ok]\u25cf[/ok]" if spec.detected else "[dim]\u25cb[/dim]"
        skill_state = "skill ✓" if spec.skill_path.exists() else "skill ×"
        hooks_state = "hooks ✓" if spec.hooks_ok else "hooks ×"
        llm_table.add_row(f"{marker} {spec.title}", f"{skill_state}, {hooks_state}")
    console.print(Panel(llm_table, title="LLM Backend", border_style="dim", padding=(0, 1)))

    # ── Metrics Panel (always shown) ──────────────────────────────────
    total_facts = kh.get("total_facts", 0)
    fresh_pct = kh.get("fresh_pct", 0)

    metrics_t = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
    metrics_t.add_column(justify="right")
    metrics_t.add_column(justify="left")
    metrics_t.add_column(justify="right")
    metrics_t.add_column(justify="left")
    metrics_t.add_column(justify="right")
    metrics_t.add_column(justify="left")
    metrics_t.add_column(justify="right")
    metrics_t.add_column(justify="left")

    pulse_str = f"{pulse_score}/100" if pulse_score is not None else "..."
    metrics_t.add_row(
        f"[bold]{total_facts}[/bold]",
        "facts",
        f"[bold]{fresh_pct:.0f}%[/bold]",
        "fresh",
        f"[bold]{curr_streak}d[/bold]",
        "streak",
        f"[bold]{pulse_str}[/bold]",
        "pulse",
    )

    # Activity line
    activity_parts = []
    if msgs_today > 0:
        activity_parts.append(f"[bold]{msgs_today}[/bold] msgs today")
    if msgs_week > 0:
        activity_parts.append(f"{msgs_week} this week")
    if lines_added > 0 or lines_removed > 0:
        activity_parts.append(f"[ok]+{lines_added}[/ok]/[warn]-{lines_removed}[/warn] lines")
    if git_commits > 0:
        activity_parts.append(f"{git_commits} commits")

    from rich.console import Group

    metrics_content_parts = [metrics_t]
    if activity_parts:
        activity_text = Text.from_markup("  ".join(activity_parts))
        metrics_content_parts.append(Text(""))
        metrics_content_parts.append(activity_text)
    if hourly_spark:
        metrics_content_parts.append(Text(f"  {hourly_spark}", style="dim"))

    console.print(
        Panel(Group(*metrics_content_parts), title="Metrics", border_style="dim", padding=(0, 1))
    )

    # ── Attention Panel (conditional) ─────────────────────────────────
    if warnings:
        att_t = Table(show_header=False, show_edge=False, box=None, padding=(0, 1))
        att_t.add_column(width=2)
        att_t.add_column()
        for icon, msg in warnings:
            style = "warn" if icon == "\u26a0" else "info" if icon == "\u26a1" else "dim"
            att_t.add_row(f"[{style}]{icon}[/{style}]", f"[{style}]{msg}[/{style}]")
        console.print(Panel(att_t, title="Attention", border_style="yellow", padding=(0, 1)))

    # ── TODOs Panel (conditional) ─────────────────────────────────────
    if todos:
        t = get_today()
        shown = list(reversed(todos[-3:]))
        todo_t = Table(show_header=False, show_edge=False, box=None, padding=(0, 1))
        todo_t.add_column(width=7, justify="right")
        todo_t.add_column()
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
            age_style = "warn" if age_s not in ("today", "1d", "?") else "dim"
            todo_t.add_row(f"[{age_style}]\\[{age_s}][/{age_style}]", text)
        overflow = ""
        if len(todos) > 3:
            overflow = f"  ... and {len(todos) - 3} more (hive todo)"
        content_parts = [todo_t]
        if overflow:
            content_parts.append(Text(overflow, style="dim"))
        console.print(
            Panel(
                Group(*content_parts),
                title=f"TODOs ({len(todos)} open)",
                border_style="dim",
                padding=(0, 1),
            )
        )

    # ── Today Panel (conditional) ─────────────────────────────────────
    entries = get_meaningful_entries()
    if entries:
        today_t = Table(show_header=False, show_edge=False, box=None, padding=(0, 1))
        today_t.add_column(width=7, justify="right", style="dim")
        today_t.add_column()
        for e in reversed(entries):
            # Parse out the timestamp and content from formatted entry
            # get_meaningful_entries returns "  ~ [HH:MM] text" or "  [HH:MM] text"
            raw = e.strip()
            if raw.startswith("~"):
                raw = raw[1:].strip()
            m = re.match(r"\[(\d{2}:\d{2})\]\s*(.*)", raw)
            if m:
                today_t.add_row(m.group(1), m.group(2))
            else:
                today_t.add_row("", raw)
        console.print(Panel(today_t, title="Today", border_style="dim", padding=(0, 1)))
    elif today_entries == 0:
        console.print(
            Panel("[dim]no entries yet[/dim]", title="Today", border_style="dim", padding=(0, 1))
        )

    # Lookback when today is thin
    if today_entries < 3 and yesterday_entries > 0:
        yday = yesterday()
        yday_entries = get_meaningful_entries(yday, limit=5)
        if yday_entries:
            yday_t = Table(show_header=False, show_edge=False, box=None, padding=(0, 1))
            yday_t.add_column(width=7, justify="right", style="dim")
            yday_t.add_column()
            for e in reversed(yday_entries):
                raw = e.strip()
                if raw.startswith("~"):
                    raw = raw[1:].strip()
                m = re.match(r"\[(\d{2}:\d{2})\]\s*(.*)", raw)
                if m:
                    yday_t.add_row(m.group(1), m.group(2))
                else:
                    yday_t.add_row("", raw)
            console.print(
                Panel(yday_t, title=f"Yesterday ({yday})", border_style="dim", padding=(0, 1))
            )

    # ── Draft Panel (conditional) ─────────────────────────────────────
    current_slot = active_slot()
    slot_path = slot_file(current_slot)
    if slot_path.exists() and slot_path.read_text().strip():
        slot_text = slot_path.read_text()
        words = len(slot_text.split())
        flat = slot_text.replace("\n", " ")
        preview = flat[:40] + ("..." if len(flat) > 40 else "")
        draft_line = f'slot {current_slot} \u00b7 "{preview}" ({words} words)'
        filled = sum(
            1
            for n in range(1, NOTE_SLOT_COUNT + 1)
            if slot_file(n).exists() and slot_file(n).read_text().strip()
        )
        slot_parts = [Text.from_markup(f"[info]{draft_line}[/info]")]
        if filled > 1:
            from keephive.commands.note import _slot_bar

            slot_parts.append(Text.from_markup(_slot_bar()))
        console.print(
            Panel(Group(*slot_parts), title="Active Note", border_style="dim", padding=(0, 1))
        )

    # ── Footer ────────────────────────────────────────────────────────
    next_cmd, next_reason = _suggest_next()
    console.print()
    if next_cmd:
        console.print(f"[bold]Next:[/bold] [info]{next_cmd}[/info]  ({next_reason})")
    else:
        console.print(f"[bold]Next:[/bold] [ok]{next_reason}[/ok]")
    console.print()
    console.print("[dim]hive v \u00b7 hive go \u00b7 hive l \u00b7 hive rf \u00b7 hive help[/dim]")


def cmd_status(args: list[str]) -> None:
    ensure_dirs()

    # JSON mode takes priority over watch
    if "--json" in args:
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

        from keephive.health import check_anthropic_memory, health_summary

        hooks_ok, mcp_ok, data_ok = health_summary()
        anthropic_mem = check_anthropic_memory()

        _llm_state = get_backend_state()
        llm_summary = {
            "settings": get_setting("llm_backend") or "auto",
            "env_override": os.environ.get("HIVE_LLM_BACKEND"),
            "state": _llm_state,
            "last_error": _llm_state.get("error")
            if _llm_state and _llm_state.get("status") == "error"
            else None,
            "backends": [
                {
                    "name": backend.name,
                    "available": detected,
                    "reason": reason,
                    "supports_tools": backend.supports_tools,
                    "supports_structured": backend.supports_structured,
                }
                for backend, detected, reason in ((b, *b.detect()) for b in available_backends())
                if backend.name != "none"
            ],
        }

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
                    "llm": llm_summary,
                    "disabled": is_disabled(),
                    "llm_paused": is_llm_paused(),
                    "force_cli": is_force_cli(),
                }
            )
        )
        return

    # Check for watch mode
    from keephive.watch import parse_watch_args, watch_loop

    remaining, watch, interval = parse_watch_args(args)
    if watch:
        watch_loop(_render_status, _watch_paths_status, interval)
        return

    _render_status()
