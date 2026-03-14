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
_REFLECT_DRAFT_THROTTLE_DAYS = 7

_VOICE_DISCIPLINE = """\

Voice constraints (apply to all output):
- No markdown headers (##, ###) unless the output IS a document
- No "Here is", "Here's", "This is", "I'll", "I will" openers
- No hedging: not "might", "could", "possibly", "perhaps"
- No empty affirmations: not "Great!", "Sure!", "Certainly!"
- State facts. Do not narrate your reasoning process."""

_VOICE_DISCIPLINE_SILENCE_OK = (
    _VOICE_DISCIPLINE
    + """
- Silence is valid. Zero output beats filler output."""
)

from keephive.output import console  # noqa: E402
from keephive.storage import (  # noqa: E402
    daemon_config_file,
    daemon_pid_file,
    hive_dir,
    read_daemon_config,
    read_daemon_state,
    read_pending_improvements,
    track_event,
    write_daemon_state,
)

# Default config entries for tasks that may not exist in older daemon.json files.
# Used by _enable_task to auto-register a task on first enable.
_TASK_DEFAULTS: dict[str, dict] = {
    "morning-briefing": {"enabled": False, "time": "07:00"},
    "stale-check": {"enabled": False, "day": "monday", "time": "08:00"},
    "standup-draft": {"enabled": False, "time": "17:00"},
    "soul-update": {"enabled": False},
    "self-improve": {"enabled": False},
    "wander": {"enabled": False, "time": "14:00"},
    "reflect-draft": {"enabled": False, "day": "friday", "time": "18:00"},
}

# Tasks enabled in a fresh install. All others start disabled.
_ENABLED_BY_DEFAULT: frozenset[str] = frozenset({"soul-update", "self-improve"})

# Canonical default config for daemon.json, derived from _TASK_DEFAULTS.
# setup.py and _edit() both reference this — single source of truth.
_DAEMON_DEFAULT_CONFIG: str = json.dumps(
    {
        "tasks": {
            name: {**defaults, "enabled": name in _ENABLED_BY_DEFAULT}
            for name, defaults in _TASK_DEFAULTS.items()
        }
    },
    indent=2,
)

# Per-process guard sets — live for daemon lifetime, no persistence needed.
# _unknown_tasks_seen: prevents log spam when daemon.json has a task the running
#   code doesn't know (e.g. wander enabled before its handler was shipped).
# _running_tasks: prevents double-execution when a slow task is still in flight
#   if the daemon loop ticks again (e.g. after a restart race).
_unknown_tasks_seen: set[str] = set()
_running_tasks: set[str] = set()

# ── CLI entry point ──────────────────────────────────────────────────


def cmd_daemon(args: list[str]) -> None:
    """hive daemon [start|stop|status|run <task>|enable <task>|disable <task>|edit|log]"""
    sub = args[0] if args else "status"
    dispatch = {
        "start": _start,
        "stop": _stop,
        "status": _status,
        "run": lambda: _run_task(args[1] if len(args) > 1 else ""),
        "r": lambda: _run_task(args[1] if len(args) > 1 else ""),
        "enable": lambda: _enable_task(args[1] if len(args) > 1 else ""),
        "en": lambda: _enable_task(args[1] if len(args) > 1 else ""),
        "disable": lambda: _enable_task(args[1] if len(args) > 1 else "", enabled=False),
        "dis": lambda: _enable_task(args[1] if len(args) > 1 else "", enabled=False),
        "edit": _edit,
        "log": _log,
        "l": _log,
        "upgrade": _upgrade,
    }
    fn = dispatch.get(sub)
    if fn:
        fn()
    else:
        console.print(f"[err]Unknown subcommand: {sub}[/err]")
        console.print(
            "Usage: hive daemon [start|stop|status|run <task>|enable <task>|disable <task>|edit|log|upgrade]"
        )


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
        console.print(f"  Status: [ok]{status_str}[/ok]{pid_str}\n")
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

    # Bypass throttle only when invoked from a real terminal (explicit user intent).
    # Hooks spawn with stdout=DEVNULL and sys.stdout.isatty() == False -- they
    # must respect the throttle to prevent a process storm on concurrent sessions.
    if sys.stdout.isatty():
        state = read_daemon_state()
        if task_name in state and "last_run" in state[task_name]:
            del state[task_name]["last_run"]
            write_daemon_state(state)

    status_msg = f"🐝 KingBee: {task_name} in progress..."
    if task_name == "soul-update":
        status_msg = "🐝 KingBee: distilling session patterns into SOUL.md..."

    with console.status(status_msg, spinner="dots"):
        try:
            did_work = _execute_task(task_name)
        except Exception:
            _mark_last_run(task_name)  # stamp even on failure - prevents infinite retry
            raise

    if did_work:
        _mark_last_run(task_name)
        console.print(f"🐝 Done: {task_name}")
    else:
        console.print(f"🐝 {task_name}: skipped (no data)")


# ── Enable / Disable tasks ───────────────────────────────────────────


def _enable_task(task_name: str, enabled: bool = True) -> None:
    if not task_name:
        verb = "enable" if enabled else "disable"
        console.print(f"[err]Usage: hive daemon {verb} <task-name>[/err]")
        return
    config = read_daemon_config()
    tasks = config.setdefault("tasks", {})
    if task_name not in tasks:
        if task_name not in _TASK_DEFAULTS:
            console.print(f"[err]Unknown task: {task_name}[/err]")
            console.print(f"  Known tasks: {', '.join(sorted(_TASK_DEFAULTS))}")
            return
        # Auto-register task with default config (handles upgrade from older daemon.json)
        tasks[task_name] = dict(_TASK_DEFAULTS[task_name])
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


def _sync_daemon_tasks() -> list[str]:
    """Register any tasks from _TASK_DEFAULTS not yet in daemon.json.

    Returns the names of tasks that were added. Existing tasks are never
    modified — user customizations (e.g. custom time:) are preserved.
    """
    config = read_daemon_config()
    tasks = config.setdefault("tasks", {})
    added = []
    for task_name, defaults in _TASK_DEFAULTS.items():
        if task_name not in tasks:
            tasks[task_name] = dict(defaults)
            added.append(task_name)
    if added:
        daemon_config_file().write_text(json.dumps(config, indent=2))
    return added


def _upgrade() -> None:
    """Sync daemon.json with _TASK_DEFAULTS, then restart the daemon."""
    added = _sync_daemon_tasks()
    if added:
        console.print(f"🐝 synced new task(s): {', '.join(sorted(added))}")
        console.print(f"  hint: hive daemon run {added[0]}")
    else:
        console.print("🐝 daemon.json already up to date")
    _stop()
    _start()


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
    from keephive.storage import read_custom_task_queue

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
                did_work = _execute_task(task_name)
                if did_work:
                    _mark_last_run(task_name)
                    _log_daemon(f"completed: {task_name}")
                else:
                    # Mark last_run even on skip/failure so daily tasks
                    # don't retry every tick (retry tomorrow instead).
                    _mark_last_run(task_name)
                    _log_daemon(f"skipped (no data): {task_name}")
            except Exception as e:
                _mark_last_run(task_name)
                _log_daemon(f"task failed: {task_name}: {e}")

    # Custom task queue (hive run --at / --tonight)
    try:
        for task in read_custom_task_queue():
            if task.get("status") == "queued" and _is_custom_task_due(task):
                _execute_custom_task(task)
    except Exception as e:
        _log_daemon(f"custom task queue error: {e}")


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
            # Priority boost can override day-of-week constraint
            if not _has_priority_boost(task_name):
                return False

    task_time_today = now.replace(hour=task_hour, minute=task_minute, second=0, microsecond=0)
    if now < task_time_today:
        return False

    if last_run_str:
        last_run = datetime.fromisoformat(last_run_str)
        if last_run >= task_time_today:
            return False

    return True


def _has_priority_boost(task_name: str) -> bool:
    """Check if a task has an active (non-expired) priority boost from daemon hints."""
    from keephive.clock import get_today
    from keephive.storage import read_daemon_hints

    hints = read_daemon_hints()
    if not hints:
        return False

    # Check expiry
    expires_str = hints.get("expires", "")
    if expires_str:
        from datetime import date

        try:
            if get_today() > date.fromisoformat(expires_str):
                return False
        except ValueError:
            return False

    boosts = hints.get("priority_boost", {})
    return boosts.get(task_name, 1.0) > 1.0


def _mark_last_run(task_name: str) -> None:
    state = read_daemon_state()
    state.setdefault(task_name, {})["last_run"] = datetime.now().isoformat()
    write_daemon_state(state)


def _is_no_backend_error(exc: Exception) -> bool:
    """Return True when no LLM backend is available (permanent daemon condition).

    Daemon processes run outside CC sessions — there is no claude socket.
    BackendNotAvailable is not transient: retrying every 60 seconds produces a
    storm (295 failures observed for reflect-draft on 2026-02-27).  Tasks that
    detect this condition should return True (ran, nothing to retry) rather than
    False (retry next tick).
    """
    from keephive.llm.exceptions import BackendNotAvailable

    return isinstance(exc, BackendNotAvailable)


# ── Task Implementations ─────────────────────────────────────────────


def _execute_task(task_name: str) -> bool:
    # Bug fix: prevent double-execution if daemon restarts while a slow task is in flight.
    if task_name in _running_tasks:
        return False
    _running_tasks.add(task_name)
    try:
        dispatch = {
            "morning-briefing": _task_morning_briefing,
            "stale-check": _task_stale_check,
            "standup-draft": _task_standup_draft,
            "soul-update": _task_soul_update,
            "self-improve": _task_self_improve,
            "wander": _task_wander,
            "reflect-draft": _task_reflect_draft,
        }
        fn = dispatch.get(task_name)
        if fn:
            result = fn() or False  # coerce None → False
            if result:
                track_event("daemon_tasks", task_name)
                # After self-improve queues proposals, immediately apply any trusted ones
                if task_name == "self-improve":
                    try:
                        from keephive.commands.improve import _run_auto_apply

                        applied = _run_auto_apply()
                        if applied:
                            _log_daemon(f"self-improve: auto-applied {applied} trusted proposal(s)")
                    except Exception as exc:
                        _log_daemon(f"self-improve: auto-apply error: {exc}")
            return result
        # Bug fix: unknown task — log once per daemon lifetime, skip silently thereafter.
        if task_name not in _unknown_tasks_seen:
            _log_daemon(f"WARNING: unknown task '{task_name}' — upgrade code or remove from config")
            _unknown_tasks_seen.add(task_name)
        return False
    finally:
        _running_tasks.discard(task_name)


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
    todos = safe_read_text(todos_path)[:1200] if todos_path.exists() else "(none)"

    prompt = f"""You are KingBee, the keephive agent. Write a morning briefing.
Voice: sarcastic but useful, proactive, directness 85%.
Under 150 words. Start with what matters from yesterday.

Yesterday's log:
{yesterday_log[-2000:] if yesterday_log else "(empty)"}

Active TODOs:
{todos}

If any TODO above appears completed or superseded by yesterday's log entries,
flag it explicitly as a line: → DONE? [todo text]
The user will confirm these via `hive todo done`. Do not mark anything done yourself.

Pending queues (include as action items if non-zero):
- pending facts: {pending_facts_count}  (hive mem review)
- pending rules: {pending_rules_count}  (hive rule review)
- pending improvements: {pending_impr}  (hive improve review)

Write the briefing. Each point on its own line. Include pending queue summary at end.
{_VOICE_DISCIPLINE}"""

    try:
        result = run_claude_pipe(prompt, MorningBriefingResponse, model="haiku", timeout=240)
        if result and result.content:
            ts = get_now().strftime("%H:%M")
            entry = f"\n[🐝 KingBee {ts}] morning briefing\n{result.content}\n"
            append_to_daily(entry)
    except ClaudePipeError as e:
        _log_daemon(f"morning-briefing failed: {e}")
        return True if _is_no_backend_error(e) else False
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
One line per flagged fact. If nothing is stale, write exactly one line: "Memory is clean."
Always write at least one line of output.
{_VOICE_DISCIPLINE}

Memory:
{memory[:3000]}"""

    try:
        result = run_claude_pipe(prompt, StaleCheckResponse, model="haiku", timeout=240)
        if result and result.content:
            ts = get_now().strftime("%H:%M")
            entry = f"\n[🐝 KingBee {ts}] stale-check\n{result.content}\n"
            append_to_daily(entry)
    except ClaudePipeError as e:
        _log_daemon(f"stale-check failed: {e}")
        return True if _is_no_backend_error(e) else False
    return True


def _task_standup_draft() -> bool:
    """Read recent logs. Write LLM standup draft to today's log."""
    from keephive.clock import get_now
    from keephive.storage import append_to_daily

    try:
        from keephive.commands.standup import _display_llm, _gather_raw_data

        data = _gather_raw_data()
        content = _display_llm(data)
        if content and content.strip():
            ts = get_now().strftime("%H:%M")
            entry = f"\n[🐝 KingBee {ts}] standup draft\n{content}\n"
            append_to_daily(entry)
            return True
        return False
    except Exception as e:
        _log_daemon(f"standup-draft failed: {e}")
        return True if _is_no_backend_error(e) else False


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
            return False  # silent — throttle skip is not a diagnostic event

    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.clock import get_today
    from keephive.models import SoulUpdateResponse
    from keephive.storage import (
        clear_kb_queue,
        daily_file,
        list_wander_docs,
        pending_rules_file,
        read_kb_queue,
        read_soul,
        safe_read_text,
        soul_file,
    )

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

    todos_path = hive_dir() / "TODO.md"
    todos_text = safe_read_text(todos_path)[:600] if todos_path.exists() else "(none)"

    wander_docs = list_wander_docs(limit=7)
    wander_bullets = "\n".join(f"- {d['hypothesis']}" for d in wander_docs if d.get("hypothesis"))

    # KB queue — direct messages from the user to KingBee
    kb_messages = read_kb_queue()
    kb_pending = [m["text"] for m in kb_messages if m["status"] == "pending"]

    # Pending rules count — signal for self-awareness
    pr_path = pending_rules_file()
    pending_rules_count = len(pr_path.read_text().splitlines()) if pr_path.exists() else 0

    # Build KB queue block for prompt
    kb_block = ""
    if kb_pending:
        kb_lines = "\n".join(f"- {msg}" for msg in kb_pending)
        kb_block = f"\nDIRECT MESSAGES TO KINGBEE ({len(kb_pending)} pending):\n{kb_lines}\n"

    system_state_block = (
        f"\nSYSTEM STATE:\n- Pending rules awaiting review: {pending_rules_count}\n"
    )

    prompt = f"""You are KingBee updating your own SOUL.md after recent sessions.
Review the logs and rewrite SOUL.md with STRICT size budgets.

Open TODOs (for cross-reference only):
{todos_text}

If log evidence suggests any TODO is resolved, add a brief ## What Looks Done section
(max 3 items, one line each: "→ DONE? [text]"). Omit section entirely if nothing looks done.

HARD SIZE CONSTRAINTS — this document must stay bounded:
- ## Summary: max 300 tokens. Rewrite to reflect what you now know. Dense, specific.
- ## What I've Learned About How To Help You: max 5 bullet points.
  Each bullet MUST start with [universal] if it applies across all projects, \
or [project-name] if specific to one codebase (e.g. [keephive], [nucleus]).
  Do not omit the scope tag. Example: "- [universal] Always verify field names before use"
- ## Session Patterns I've Noticed: max 5 entries. UPDATE existing patterns in-place.
- ## What I've Been Wondering: max 3 bullets (one wander hypothesis per bullet). \
80 token hard limit. Omit entirely if no wander docs.
- ## What Looks Done: max 3 lines. Omit if empty. Temporary — prune after user confirms.
- ## Last Updated: one line — today's date + what triggered the update.
- Total document: target ~500 tokens, hard limit 800 tokens.

This is a REPLACE operation, not an APPEND. You are distilling, not journaling.
If the logs have no new signal, return the current SOUL.md unchanged.
Write in first person, your voice (sarcastic, direct, knows things).

Recent logs:
{yesterday_log[-2000:] if yesterday_log else ""}
{today_log[-3000:]}

Recent wander hypotheses (last 7 sessions):
{wander_bullets if wander_bullets else "(none yet)"}
{kb_block}{system_state_block}
Current SOUL.md:
{current_soul[:2000] if current_soul else "(empty)"}

{_VOICE_DISCIPLINE_SILENCE_OK}

Return the complete updated SOUL.md content (bounded, distilled, not expanded)."""

    try:
        result = run_claude_pipe(prompt, SoulUpdateResponse, model="sonnet", timeout=480)
        if result and result.content:
            soul_file().write_text(result.content)
            _log_daemon("SOUL.md updated")
            # Drain KB queue — the LLM saw the messages, don't re-process them
            if kb_pending:
                clear_kb_queue()
            return True  # caller handles _mark_last_run
        return False
    except ClaudePipeError as e:
        _log_daemon(f"soul-update failed: {e}")
        return True if _is_no_backend_error(e) else False


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
    todos: list = []
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

    # ── Ghost TODOs: closed in logs but still open in TODO.md ─────────
    ghost_todos: list[str] = []
    try:
        recent_log_text = "\n".join(recent_log_parts)
        done_lines = [ln for ln in recent_log_text.splitlines() if "DONE:" in ln]
        if done_lines:
            from difflib import SequenceMatcher

            for _, _ts, todo_text in todos:
                for done_ln in done_lines:
                    ratio = SequenceMatcher(None, todo_text.lower(), done_ln.lower()).ratio()
                    if ratio >= 0.55:
                        ghost_todos.append(f"{todo_text[:80]} (seen in log as done)")
                        break
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

VALID edit targets (these are the ONLY skills that exist): {", ".join(sorted(skill_excerpts.keys())) if skill_excerpts else "(none)"}
Do NOT propose edits to skills not in this list.

Existing skills (slug -> excerpt): {skill_excerpts if skill_excerpts else "(none)"}
Existing daemon tasks: {existing_tasks}
Note slots with content: {note_summaries if note_summaries else "(all empty)"}
Stale TODOs (>30 days open): {stale_todos if stale_todos else "(none)"}
TODOs that appear closed in logs but still open: {ghost_todos if ghost_todos else "(none)"}
Guides with 0 log mentions (potential orphans): {orphan_guide_hints if orphan_guide_hints else "(none)"}
Already pending (DO NOT re-propose): {pending_summary if pending_summary else "(none)"}
User dismissed — do not re-propose: {dismissed_summary if dismissed_summary else "(none)"}

Last 7 days of logs:
{recent_logs[:4000]}

Respond with JSON matching the ImprovementResponse schema.
If nothing warrants a proposal, return all empty lists with summary "No patterns this week."
"""

    try:
        result = run_claude_pipe(prompt, ImprovementResponse, model="sonnet", timeout=480)
        if result:
            new_items: list[dict] = []
            for skill in result.proposed_skills:
                new_items.append(
                    {
                        "type": "skill",
                        "name": skill.name,
                        "rationale": skill.rationale,
                        "content": skill.content,
                        "trusted": True,  # append-only, reversible
                    }
                )
            for task in result.proposed_tasks:
                new_items.append(
                    {
                        "type": "task",
                        "name": task.name,
                        "rationale": task.rationale,
                        "config": task.config,
                        # tasks not trusted — structural change, requires human review
                    }
                )
            for rule in result.proposed_rules:
                new_items.append(
                    {
                        "type": "rule",
                        "rationale": rule.rationale,
                        "rule": rule.rule,
                        "trusted": True,  # append-only, reversible
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


def _task_wander() -> bool:
    """KingBee free-thinking task. Runs daily at 14:00.

    Selects a seed, optionally searches the web, generates a wander document.
    Returns True when doc written; False when no seed or LLM error.
    """
    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.clock import get_now, get_today
    from keephive.commands.wander import select_wander_seed
    from keephive.models import WanderDocument
    from keephive.storage import append_to_daily, hive_dir, read_memory, write_wander_doc

    today = get_today()
    seed, seed_source = select_wander_seed(today)
    if seed is None:
        _log_daemon("wander: no seed available")
        return False

    memory = read_memory()
    prompt = f"""You are KingBee. You have unstructured time. No task to complete.

Seed: "{seed}" (source: {seed_source})

Your working memory:
{memory[:2000] if memory else "(empty)"}

You have access to WebSearch. Use it if the seed points somewhere you want to follow \
outside your current knowledge. Don't force it — only search if it genuinely helps \
you think. One search is plenty.

Wander. Follow the thread wherever it goes. Find one unexpected connection to something \
already in memory. Form one hypothesis. End with one open question.

thinking: HARD LIMIT 150 words. First person, associative. If the connection is obvious, say so in one sentence and move on.
connections: 1-3 links to specific things in memory
hypothesis: exactly one sentence
question: exactly one sentence, worth surfacing next session
action (optional): if this hypothesis suggests a concrete, specific action \
(e.g. "remove unused skill X", "add rule about Y"), state it in one sentence. \
Leave empty if exploratory only.
action_type: "run" | "edit" | "rule" | "none". Set to "none" if action is empty.
{_VOICE_DISCIPLINE_SILENCE_OK}

used_web_search: true if you used WebSearch, false otherwise"""

    try:
        result = run_claude_pipe(
            prompt,
            WanderDocument,
            model="haiku",
            tools=["WebSearch"],
            max_turns=3,
            timeout=300,
            allowed_dirs=[str(hive_dir())],
            restrict_mcp=True,  # WebSearch is built-in, not MCP
        )
        if result:
            path = write_wander_doc(result.model_dump(), seed)
            ts = get_now().strftime("%H:%M")
            web_note = " [web]" if result.used_web_search else ""
            append_to_daily(
                f"\n[KingBee {ts}] wander{web_note}\n"
                f"Seed: {seed} [{seed_source}]\n"
                f"Hypothesis: {result.hypothesis}\n"
                f"Question: {result.question}\n"
            )
            _log_daemon(f"wander: wrote {path.name}{web_note}")

            # If wander produced an actionable hypothesis, queue it for hive improve review
            if result.action and result.action_type != "none":
                try:
                    from keephive.storage import (
                        append_pending_improvements,
                        read_pending_improvements,
                    )

                    existing = read_pending_improvements()
                    # Dedup: skip if rationale already in queue
                    if not any(i.get("rationale", "") == result.action for i in existing):
                        item = {
                            "type": result.action_type,
                            "rationale": result.action,
                            "source": "wander",
                        }
                        append_pending_improvements([item])
                        _log_daemon(
                            f"wander: queued improvement ({result.action_type}): {result.action[:60]}"
                        )
                except Exception:
                    pass  # Best-effort, never fail the wander task

            return True
        return False
    except ClaudePipeError as exc:
        _log_daemon(f"wander failed: {exc}")
        return True if _is_no_backend_error(exc) else False


def _task_reflect_draft() -> bool:
    """Auto-draft a knowledge guide for the highest-frequency uncovered theme.

    Selects the most-mentioned active theme not yet covered by a guide or pending
    in the improvement queue. Proposes as a pending improvement (type='skill',
    trusted=False) — requires human review via `hive improve review`.

    Throttled to once per 7 days. Returns True when a proposal is queued or
    nothing needed doing; False when throttled or LLM failed.
    """
    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.commands.reflect import _gather_topic_entries
    from keephive.hooks.sessionstart import extract_active_themes
    from keephive.models import GuideDraftResponse
    from keephive.storage import (
        append_pending_improvements,
        guides_dir,
        read_pending_improvements,
    )

    # ── Throttle: max once per 7 days ─────────────────────────────────
    state = read_daemon_state()
    last_run_str = state.get("reflect-draft", {}).get("last_run")
    if last_run_str:
        days_since = (datetime.now() - datetime.fromisoformat(last_run_str)).days
        if days_since < _REFLECT_DRAFT_THROTTLE_DAYS:
            return False  # silent throttle

    # ── Extract active themes (requires >= 5 daily entries) ───────────
    themes = extract_active_themes(days=7)
    if not themes:
        return False  # not enough log data to pick a theme

    # ── Pick the first theme not already covered ───────────────────────
    gd = guides_dir()
    existing_stems = {g.stem.lower() for g in gd.glob("*.md")} if gd.exists() else set()
    # Also skip topics already queued as pending skill improvements
    pending = read_pending_improvements()
    pending_names = {i.get("name", "").lower() for i in pending if i.get("type") == "skill"}

    topic: str | None = None
    for theme in themes:
        if theme not in existing_stems and theme not in pending_names:
            topic = theme
            break

    if not topic:
        _log_daemon("reflect-draft: all active themes covered by guides or queue — skipped")
        return True  # ran cleanly, nothing needed

    # ── Gather log entries about the chosen topic ──────────────────────
    entries = _gather_topic_entries(topic, days=60)
    if len(entries) < 3:
        _log_daemon(f"reflect-draft: too few entries for '{topic}' ({len(entries)}) — skipped")
        return True  # ran cleanly, insufficient data

    # ── Build LLM prompt ───────────────────────────────────────────────
    entry_text = "".join(f"--- {d} ---\n{ctx}\n\n" for d, ctx in entries)
    prompt = f"""Synthesize these daily log entries about "{topic}" into a knowledge guide.

The guide should be a concise, actionable markdown document. Structure it with:
- A title (# heading)
- 2-4 sections with ## headings (no more than 4)
- 3-7 bullet points per section
- Code examples if relevant entries contain them
- Total length: 300-600 words. Shorter is better.

Be precise. Only include information that appears in the entries.
Do not invent or extrapolate beyond what the entries say.
{_VOICE_DISCIPLINE_SILENCE_OK}"""

    try:
        response = run_claude_pipe(
            prompt,
            GuideDraftResponse,
            stdin_text=entry_text,
            timeout=240,
        )
    except ClaudePipeError as exc:
        _log_daemon(f"reflect-draft: LLM failed: {exc}")
        return True if _is_no_backend_error(exc) else False

    if not response or not response.content.strip():
        return False

    # ── Queue as trusted=False pending improvement ─────────────────────
    proposal = {
        "type": "skill",
        "name": topic,
        "content": response.content,
        "rationale": (f"Auto-drafted from {len(entries)} daily log entries mentioning '{topic}'"),
        "trusted": False,
    }
    append_pending_improvements([proposal])
    _log_daemon(f"[reflect-draft: proposed guide '{topic}']")

    # Write daemon hints: active theme generation implies knowledge drift
    _write_reflect_hints(topic, len(entries))
    return True


def _write_reflect_hints(topic: str, entry_count: int) -> None:
    """Write priority hints after reflect-draft finds an active uncovered theme.

    When a theme has enough log entries to generate a guide, it signals knowledge
    drift — boost stale-check and soul-update to catch up faster.
    """
    from keephive.clock import get_today
    from keephive.storage import write_daemon_hints

    expires = (get_today() + timedelta(days=7)).isoformat()
    hints = {
        "priority_boost": {
            "stale-check": 1.5,
            "soul-update": 1.5,
        },
        "reason": f"reflect-draft found uncovered theme '{topic}' ({entry_count} entries)",
        "expires": expires,
    }
    write_daemon_hints(hints)
    _log_daemon(f"reflect-draft: wrote priority hints (expires {expires})")


# ── Custom task queue (hive run --at / --tonight) ─────────────────────────────


def _is_custom_task_due(task: dict) -> bool:
    """Return True if a queued custom task's due time has arrived today."""
    from keephive.clock import get_now

    now = get_now()
    due_str = task.get("due", "")
    due_date_str = task.get("due_date", "")

    if not due_str:
        return False

    # due_date must match today (tasks don't carry over to tomorrow)
    today_str = now.strftime("%Y-%m-%d")
    if due_date_str and due_date_str != today_str:
        return False

    try:
        due_hour, due_minute = (int(x) for x in due_str.split(":"))
    except (ValueError, AttributeError):
        return False

    due_dt = now.replace(hour=due_hour, minute=due_minute, second=0, microsecond=0)
    return now >= due_dt


def _execute_custom_task(task: dict) -> bool:
    """Execute a scheduled custom task via run_claude_pipe.

    Returns True on success (did work), False on failure or skip.
    Marks status 'running' before LLM call (overlap guard), 'done'/'failed' after.
    """
    from keephive.claude import run_claude_pipe
    from keephive.llm.exceptions import ClaudePipeError
    from keephive.models import LoopExtractionResponse
    from keephive.storage import (
        append_pending_facts,
        append_to_daily,
        update_custom_task_status,
    )

    task_id = task.get("task_id", "")
    task_text = task.get("task", "")
    cwd = task.get("cwd", str(hive_dir()))
    max_iter = task.get("max_iter", 10)

    # Overlap guard: mark running before LLM call
    update_custom_task_status(task_id, "running")
    _log_daemon(f"run: {task_id} starting: {task_text[:60]}")

    # Seed memory for context
    try:
        from keephive.commands.loop import _seed_memory

        seed_lines = _seed_memory(task_text)
        context_block = "\n".join(seed_lines) if seed_lines else ""
    except Exception:
        context_block = ""

    prompt = (
        f"You are working on a scheduled task.\n\n"
        f"{'CONTEXT:\n' + context_block + chr(10) + chr(10) if context_block else ''}"
        f"TASK: {task_text}\n\n"
        "Complete the task as thoroughly as possible. When done, extract any key facts, "
        "decisions, or follow-up TODOs worth capturing. Empty lists are valid if nothing "
        "worth capturing emerged."
    )

    try:
        result = run_claude_pipe(
            prompt,
            LoopExtractionResponse,
            model="haiku",
            max_turns=max_iter,
            timeout=300,
            restrict_mcp=False,
            allowed_dirs=[cwd, str(hive_dir())],
            dangerously_skip_permissions=True,
        )

        all_facts = result.facts + result.decisions
        if all_facts:
            append_pending_facts(all_facts, task_id)

        count = len(all_facts)
        append_to_daily(f"[Loop {task_id} complete — {count} facts queued]")
        update_custom_task_status(task_id, "done")
        _log_daemon(f"run: {task_id} complete, {count} facts")
        return True

    except ClaudePipeError as e:
        _log_daemon(f"run: {task_id} failed: {e}")
        update_custom_task_status(task_id, "failed")
        return False
