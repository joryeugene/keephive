"""hive run — Antifragile autonomous loop.

hive run "<task>" [--max-time DURATION] [--background] [--at HH:MM] [--tonight]

Two modes:
  background   Launches a new tmux window with HIVE_LOOP_ID set.
  scheduled    Queues for daemon execution at a specified time.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from keephive.output import console

# ── Public entry points ──────────────────────────────────────────────────────


def cmd_loop(args: list[str]) -> None:
    """Dispatcher for `hive run` subcommands."""
    sub = args[0] if args else None

    if sub is None:
        # Smart default: show status if any loops active, else show help
        from keephive.storage import hive_dir

        if list(hive_dir().glob(".loop-*.json")):
            _cmd_run_status()
        else:
            _print_run_help()
        return

    if sub in ("-h", "--help", "help"):
        _print_run_help()
        return
    if sub == "status":
        return _cmd_run_status()
    if sub == "cancel":
        return _cmd_run_cancel(args[1:])
    if sub == "history":
        return _cmd_run_history()
    if sub == "review":
        return _cmd_run_review()

    # Otherwise: sub is the task string
    _cmd_run_task(sub, args[1:])


def cmd_loop_extract(args: list[str]) -> None:
    """Internal: extract learnings from a completed loop. Not shown in --help."""
    loop_id = args[0] if args else ""
    if not loop_id:
        return
    from keephive.storage import track_event

    track_event("loops", "iteration")
    task = args[1] if len(args) > 1 else ""
    _do_loop_extract(loop_id, task=task)


# ── Loop file helpers (imported by stop.py) ──────────────────────────────────


def _loop_done_path(loop_id: str) -> Path:
    """Absolute path of the done-signal file for a loop."""
    from keephive.storage import hive_dir

    return hive_dir() / f".loop-done-{loop_id}"


def _write_iter_log(loop_id: str, iter_n: int, status: str) -> None:
    """Write an iteration progress entry to today's daily log."""
    from keephive.storage import append_to_daily

    append_to_daily(f"[Loop {loop_id} iter {iter_n}: {status}]")


def _find_loop_for_session(session_id: str) -> tuple[dict, Path] | tuple[None, None]:
    """Find the loop file for the current session.

    Background mode: checks HIVE_LOOP_ID env var for direct lookup + claim.
    In-session mode: scans .loop-*.json for matching session_id.

    Returns (req_dict, loop_file_path) or (None, None) if no loop found.
    """
    from keephive.storage import hive_dir

    # Background mode: HIVE_LOOP_ID is set in the tmux window environment
    hive_loop_id = os.environ.get("HIVE_LOOP_ID")
    if hive_loop_id:
        loop_file = hive_dir() / f".loop-{hive_loop_id}.json"
        if not loop_file.exists():
            return None, None
        try:
            req = json.loads(loop_file.read_text())
            # Claim unclaimed session (session_id is null on file creation)
            if req.get("session_id") is None:
                req["session_id"] = session_id
                loop_file.write_text(json.dumps(req))
            return req, loop_file
        except (json.JSONDecodeError, OSError):
            return None, None

    return None, None


# ── Private implementation ───────────────────────────────────────────────────


def _sanitize_loop_id(task: str) -> str:
    """Generate a unique loop ID: first meaningful word + YYYYMMDD-HHMMSS."""
    from keephive.commands.wander import STOPWORDS

    words = re.findall(r"[a-z]+", task.lower())
    word = next(
        (w for w in words if len(w) > 3 and w not in STOPWORDS),
        words[0] if words else "loop",
    )
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{word}-{ts}"


def _seed_memory(topic: str) -> list[str]:
    """Return up to 5 memory lines most relevant to the topic."""
    from keephive.commands.wander import STOPWORDS
    from keephive.storage import read_memory

    memory_text = read_memory()
    if not memory_text.strip():
        return []

    topic_words = {
        w.lower() for w in re.findall(r"[a-z]+", topic.lower()) if len(w) > 3 and w not in STOPWORDS
    }
    if not topic_words:
        return []

    relevant = []
    for line in memory_text.splitlines():
        if not line.strip().startswith("- "):
            continue
        line_words = {
            w.lower()
            for w in re.findall(r"[a-z]+", line.lower())
            if len(w) > 3 and w not in STOPWORDS
        }
        if topic_words & line_words:
            relevant.append(line.strip())

    return relevant[:5]


def _parse_duration(s: str) -> int | None:
    """Parse duration string to seconds. '2h' -> 7200, '30m' -> 1800, '3600' -> 3600."""
    s = s.strip().lower()
    if s.endswith("h"):
        try:
            return int(float(s[:-1]) * 3600)
        except ValueError:
            return None
    if s.endswith("m"):
        try:
            return int(float(s[:-1]) * 60)
        except ValueError:
            return None
    try:
        return int(s)
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    """Format seconds as human-readable string: 3600 -> '1h', 5400 -> '1h30m', 1800 -> '30m'."""
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m:02d}m" if m else f"{h}h"
    return f"{seconds // 60}m"


def _extract_soul_wisdom(soul_text: str) -> list[str]:
    """Extract 'What I've Learned' bullets from SOUL.md.

    Looks for the section starting with '## What I've Learned' and returns
    up to 3 bullet points before the next ## section.
    """
    if not soul_text:
        return []
    in_section = False
    bullets: list[str] = []
    for line in soul_text.splitlines():
        if "## What I've Learned" in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            if line.startswith("- "):
                bullets.append(line[2:].strip())
    return bullets


def _build_first_iter_output(
    loop_id: str, task: str, max_seconds: int | None, seed_lines: list[str]
) -> str:
    """Build the stdout block for the first iteration.

    This is read by Claude as Bash output, kicking off iteration 1 automatically.
    Includes an ASCII opening banner with KingBee identity + SOUL.md wisdom injection.
    """
    from keephive.storage import hive_dir, safe_read_text

    task_preview = task[:54] if len(task) > 54 else task
    limit_str = _format_duration(max_seconds) if max_seconds else "no limit"
    limit_display = f"Time: {limit_str}  ·  Memory seeded: {len(seed_lines)} items"

    # Box header (width 62 content chars, 64 total with borders)
    W = 62
    lines = [
        "╔" + "═" * W + "╗",
        "║  🐝 KingBee  ·  Autonomous Loop" + " " * (W - 31) + "║",
        "╠" + "═" * W + "╣",
        f"║  Task: {task_preview:<{W - 8}}║",
        f"║  Loop: {loop_id:<{W - 8}}║",
        "║  " + limit_display + " " * max(0, W - 2 - len(limit_display)) + "║",
        "╚" + "═" * W + "╝",
    ]

    # SOUL injection — best-effort, silent on missing
    try:
        soul_path = hive_dir() / "SOUL.md"
        soul_wisdom = _extract_soul_wisdom(safe_read_text(soul_path) if soul_path.exists() else "")
        if soul_wisdom:
            lines.append("WISDOM (from KingBee's memory):")
            for wisdom in soul_wisdom[:3]:
                lines.append(f"  {wisdom}")
            lines.append("")
    except Exception:
        pass  # Never let SOUL injection crash the loop start

    if seed_lines:
        lines.append("CONTEXT:")
        for line in seed_lines:
            lines.append(f"  {line}")
        lines.append("")

    # Self-maintenance block — Claude must run these before task work
    task_words = " ".join(task.split()[:3])  # first 3 words for rc query
    lines.append("SELF-MAINTENANCE (complete before task work):")
    lines.append("  1. hive todo          → review open items; close what's done")
    lines.append("  2. hive s             → health snapshot; note warnings")
    lines.append(f'  3. hive rc "{task_words}"  → pull prior context for this task')
    lines.append("─" * 64)
    lines.append("")

    # Task block for iteration 1
    lines.append("─── ITERATION 1 " + "─" * 48)
    lines.append(f"TASK: {task}")
    lines.append("─" * 64)

    return "\n".join(lines)


def _parse_run_flags(flag_args: list[str]) -> dict:
    """Parse flag args into an options dict."""
    opts: dict = {
        "max_seconds": None,  # None = no limit
        "background": False,
        "at": None,
        "tonight": False,
    }
    i = 0
    while i < len(flag_args):
        a = flag_args[i]
        if a == "--max-time" and i + 1 < len(flag_args):
            parsed = _parse_duration(flag_args[i + 1])
            if parsed is not None:
                opts["max_seconds"] = parsed
            i += 2
            continue
        if a == "--max" and i + 1 < len(flag_args):
            # Backwards compat: treat --max N as iteration hint but ignore silently
            # (iteration-based limits are removed; just skip the value)
            i += 2
            continue
        if a == "--background":
            opts["background"] = True
        elif a == "--at" and i + 1 < len(flag_args):
            opts["at"] = flag_args[i + 1]
            i += 2
            continue
        elif a == "--tonight":
            opts["tonight"] = True
        i += 1
    return opts


def _cmd_run_task(task: str, flag_args: list[str]) -> None:
    """Main entry: seed memory, write loop file, launch."""
    opts = _parse_run_flags(flag_args)
    loop_id = _sanitize_loop_id(task)
    seed_lines = _seed_memory(task)

    if opts["tonight"]:
        opts["at"] = "22:00"

    # Scheduled mode (--at / --tonight)
    if opts["at"]:
        _schedule_task(loop_id, task, opts)
        return

    _launch_background(loop_id, task, opts, seed_lines)


def _launch_background(loop_id: str, task: str, opts: dict, seed_lines: list[str]) -> None:
    """Launch loop in a new tmux window."""
    from keephive.storage import append_to_daily, hive_dir, track_event

    # Require tmux
    tmux_ok = bool(os.environ.get("TMUX")) or (
        subprocess.run(["tmux", "info"], capture_output=True).returncode == 0
    )
    if not tmux_ok:
        console.print("[err]Background mode requires tmux.[/err]")
        console.print("Options: use --at HH:MM to schedule, or run without --background")
        return

    window_name = f"hive-loop-{loop_id[-10:]}"

    # Write loop file (session_id null — Stop hook claims it via HIVE_LOOP_ID)
    loop_file = hive_dir() / f".loop-{loop_id}.json"
    loop_data = {
        "loop_id": loop_id,
        "task": task,
        "max_seconds": opts["max_seconds"],
        "iter": 0,
        "mode": "background",
        "session_id": None,
        "tmux_window": window_name,
        "cwd": os.getcwd(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    hive_dir().mkdir(parents=True, exist_ok=True)
    loop_file.write_text(json.dumps(loop_data))
    track_event("loops", "started")

    prompt = _build_first_iter_output(loop_id, task, opts["max_seconds"], seed_lines)
    limit_note = (
        f"max {_format_duration(opts['max_seconds'])}" if opts["max_seconds"] else "no limit"
    )
    append_to_daily(f"[Loop {loop_id} start (background): {task} ({limit_note})]")

    launched = _launch_tmux_window(loop_id, window_name, prompt)
    if launched is None:
        loop_file.unlink(missing_ok=True)
        return

    console.print("[ok]🔁 Background loop started[/ok]")
    console.print(f"   Task: {task}")
    console.print(f"   Loop ID: {loop_id}")
    console.print(f"   Window: {window_name}")
    console.print(f"   Watch: tmux select-window -t {window_name}")
    console.print(f"   Stop: hive run cancel {loop_id}")


def _launch_tmux_window(loop_id: str, window_name: str, prompt: str) -> str | None:
    """Launch a new tmux window for the loop. Returns window name or None on failure."""
    from keephive.storage import hive_dir

    # Write prompt to a file — SessionStart hook reads it via HIVE_LOOP_ID and
    # injects it as additionalContext so Claude sees the task on session open.
    prompt_file = hive_dir() / f".loop-prompt-{loop_id}.txt"
    prompt_file.write_text(prompt)

    # Test seam: set HIVE_NO_TMUX_SPAWN=1 to skip the actual tmux launch.
    # Loop file and prompt file are already written; returning window_name lets
    # _launch_background print confirmation and keep state without spawning Claude.
    if os.environ.get("HIVE_NO_TMUX_SPAWN"):
        return window_name

    # Interactive mode (not -p): Stop hook fires after each turn, advancing iter.
    # HIVE_LOOP_ID in env lets _find_loop_for_session claim the session on first Stop.
    # Pass 'proceed' as the initial message positional arg — Claude interactive mode
    # accepts this and auto-submits it, so no timing-sensitive send-keys is needed.
    # SessionStart injects the loop context as additionalContext before Claude
    # processes any user messages, so 'proceed' lands with full context available.
    cmd_str = f"HIVE_LOOP_ID={loop_id} claude --dangerously-skip-permissions 'proceed'"

    result = subprocess.run(
        ["tmux", "new-window", "-n", window_name, cmd_str],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        err = result.stderr.strip()
        console.print(f"[err]tmux launch failed:[/err] {err}")
        prompt_file.unlink(missing_ok=True)
        return None

    return window_name


def _schedule_task(loop_id: str, task: str, opts: dict) -> None:
    """Queue task for daemon execution via the custom task queue."""
    from keephive.storage import append_custom_task

    at_time = opts["at"]
    task_entry = {
        "task_id": loop_id,
        "task": task,
        "status": "queued",
        "due": at_time,
        "due_date": datetime.now().strftime("%Y-%m-%d"),
        "cwd": os.getcwd(),
        "max_seconds": opts["max_seconds"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    append_custom_task(task_entry)

    note = "tonight at 22:00" if opts.get("tonight") else f"at {at_time}"
    console.print(f"[ok]⏳ Scheduled · {note} · id: {loop_id}[/ok]")
    console.print("   Check: hive run history")
    console.print("   Note: daemon must be running (hive daemon start)")


# ── Subcommands ──────────────────────────────────────────────────────────────


def _cmd_run_status() -> None:
    """Show all active loops with orphan detection."""
    from rich.panel import Panel

    from keephive.storage import hive_dir

    loop_files = sorted(hive_dir().glob(".loop-*.json"))
    if not loop_files:
        console.print("No active loops.")
        console.print('Start one: hive run "your task"')
        return

    content_lines = []
    for f in loop_files:
        try:
            req = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        loop_id = req.get("loop_id", f.stem[6:])
        task = req.get("task", "unknown")
        mode = req.get("mode", "unknown")
        iter_n = req.get("iter", 0)
        max_seconds = req.get("max_seconds")
        created = req.get("created_at", "")
        last_stop = req.get("last_stop_at")

        orphaned = ""
        if mode == "background":
            window = req.get("tmux_window", "")
            if window and not _tmux_window_exists(window):
                orphaned = "  [ORPHANED — window closed]"

        elapsed_str = ""
        if created:
            try:
                elapsed = datetime.now() - datetime.fromisoformat(created)
                total_min = int(elapsed.total_seconds() / 60)
                elapsed_str = (
                    f"{total_min}m"
                    if total_min < 60
                    else f"{total_min // 60}h{total_min % 60:02d}m"
                )
            except ValueError:
                pass

        if max_seconds and elapsed_str:
            mode_line = f"Mode: {mode}  iter {iter_n}  ({elapsed_str} / {_format_duration(max_seconds)} limit)"
        elif elapsed_str:
            mode_line = f"Mode: {mode}  iter {iter_n}  (running {elapsed_str})"
        else:
            mode_line = f"Mode: {mode}  iter {iter_n}"

        if last_stop:
            try:
                since = datetime.now() - datetime.fromisoformat(last_stop)
                mins = int(since.total_seconds() / 60)
                heartbeat = f"{mins}m ago" if mins > 0 else "just now"
            except ValueError:
                heartbeat = last_stop
        elif iter_n == 0:
            heartbeat = "turn 1 in progress"
        else:
            heartbeat = "unknown"

        content_lines.append(f"  {loop_id}{orphaned}")
        content_lines.append(f"    Task: {task}")
        content_lines.append(f"    {mode_line}")
        content_lines.append(f"    Last active: {heartbeat}")
        if elapsed_str:
            content_lines.append(f"    Started: {elapsed_str} ago")
        content_lines.append(f"    Cancel: hive run cancel {loop_id}")
        content_lines.append("")

    console.print(Panel("\n".join(content_lines).rstrip(), title="Active Loops"))


def _tmux_window_exists(window_name: str) -> bool:
    """Return True if the named tmux window currently exists."""
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-F", "#{window_name}"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return window_name in result.stdout.splitlines()
    except (subprocess.SubprocessError, OSError):
        return False


def _cmd_run_cancel(cancel_args: list[str]) -> None:
    """Cancel one or all active loops."""
    from keephive.storage import hive_dir

    cancel_all = "--all" in cancel_args
    cancel_id = next((a for a in cancel_args if not a.startswith("-")), None)

    loop_files = sorted(hive_dir().glob(".loop-*.json"))
    if not loop_files:
        console.print("No active loops.")
        return

    # Multiple loops without an explicit target: show list and exit (H3)
    if len(loop_files) > 1 and not cancel_all and not cancel_id:
        console.print(f"{len(loop_files)} active loops:")
        for f in loop_files:
            try:
                req = json.loads(f.read_text())
                lid = req.get("loop_id", "?")
                task = req.get("task", "?")
                mode = req.get("mode", "?")
                iter_n = req.get("iter", 0)
                console.print(f"  {lid:<40} {task[:40]:<42} ({mode}, iter {iter_n})")
            except (json.JSONDecodeError, OSError):
                continue
        console.print()
        console.print("Cancel all: hive run cancel --all")
        console.print("Cancel one: hive run cancel {id}")
        return

    if cancel_all:
        targets = loop_files
    elif cancel_id:
        targets = [f for f in loop_files if cancel_id in f.name]
        if not targets:
            console.print(f"[warn]No loop found with ID:[/warn] {cancel_id}")
            return
    else:
        targets = loop_files  # Single-loop fast path

    for f in targets:
        try:
            req = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            f.unlink(missing_ok=True)
            continue

        loop_id = req.get("loop_id", "")

        # Kill tmux window for background loops
        if req.get("mode") == "background":
            window = req.get("tmux_window", "")
            if window and _tmux_window_exists(window):
                subprocess.run(
                    ["tmux", "kill-window", "-t", window],
                    capture_output=True,
                )

        # Remove loop state files
        f.unlink(missing_ok=True)
        _loop_done_path(loop_id).unlink(missing_ok=True)
        (hive_dir() / f".loop-prompt-{loop_id}.txt").unlink(missing_ok=True)

        console.print(f"[ok]Cancelled:[/ok] {loop_id}")


def _cmd_run_history() -> None:
    """Show past loops from daily log (last 30 days)."""
    from datetime import timedelta

    from rich.table import Table

    from keephive.clock import get_today
    from keephive.storage import daily_file

    today = get_today()
    entries = []

    for days_ago in range(30):
        day = (today - timedelta(days=days_ago)).isoformat()
        path = daily_file(day)
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            m = re.search(r"\[Loop (\S+) (start|iter \d+|extract)[^\]]*\]", line)
            if m:
                entries.append({"loop_id": m.group(1), "event": m.group(2), "day": day})

    if not entries:
        console.print("No loop history found.")
        return

    # Group by loop_id
    loops: dict[str, dict] = {}
    for e in entries:
        lid = e["loop_id"]
        if lid not in loops:
            loops[lid] = {"loop_id": lid, "events": [], "day": e["day"]}
        loops[lid]["events"].append(e["event"])

    table = Table(show_header=True, header_style="bold")
    table.add_column("LOOP ID", style="dim", min_width=30)
    table.add_column("EVENTS")
    table.add_column("DATE")

    for lid, info in list(loops.items())[:20]:
        events_list = info["events"]
        events = ", ".join(events_list[:3])
        if len(events_list) > 3:
            events += f" +{len(events_list) - 3}"
        table.add_row(lid, events, info["day"])

    console.print(table)


def _cmd_run_review() -> None:
    """Interactive review of .pending-facts.md — accept/skip each fact."""
    from keephive.output import prompt_yn
    from keephive.storage import (
        append_to_daily,
        clear_reviewed_facts,
        memory_file,
        read_pending_facts,
    )

    facts = read_pending_facts()
    if not facts:
        console.print("No pending facts to review.")
        return

    console.print(f"{len(facts)} facts from completed loops pending review:")
    console.print("─" * 45)

    accepted_indices: list[int] = []

    for i, item in enumerate(facts):
        loop_id = item.get("loop_id", "?")
        fact = item.get("fact", "")
        console.print(f"[{i + 1}/{len(facts)}] loop:{loop_id}")
        console.print(f"  {fact}")

        try:
            yn = prompt_yn("  Add to memory?")
        except KeyboardInterrupt:
            console.print("\ncancelled")
            break

        if yn:
            mf = memory_file()
            mf.parent.mkdir(parents=True, exist_ok=True)
            with open(mf, "a") as mfh:
                mfh.write(f"- {fact}\n")
            append_to_daily(f"[Loop review: accepted] {fact[:60]}")
            accepted_indices.append(i)
            console.print("  [ok]✓[/ok]")
        else:
            console.print("  [dim]skipped[/dim]")

    if accepted_indices:
        clear_reviewed_facts(accepted_indices)
        console.print(f"\n{len(accepted_indices)} fact(s) added to memory.")


# ── Loop extract (internal, called non-blocking after loop completes) ────────


def _do_loop_extract(loop_id: str, task: str = "") -> None:
    """Extract learnings from a completed loop. Best-effort, never crashes."""
    from keephive.clock import get_today
    from keephive.storage import (
        append_pending_facts,
        append_to_daily,
        daily_file,
        safe_read_text,
    )

    today = get_today()
    # Only today's log is searched — cross-day extraction not attempted to keep
    # extraction fast and avoid re-processing already-reviewed facts.
    log_path = daily_file(today.isoformat())
    if not log_path.exists():
        return

    log_content = safe_read_text(log_path)
    loop_lines = [line for line in log_content.splitlines() if loop_id in line]

    if not loop_lines:
        return

    context = "\n".join(loop_lines)

    try:
        from keephive.claude import run_claude_pipe
        from keephive.models import LoopExtractionResponse

        prompt = (
            f"A keephive autonomous loop just completed. Loop ID: {loop_id}\n\n"
            f"Log entries from today related to this loop:\n{context}\n\n"
            "Extract key learnings: facts discovered, decisions made, follow-up TODOs. "
            "Be concise. Empty lists are valid if nothing worth capturing."
        )

        result = run_claude_pipe(prompt, LoopExtractionResponse, model="haiku", timeout=60)

        all_facts = result.facts + result.decisions + result.todos
        if all_facts:
            append_pending_facts(all_facts, loop_id)

        count = len(all_facts)
        append_to_daily(f"[Loop {loop_id} extract: {count} items queued for review]")

    except Exception:
        pass  # Extraction is best-effort; never crash a background process

    # Auto-close TODOs whose text fuzzy-matches the loop task
    if task:
        try:
            from difflib import SequenceMatcher

            from keephive.clock import get_now
            from keephive.storage import open_todos

            todos = open_todos()
            task_lower = task.lower()
            closed: list[str] = []
            for _, _, todo_text in todos:
                ratio = SequenceMatcher(None, task_lower, todo_text.lower()).ratio()
                if ratio >= 0.55:
                    ts = get_now().strftime("%H:%M:%S")
                    append_to_daily(f"- [{ts}] DONE: {todo_text}")
                    closed.append(todo_text[:50])
            if closed:
                append_to_daily(f"[Loop {loop_id} auto-closed {len(closed)} TODO(s)]")
        except Exception:
            pass  # Best-effort, never crash


# ── Help ─────────────────────────────────────────────────────────────────────


def _print_run_help() -> None:
    print(
        'Usage: hive run "<task>" [--max-time DURATION] [--background] [--at HH:MM] [--tonight]\n'
        "  Run an autonomous loop on a task. Runs until cancelled or time limit reached.\n"
        "\n"
        "  Modes:\n"
        "    (no flags)        In-session stop-hook loop\n"
        "    --background      Launch in a new tmux window\n"
        "    --at HH:MM        Schedule via daemon at specific time\n"
        "    --tonight         Schedule for tonight at 22:00\n"
        "\n"
        "  Options:\n"
        "    --max-time DURATION   Time limit: 2h, 30m, 90m, 3600 (seconds). Default: no limit.\n"
        "\n"
        "  Subcommands:\n"
        "    hive run status          Show active loops with heartbeat\n"
        "    hive run cancel [id]     Cancel loop(s)\n"
        "    hive run cancel --all    Cancel all loops\n"
        "    hive run history         Past loops from daily log\n"
        "    hive run review          Review extracted facts\n"
    )
