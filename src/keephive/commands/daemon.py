"""KingBee daemon — manages proactive background tasks."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta

# Daily feedback loop is right for a self-evolving system (see Reflexion, Shinn et al. 2023).
# The 7-day analysis *window* (for i in range(7)) is orthogonal — that's context depth, not cadence.
_SELF_IMPROVE_THROTTLE_DAYS = 1

from keephive.output import console
from keephive.storage import (
    daemon_config_file,
    daemon_pid_file,
    hive_dir,
    read_daemon_config,
    read_daemon_state,
    read_pending_improvements,
    write_daemon_state,
)

# ── CLI entry point ──────────────────────────────────────────────────


def cmd_daemon(args: list[str]) -> None:
    """hive daemon [start|stop|status|run <task>|enable <task>|disable <task>|edit|log]"""
    sub = args[0] if args else "status"
    dispatch = {
        "start": _start,
        "stop": _stop,
        "status": _status,
        "run": lambda: _run_task(args[1] if len(args) > 1 else ""),
        "enable": lambda: _enable_task(args[1] if len(args) > 1 else ""),
        "disable": lambda: _enable_task(args[1] if len(args) > 1 else "", enabled=False),
        "edit": _edit,
        "log": _log,
    }
    fn = dispatch.get(sub)
    if fn:
        fn()
    else:
        console.print(f"[err]Unknown subcommand: {sub}[/err]")
        console.print("Usage: hive daemon [start|stop|status|run <task>|enable <task>|disable <task>|edit|log]")


# ── Start / Stop ─────────────────────────────────────────────────────


def _start() -> None:
    if _is_running():
        pid = int(daemon_pid_file().read_text().strip())
        console.print(f"🐝 KingBee daemon already running (pid {pid})")
        return
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}}
    log_path = hive_dir() / "daemon.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "keephive", "daemon-loop"],
        stdout=subprocess.DEVNULL,
        stderr=open(log_path, "a"),
        start_new_session=True,
        env=env,
    )
    daemon_pid_file().write_text(str(proc.pid))
    console.print(f"🐝 KingBee daemon started (pid {proc.pid})")
    console.print(f"   log: {log_path}")


def _stop() -> None:
    pf = daemon_pid_file()
    if not pf.exists() or not _is_running():
        console.print("[dim]No daemon running[/dim]")
        pf.unlink(missing_ok=True)
        return
    pid = int(pf.read_text().strip())
    os.kill(pid, signal.SIGTERM)
    pf.unlink(missing_ok=True)
    console.print(f"🐝 KingBee daemon stopped (pid {pid})")


def _is_running() -> bool:
    pf = daemon_pid_file()
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


# ── Status ───────────────────────────────────────────────────────────


def _status() -> None:
    from rich.table import Table

    from keephive.clock import get_now

    running = _is_running()
    status_str = "● running" if running else "○ stopped"
    pid_str = ""
    if running:
        pid = daemon_pid_file().read_text().strip()
        pid_str = f"  (pid {pid})"

    console.print("\n  🐝 [bold]KingBee Daemon[/bold]")
    if running:
        console.print(f"  Status: [green]{status_str}[/green]{pid_str}\n")
    else:
        console.print(f"  Status: [dim]{status_str}[/dim]{pid_str}\n")

    config = read_daemon_config()
    state = read_daemon_state()
    now = get_now()

    table = Table(box=None, padding=(0, 2))
    table.add_column("Task", style="bold")
    table.add_column("Status")
    table.add_column("Next Run")

    for task_name, task_config in config.get("tasks", {}).items():
        if task_name in ("soul-update", "self-improve"):
            continue  # shown separately below
        enabled = task_config.get("enabled", False)
        last_run = state.get(task_name, {}).get("last_run")
        last_str = f"✓ ran {_fmt_run(last_run)}" if last_run else "never run"
        next_str = _next_run_str(task_name, task_config, state, now) if enabled else "disabled"
        table.add_row(
            task_name,
            last_str if enabled else "[dim]disabled[/dim]",
            next_str,
        )

    # Hook-triggered tasks shown separately
    su_last = state.get("soul-update", {}).get("last_run")
    si_last = state.get("self-improve", {}).get("last_run")
    pending_count = len(read_pending_improvements())
    table.add_row(
        "soul-update",
        f"✓ ran {_fmt_run(su_last)}" if su_last else "never run",
        "PreCompact (if data) + SessionEnd",
    )
    table.add_row(
        "self-improve",
        f"✓ ran {_fmt_run(si_last)}" if si_last else "never run",
        f"daily throttle  ({pending_count} pending)",
    )
    console.print(table)
    console.print(f"\n  log: {hive_dir() / 'daemon.log'}\n")


# ── Run a task on demand ─────────────────────────────────────────────


def _run_task(task_name: str) -> None:
    if not task_name:
        console.print("[err]Usage: hive daemon run <task-name>[/err]")
        return

    status_msg = f"🐝 KingBee: {task_name} in progress..."
    if task_name == "soul-update":
        status_msg = "🐝 KingBee: distilling session patterns into SOUL.md..."

    with console.status(status_msg, spinner="dots"):
        did_work = _execute_task(task_name)

    if did_work:
        _mark_last_run(task_name)
        console.print(f"🐝 Done: {task_name}")
    else:
        console.print(f"🐝 {task_name}: skipped (throttled or no data)")


# ── Enable / Disable tasks ───────────────────────────────────────────


def _enable_task(task_name: str, enabled: bool = True) -> None:
    if not task_name:
        verb = "enable" if enabled else "disable"
        console.print(f"[err]Usage: hive daemon {verb} <task-name>[/err]")
        return
    config = read_daemon_config()
    tasks = config.get("tasks", {})
    if task_name not in tasks:
        console.print(f"[err]Unknown task: {task_name}[/err]")
        console.print(f"  Known tasks: {', '.join(tasks)}")
        return
    tasks[task_name]["enabled"] = enabled
    daemon_config_file().write_text(json.dumps(config, indent=2))
    verb = "enabled" if enabled else "disabled"
    console.print(f"🐝 {task_name}: {verb}")


# ── Edit / Log ───────────────────────────────────────────────────────


def _edit() -> None:
    import subprocess as sp

    editor = os.environ.get("EDITOR", "vi")
    path = daemon_config_file()
    if not path.exists():
        from keephive.commands.setup import _DAEMON_DEFAULT_CONFIG

        path.write_text(_DAEMON_DEFAULT_CONFIG)
    sp.run([editor, str(path)])


def _log() -> None:
    log = hive_dir() / "daemon.log"
    if not log.exists():
        console.print("[dim]No daemon log yet[/dim]")
        return
    lines = log.read_text().splitlines()
    for line in lines[-50:]:
        console.print(line)


# ── Daemon Loop (internal — not user-facing) ─────────────────────────


def cmd_daemon_loop(_args: list[str]) -> None:
    """Internal: run the background task loop. Spawned by 'hive daemon start'."""
    _log_daemon("KingBee daemon loop started")
    while True:
        try:
            _tick()
        except Exception as e:
            _log_daemon(f"error in tick: {e}")
        time.sleep(60)


def _tick() -> None:
    from keephive.clock import get_now

    config = read_daemon_config()
    state = read_daemon_state()
    now = get_now()

    for task_name, task_config in config.get("tasks", {}).items():
        if task_name in ("soul-update", "self-improve"):
            continue  # triggered by hooks, not tick
        if not task_config.get("enabled", False):
            continue
        if _is_task_due(task_name, task_config, state, now):
            _log_daemon(f"running task: {task_name}")
            try:
                _execute_task(task_name)
                _mark_last_run(task_name)
                _log_daemon(f"completed: {task_name}")
            except Exception as e:
                _log_daemon(f"task failed: {task_name}: {e}")


def _is_task_due(task_name: str, config: dict, state: dict, now: datetime) -> bool:
    last_run_str = state.get(task_name, {}).get("last_run")
    task_time_str = config.get("time", "07:00")
    task_hour, task_minute = (int(x) for x in task_time_str.split(":"))

    if "day" in config:
        target_day = config["day"].lower()
        day_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        target_weekday = day_map.get(target_day, 0)
        if now.weekday() != target_weekday:
            return False

    task_time_today = now.replace(hour=task_hour, minute=task_minute, second=0, microsecond=0)
    if now < task_time_today:
        return False

    if last_run_str:
        last_run = datetime.fromisoformat(last_run_str)
        if last_run >= task_time_today:
            return False

    return True


def _mark_last_run(task_name: str) -> None:
    state = read_daemon_state()
    state.setdefault(task_name, {})["last_run"] = datetime.now().isoformat()
    write_daemon_state(state)


# ── Task Implementations ─────────────────────────────────────────────


def _execute_task(task_name: str) -> bool:
    dispatch = {
        "morning-briefing": _task_morning_briefing,
        "stale-check": _task_stale_check,
        "standup-draft": _task_standup_draft,
        "soul-update": _task_soul_update,
        "self-improve": _task_self_improve,
    }
    fn = dispatch.get(task_name)
    if fn:
        return fn() or False  # coerce None → False
    _log_daemon(f"unknown task: {task_name}")
    return False


def _task_morning_briefing() -> bool:
    """Read yesterday's log + pending counts. Write briefing to today's log."""
    from datetime import timedelta

    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.clock import get_now, get_today
    from keephive.models import MorningBriefingResponse
    from keephive.storage import (
        append_to_daily,
        daily_file,
        read_pending_improvements,
        safe_read_text,
    )

    today = get_today()
    yesterday = today - timedelta(days=1)
    yesterday_path = daily_file(yesterday.isoformat())
    yesterday_log = safe_read_text(yesterday_path) if yesterday_path.exists() else ""

    pending_facts_count = 0
    pf_path = hive_dir() / ".pending-facts.md"
    if pf_path.exists():
        pending_facts_count = sum(
            1 for ln in safe_read_text(pf_path).splitlines() if ln.strip().startswith("- ")
        )

    pending_rules_count = 0
    pr_path = hive_dir() / ".pending-rules.md"
    if pr_path.exists():
        pending_rules_count = sum(
            1 for ln in safe_read_text(pr_path).splitlines() if ln.strip().startswith("- ")
        )

    pending_impr = len(read_pending_improvements())

    todos_path = hive_dir() / "TODO.md"
    todos = safe_read_text(todos_path)[:500] if todos_path.exists() else "(none)"

    prompt = f"""You are KingBee, the keephive agent. Write a morning briefing.
Voice: sarcastic but useful, proactive, directness 85%.
Under 150 words. Start with what matters from yesterday.

Yesterday's log:
{yesterday_log[-2000:] if yesterday_log else "(empty)"}

Active TODOs:
{todos}

Pending queues (include as action items if non-zero):
- pending facts: {pending_facts_count}  (hive mem review)
- pending rules: {pending_rules_count}  (hive rule review)
- pending improvements: {pending_impr}  (hive improve review)

Write the briefing. Each point on its own line. Include pending queue summary at end.
No markdown headers."""

    try:
        result = run_claude_pipe(prompt, MorningBriefingResponse, model="haiku")
        if result and result.content:
            ts = get_now().strftime("%H:%M")
            entry = f"\n[🐝 KingBee {ts}] morning briefing\n{result.content}\n"
            append_to_daily(entry)
    except ClaudePipeError as e:
        _log_daemon(f"morning-briefing failed: {e}")
    return True


def _task_stale_check() -> bool:
    """Review memory.md for potentially stale facts. Write warnings to daily log."""
    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.clock import get_now
    from keephive.models import StaleCheckResponse
    from keephive.storage import append_to_daily, safe_read_text

    memory_path = hive_dir() / "memory.md"
    if not memory_path.exists():
        return False

    memory = safe_read_text(memory_path)
    prompt = f"""You are KingBee. Review these memory.md facts for staleness.
Flag any that are: older than 30 days about fast-moving code, contradictory, or likely outdated.
Be brief. One line per flagged fact. If nothing is stale, say so.

Memory:
{memory[:3000]}"""

    try:
        result = run_claude_pipe(prompt, StaleCheckResponse, model="haiku")
        if result and result.content:
            ts = get_now().strftime("%H:%M")
            entry = f"\n[🐝 KingBee {ts}] stale-check\n{result.content}\n"
            append_to_daily(entry)
    except ClaudePipeError as e:
        _log_daemon(f"stale-check failed: {e}")
    return True


def _task_standup_draft() -> bool:
    """Read recent logs. Write standup draft to today's log."""
    from keephive.clock import get_now
    from keephive.storage import append_to_daily

    try:
        from keephive.commands.standup import _display_deterministic, _gather_raw_data

        data = _gather_raw_data()
        content = _display_deterministic(data)
        if content and content.strip():
            ts = get_now().strftime("%H:%M")
            entry = f"\n[🐝 KingBee {ts}] standup draft\n{content}\n"
            append_to_daily(entry)
    except Exception as e:
        _log_daemon(f"standup-draft failed: {e}")
    return True


def _task_soul_update() -> bool:
    """Read today's log. Update SOUL.md ## Summary + ## Session Patterns.

    Does exactly one thing: update SOUL.md. Self-improve is a separate process.
    Throttled: max once per hour (PreCompact fires this mid-session; SessionEnd
    fires it at exit — without throttle a long session would run it many times).

    Returns True when SOUL.md is written, False in all other paths.
    Caller (_run_task) handles _mark_last_run when True is returned.
    """
    from datetime import datetime, timedelta

    # Throttle: max once per hour across all callers (PreCompact + SessionEnd)
    state = read_daemon_state()
    last_run_str = state.get("soul-update", {}).get("last_run")
    if last_run_str:
        last_run_dt = datetime.fromisoformat(last_run_str)
        if (datetime.now() - last_run_dt) < timedelta(hours=1):
            return False

    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.clock import get_today
    from keephive.models import SoulUpdateResponse
    from keephive.storage import daily_file, read_soul, safe_read_text, soul_file

    today = get_today()
    today_log_path = daily_file(today.isoformat())
    today_log = safe_read_text(today_log_path) if today_log_path.exists() else ""

    # Expansion: if today is thin, pull in yesterday for continuity
    yesterday_log = ""
    if len(today_log) < 1000:
        yday = today - timedelta(days=1)
        yday_path = daily_file(yday.isoformat())
        if yday_path.exists():
            yesterday_log = safe_read_text(yday_path)

    current_soul = read_soul()
    if not today_log and not yesterday_log:
        return False

    prompt = f"""You are KingBee updating your own SOUL.md after recent sessions.
Review the logs and rewrite SOUL.md with STRICT size budgets.

HARD SIZE CONSTRAINTS — this document must stay bounded:
- ## Summary: max 300 tokens. Rewrite to reflect what you now know. Dense, specific.
- ## What I've Learned About How To Help You: max 5 bullet points.
- ## Session Patterns I've Noticed: max 5 entries. UPDATE existing patterns in-place.
- ## Last Updated: one line — today's date + what triggered the update.
- Total document: target ~500 tokens, hard limit 800 tokens.

This is a REPLACE operation, not an APPEND. You are distilling, not journaling.
If the logs have no new signal, return the current SOUL.md unchanged.
Write in first person, your voice (sarcastic, direct, knows things).

Recent logs:
{yesterday_log[-2000:] if yesterday_log else ""}
{today_log[-3000:]}

Current SOUL.md:
{current_soul[:2000] if current_soul else "(empty)"}

Return the complete updated SOUL.md content (bounded, distilled, not expanded)."""

    try:
        result = run_claude_pipe(prompt, SoulUpdateResponse, model="sonnet")
        if result and result.content:
            soul_file().write_text(result.content)
            _log_daemon("SOUL.md updated")
            return True  # caller handles _mark_last_run
        return False
    except ClaudePipeError as e:
        _log_daemon(f"soul-update failed: {e}")
        return False


def _task_self_improve() -> bool:
    """Analyze patterns across recent sessions. Propose skills, tasks, and rules.

    Time-throttled: max once per day. Queue-depth-capped: stops at 20 pending.
    Appends to existing queue (never replaces). Deduplicates against existing pending.

    Returns True when LLM ran (with or without proposals), False when throttled/skipped.
    Caller (_run_task) handles _mark_last_run when True is returned.
    """
    from datetime import timedelta

    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.clock import get_today
    from keephive.models import ImprovementResponse
    from keephive.storage import (
        append_pending_improvements,
        daily_file,
        guides_dir,
        read_dismissed_improvements,
        read_pending_improvements,
        safe_read_text,
    )

    # ── Time throttle: max once per day ──────────────────────────────
    state = read_daemon_state()
    last_run_str = state.get("self-improve", {}).get("last_run")
    if last_run_str:
        days_since = (datetime.now() - datetime.fromisoformat(last_run_str)).days
        if days_since < _SELF_IMPROVE_THROTTLE_DAYS:
            return False  # silent — throttle skip is not a diagnostic event

    # ── Depth cap: don't pile on if user hasn't reviewed ─────────────
    existing = read_pending_improvements()
    if len(existing) >= 20:
        _log_daemon(f"self-improve: queue at {len(existing)} items — review before new proposals")
        return False

    today = get_today()

    # ── Read last 7 days of logs ──────────────────────────────────────
    recent_log_parts = []
    for i in range(7):
        day = today - timedelta(days=i)
        p = daily_file(day.isoformat())
        if p.exists():
            recent_log_parts.append(f"--- {day.isoformat()} ---\n{safe_read_text(p)[-800:]}")
    recent_logs = "\n\n".join(recent_log_parts)

    if not recent_logs.strip():
        return False  # Nothing to analyze

    # ── Scan note slots (0-9) ─────────────────────────────────────────
    from keephive.storage import slot_file

    note_summaries = []
    for slot in range(10):
        np = slot_file(slot)
        if np.exists():
            content = safe_read_text(np).strip()
            if content:
                preview = content[:200].replace("\n", " ")
                note_summaries.append(f"slot {slot}: {preview}")

    # ── Stale TODOs (>30 days open) ───────────────────────────────────
    from keephive.storage import open_todos

    stale_todos = []
    try:
        todos = open_todos()
        for date_str, _ts, text in todos:
            try:
                from datetime import date as _date

                todo_date = _date.fromisoformat(date_str)
                age_days = (today - todo_date).days
                if age_days > 30:
                    stale_todos.append(f"[{age_days}d] {text[:80]}")
            except ValueError:
                pass
    except Exception:
        pass

    # ── Orphan guide hints ────────────────────────────────────────────
    gd = guides_dir()
    orphan_guide_hints = []
    if gd.exists():
        guide_list = list(gd.glob("*.md"))
        if len(guide_list) > 3:
            for sk in guide_list:
                slug = sk.stem
                mentions = sum(1 for log in recent_log_parts if slug in log)
                if mentions == 0:
                    orphan_guide_hints.append(f"{slug} (0 mentions in 7 days)")

    # ── Context for dedup ─────────────────────────────────────────────
    skill_excerpts = {}
    if gd.exists():
        for sk in gd.glob("*.md"):
            text = safe_read_text(sk)
            skill_excerpts[sk.stem] = text[:300].replace("\n", " ")
    existing_tasks = list(read_daemon_config().get("tasks", {}).keys())
    pending_summary = [
        f"{item.get('type', '?')}: {item.get('name', item.get('rule', ''))[:60]}"
        for item in existing
    ]
    dismissed = read_dismissed_improvements()
    dismissed_summary = [
        f"{item.get('type', '?')}: {item.get('name', '')[:60]}" for item in dismissed[-20:]
    ]

    prompt = f"""You are KingBee analyzing your own effectiveness over the last 7 days.

You can propose NEW improvements OR refine existing ones. Propose at most 4 items total. Quality over quantity. Silence is acceptable.

**For NEW items** (proposed_skills / proposed_tasks / proposed_rules):
- Only propose when there is strong evidence in the logs (friction patterns, repeated manual work, missed capture)
- Do NOT re-propose anything in "Already pending" or "User dismissed" lists

**For EDITS to existing items** (proposed_edits with action = 'edit' | 'prune' | 'merge'):
- edit: skill content is stale, unclear, or violates observed patterns
- prune: skill/task/rule is routinely ignored or made redundant
- merge: two skills are always used together or cover overlapping ground
- Evidence standard: 2+ sessions showing the problem.

Do NOT propose anything the user has previously dismissed.
IMPORTANT: All proposals go through hive improve review. You do not act directly.

Existing skills (slug -> excerpt): {skill_excerpts if skill_excerpts else "(none)"}
Existing daemon tasks: {existing_tasks}
Note slots with content: {note_summaries if note_summaries else "(all empty)"}
Stale TODOs (>30 days open): {stale_todos if stale_todos else "(none)"}
Guides with 0 log mentions (potential orphans): {orphan_guide_hints if orphan_guide_hints else "(none)"}
Already pending (DO NOT re-propose): {pending_summary if pending_summary else "(none)"}
User dismissed — do not re-propose: {dismissed_summary if dismissed_summary else "(none)"}

Last 7 days of logs:
{recent_logs[:4000]}

Respond with JSON matching the ImprovementResponse schema.
If nothing warrants a proposal, return all empty lists with summary "No patterns this week."
"""

    try:
        result = run_claude_pipe(prompt, ImprovementResponse, model="sonnet")
        if result:
            new_items: list[dict] = []
            for skill in result.proposed_skills:
                new_items.append(
                    {
                        "type": "skill",
                        "name": skill.name,
                        "rationale": skill.rationale,
                        "content": skill.content,
                    }
                )
            for task in result.proposed_tasks:
                new_items.append(
                    {
                        "type": "task",
                        "name": task.name,
                        "rationale": task.rationale,
                        "config": task.config,
                    }
                )
            for rule in result.proposed_rules:
                new_items.append(
                    {
                        "type": "rule",
                        "rationale": rule.rationale,
                        "rule": rule.rule,
                    }
                )
            for edit in result.proposed_edits:
                new_items.append(
                    {
                        "type": "edit",
                        "action": edit.action,
                        "target_type": edit.target_type,
                        "name": edit.target_name,
                        "rationale": edit.rationale,
                        "changes": edit.changes,
                        "merge_with": edit.merge_with,
                    }
                )

            if new_items:
                append_pending_improvements(new_items)
                _log_daemon(
                    f"self-improve: {len(new_items)} proposals appended "
                    f"(queue now {len(existing) + len(new_items)})"
                )
            else:
                _log_daemon("self-improve: no new proposals this cycle")
            return True  # caller handles _mark_last_run
        return False  # run_claude_pipe returned None
    except ClaudePipeError as e:
        _log_daemon(f"self-improve failed: {e}")
        return False


# ── Helpers ──────────────────────────────────────────────────────────


def _log_daemon(msg: str) -> None:
    from keephive.clock import get_now

    ts = get_now().strftime("%Y-%m-%d %H:%M:%S")
    log = hive_dir() / "daemon.log"
    with log.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def _fmt_run(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso)
        from keephive.clock import get_today

        if dt.date() == get_today():
            return f"today {dt.strftime('%H:%M')}"
        return dt.strftime("%b %d")
    except ValueError:
        return iso


def _next_run_str(task_name: str, config: dict, state: dict, now: datetime) -> str:
    task_time_str = config.get("time", "07:00")
    task_hour, task_minute = (int(x) for x in task_time_str.split(":"))

    if "day" in config:
        day_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        target = day_map.get(config["day"].lower(), 0)
        days_ahead = (target - now.weekday()) % 7 or 7
        next_dt = (now + timedelta(days=days_ahead)).replace(
            hour=task_hour, minute=task_minute, second=0
        )
        return next_dt.strftime("%a %b %d %H:%M")

    next_today = now.replace(hour=task_hour, minute=task_minute, second=0)
    if now < next_today:
        return next_today.strftime("today %H:%M")
    next_dt = (now + timedelta(days=1)).replace(hour=task_hour, minute=task_minute, second=0)
    return next_dt.strftime("%b %d %H:%M")
