"""hive checkup — production health monitor.

Reads real accumulated data from HIVE_HOME. No LLM calls.
Reports hook pipeline health, daemon state, queue depths, SOUL freshness,
data integrity, and magic number audit.

Usage:
    hive checkup                # Full health report
    hive checkup --snapshot     # Git-snapshot current hive state
    hive checkup --diff         # Show diff since last snapshot
    hive checkup --json         # Machine-readable health report
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from keephive.output import console
from keephive.storage import (
    hive_dir,
    is_force_cli,
    is_llm_paused,
    read_daemon_state,
    safe_read_text,
    soul_file,
)

# ── Thresholds (match what the codebase actually uses) ──────────────
_TIMEOUT_WARN_RATE = 0.10  # > 10% Layer 2 timeouts → warning
_SOUL_STALE_DAYS = 7  # SOUL.md > 7 days old → warning
_SOUL_WARN_DAYS = 2  # soul-update not run in > 2 days → warning
_QUEUE_WARN_DEPTH = 10  # any queue > 10 items → warning
_HOOK_LOG_DAYS = 30  # analyse last N days of hook log
_LAYER2_TIMEOUT_S = 120  # configured Layer 2 timeout
_SELF_IMPROVE_THROTTLE_D = 7  # self-improve weekly throttle
_SOUL_UPDATE_THROTTLE_H = 1  # soul-update hourly throttle
_FACT_STALE_D = 30  # fact staleness threshold
_DECISION_STALE_D = 90  # decision staleness threshold
_NUDGE_PROMPT = 5  # nudge every N prompt-submits
_NUDGE_STOP = 8  # nudge every N stop-hook calls


# ── Entry point ──────────────────────────────────────────────────────


def cmd_checkup(args: list[str]) -> None:
    """hive checkup [--snapshot|--diff|--json]"""
    if "--snapshot" in args:
        _snapshot()
        return
    if "--diff" in args:
        _diff()
        return
    if "--json" in args:
        _report_json()
        return
    _report()


# ── Full health report ───────────────────────────────────────────────


def _report() -> None:
    hd = hive_dir()
    console.print("\n  🐝 [bold]hive checkup[/bold] — production health\n")

    warnings: list[str] = []

    # Stage 0
    privacy_data = _check_privacy_gate()
    _print_stage0(privacy_data, warnings)

    # Stage 1
    hook_data = _check_hook_pipeline(hd)
    _print_stage1(hook_data, warnings)

    # Stage 2
    daemon_data = _check_daemon_tasks(hd)
    _print_stage2(daemon_data, warnings)

    # Stage 3
    queue_data = _check_queue_depths(hd)
    _print_stage3(queue_data, warnings)

    # Stage 4
    soul_data = _check_soul_freshness()
    _print_stage4(soul_data, warnings)

    # Stage 5
    integrity_data = _check_data_integrity(hd)
    _print_stage5(integrity_data, warnings)

    # Stage 6
    _print_stage6(daemon_data)

    # Summary
    if warnings:
        console.print(f"\n  [warn]⚠  {len(warnings)} warning(s):[/warn]")
        for w in warnings:
            console.print(f"    · {w}")
    else:
        console.print("\n  [green]✓ System healthy[/green]")
    console.print()


# ── Stage 0: Privacy gate ─────────────────────────────────────────────


def _check_privacy_gate() -> dict:
    return {"paused": is_llm_paused(), "force_cli": is_force_cli()}


def _print_stage0(data: dict, warnings: list[str]) -> None:
    console.print("  [bold]Stage 0: Privacy Gate[/bold]")
    if data["paused"]:
        w = "LLM privacy mode is ON — all API calls blocked (run `hive privacy off` to resume)"
        warnings.append(w)
        console.print("    [warn]⚠  Privacy gate: ON — .llm-paused present[/warn]")
    else:
        console.print("    [green]✓ Privacy gate: OFF (LLM calls enabled)[/green]")
    if data.get("force_cli"):
        console.print(
            "    [bold cyan]🔐 CLI-only: ON[/bold cyan] — API backends blocked "
            "(run `hive privacy off` to disable)"
        )


# ── Stage 1: Hook pipeline ────────────────────────────────────────────


def _check_hook_pipeline(hd: Path) -> dict:
    """Parse .hook-debug.log for the last HOOK_LOG_DAYS days."""
    log_path = hd / ".hook-debug.log"
    if not log_path.exists():
        return {"log_exists": False, "calls": 0, "successes": 0, "timeouts": 0, "skipped": 0}

    cutoff = datetime.now() - timedelta(days=_HOOK_LOG_DAYS)
    calls = successes = timeouts = skipped = 0

    for line in safe_read_text(log_path).splitlines():
        m = re.match(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]", line)
        if not m:
            continue
        try:
            ts = datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        if ts < cutoff:
            continue

        if "hook-precompact called" in line:
            calls += 1
        elif "Layer 2: wrote" in line:
            successes += 1
        elif "Layer 2 failed" in line:
            timeouts += 1

    # Skipped = calls that had neither success nor timeout (empty excerpts / LLM skip)
    if calls > 0:
        skipped = max(0, calls - successes - timeouts)

    return {
        "log_exists": True,
        "calls": calls,
        "successes": successes,
        "timeouts": timeouts,
        "skipped": skipped,
    }


def _print_stage1(data: dict, warnings: list[str]) -> None:
    console.print("  [bold]Stage 1: Hook Pipeline[/bold]")
    if not data["log_exists"]:
        console.print("    [dim].hook-debug.log not found (no precompact calls yet)[/dim]")
        return

    calls = data["calls"]
    if calls == 0:
        console.print(f"    [dim]No calls in the last {_HOOK_LOG_DAYS} days[/dim]")
        warnings.append(
            f"No precompact calls in {_HOOK_LOG_DAYS} days — hooks may not be registered"
        )
        return

    success_rate = data["successes"] / calls if calls else 0
    timeout_rate = data["timeouts"] / calls if calls else 0

    console.print(
        f"    Last {_HOOK_LOG_DAYS}d: {calls} calls  "
        f"{data['successes']} Layer 2 success ({success_rate:.0%})  "
        f"{data['timeouts']} timeout ({timeout_rate:.0%})  "
        f"{data['skipped']} skip"
    )

    if timeout_rate > _TIMEOUT_WARN_RATE:
        w = f"Layer 2 timeout rate {timeout_rate:.0%} — check claude -p response times"
        warnings.append(w)
        console.print(f"    [warn]⚠  {w}[/warn]")
    else:
        console.print("    [green]✓ Hook pipeline healthy[/green]")


# ── Stage 2: Daemon tasks ─────────────────────────────────────────────


def _check_daemon_tasks(hd: Path) -> dict:
    """Read daemon state and scan log for failures."""
    state = read_daemon_state()
    now = datetime.now()

    tasks: dict[str, dict] = {}
    for task in ("soul-update", "self-improve", "morning-briefing", "stale-check", "standup-draft"):
        last_run_str = state.get(task, {}).get("last_run")
        if last_run_str:
            try:
                last_run_dt = datetime.fromisoformat(last_run_str)
                days_ago = (now - last_run_dt).days
                hours_ago = (now - last_run_dt).total_seconds() / 3600
                tasks[task] = {
                    "last_run": last_run_str,
                    "days_ago": days_ago,
                    "hours_ago": hours_ago,
                }
            except ValueError:
                tasks[task] = {"last_run": last_run_str, "days_ago": -1, "hours_ago": -1}
        else:
            tasks[task] = {"last_run": None, "days_ago": -1, "hours_ago": -1}

    # Scan daemon.log for failures
    log_path = hd / "daemon.log"
    recent_failures: list[str] = []
    if log_path.exists():
        cutoff = datetime.now() - timedelta(days=7)
        for line in safe_read_text(log_path).splitlines()[-200:]:
            if "task failed:" in line:
                m = re.match(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]", line)
                if m:
                    try:
                        ts = datetime.fromisoformat(m.group(1))
                        if ts > cutoff:
                            recent_failures.append(line.strip())
                    except ValueError:
                        pass

    return {"tasks": tasks, "recent_failures": recent_failures}


def _fmt_age(days_ago: int, hours_ago: float) -> str:
    if days_ago < 0:
        return "never run"
    if days_ago == 0:
        if hours_ago < 1:
            return "< 1h ago"
        return f"{int(hours_ago)}h ago"
    if days_ago == 1:
        return "1d ago"
    return f"{days_ago}d ago"


def _print_stage2(data: dict, warnings: list[str]) -> None:
    console.print("\n  [bold]Stage 2: Daemon Tasks[/bold]")
    tasks = data["tasks"]

    for task, info in tasks.items():
        age = _fmt_age(info["days_ago"], info["hours_ago"])
        console.print(f"    {task:<22s} {age}")

    # Check soul-update freshness
    su = tasks.get("soul-update", {})
    if su.get("days_ago", -1) == -1:
        console.print(
            "    [dim]soul-update has never run — run 'hive daemon run soul-update'[/dim]"
        )
    elif su.get("days_ago", 0) > _SOUL_WARN_DAYS:
        w = f"soul-update last ran {su['days_ago']}d ago (expected daily)"
        warnings.append(w)
        console.print(f"    [warn]⚠  {w}[/warn]")
    else:
        console.print("    [green]✓ soul-update running[/green]")

    if data["recent_failures"]:
        n = len(data["recent_failures"])
        w = f"{n} task failure(s) in last 7 days — check 'hive daemon log'"
        warnings.append(w)
        console.print(f"    [warn]⚠  {w}[/warn]")


# ── Stage 3: Queue depths ─────────────────────────────────────────────


def _check_queue_depths(hd: Path) -> dict:
    """Count pending items in each review queue."""
    from keephive.clock import get_today
    from keephive.storage import kb_queue_depth, list_wander_docs

    facts = _count_md_bullets(hd / ".pending-facts.md")
    rules = _count_md_bullets(hd / ".pending-rules.md")

    improvements = 0
    imp_path = hd / ".pending-improvements.json"
    if imp_path.exists():
        try:
            improvements = len(json.loads(imp_path.read_text()))
        except (json.JSONDecodeError, ValueError):
            pass

    kb_depth = kb_queue_depth()

    # Wander activity: days since last wander doc
    wander_days_ago = -1  # -1 = no docs found
    try:
        wander_docs = list_wander_docs(limit=1)
        if wander_docs:
            last_date = wander_docs[0].get("date", "")
            if last_date:
                from datetime import date

                wander_days_ago = (get_today() - date.fromisoformat(last_date)).days
    except Exception:
        pass

    return {
        "facts": facts,
        "rules": rules,
        "improvements": improvements,
        "kb_queue": kb_depth,
        "wander_days_ago": wander_days_ago,
    }


def _count_md_bullets(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in safe_read_text(path).splitlines() if ln.strip().startswith("- "))


def _print_stage3(data: dict, warnings: list[str]) -> None:
    console.print("\n  [bold]Stage 3: Queue Depths[/bold]")
    console.print(f"    Pending facts:        {data['facts']}")
    console.print(f"    Pending rules:        {data['rules']}")
    console.print(f"    Pending improvements: {data['improvements']}")

    # KB queue
    kb_depth = data.get("kb_queue", 0)
    if kb_depth > 0:
        console.print(
            f"    KB queue:             {kb_depth} unread message(s) — soul_update will process next compaction"
        )
    else:
        console.print("    KB queue:             0 (clear)")

    # Wander activity
    wander_days_ago = data.get("wander_days_ago", -1)
    if wander_days_ago == -1:
        w = "Wander: no docs found — hive daemon status"
        console.print(f"    [warn]⚠  {w}[/warn]")
        warnings.append(w)
    elif wander_days_ago > 3:
        w = f"Wander: last run {wander_days_ago}d ago — daemon may be paused"
        console.print(f"    [warn]⚠  {w}[/warn]")
        warnings.append(w)
    else:
        console.print(f"    Wander:               last run {wander_days_ago}d ago [green]✓[/green]")

    overflowed = [
        k
        for k, v in data.items()
        if isinstance(v, int) and k not in ("kb_queue", "wander_days_ago") and v > _QUEUE_WARN_DEPTH
    ]
    if overflowed:
        for k in overflowed:
            w = f"Queue '{k}' has {data[k]} items (>{_QUEUE_WARN_DEPTH}) — run 'hive flow' to drain"
            warnings.append(w)
        console.print(f"    [warn]⚠  Queue overflow: {', '.join(overflowed)}[/warn]")
    elif kb_depth == 0 and wander_days_ago != -1 and wander_days_ago <= 3:
        console.print("    [green]✓ All queues healthy[/green]")


# ── Stage 4: SOUL.md freshness ────────────────────────────────────────


def _check_soul_freshness() -> dict:
    sf = soul_file()
    if not sf.exists():
        return {"exists": False, "days_old": -1}
    try:
        mtime = datetime.fromtimestamp(sf.stat().st_mtime)
        days_old = (datetime.now() - mtime).days
        return {"exists": True, "days_old": days_old, "mtime": mtime.strftime("%Y-%m-%d")}
    except OSError:
        return {"exists": True, "days_old": -1}


def _print_stage4(data: dict, warnings: list[str]) -> None:
    console.print("\n  [bold]Stage 4: SOUL.md[/bold]")
    if not data["exists"]:
        console.print("    [dim]SOUL.md not found — run 'hive daemon run soul-update'[/dim]")
        return

    days = data["days_old"]
    mtime = data.get("mtime", "unknown")
    if days < 0:
        console.print("    SOUL.md: exists (mtime unreadable)")
    else:
        console.print(f"    SOUL.md: last updated {mtime} ({days}d ago)")

    if days > _SOUL_STALE_DAYS:
        w = f"SOUL.md is {days}d old (>{_SOUL_STALE_DAYS}d) — run 'hive daemon run soul-update'"
        warnings.append(w)
        console.print(f"    [warn]⚠  {w}[/warn]")
    else:
        console.print("    [green]✓ SOUL.md is fresh[/green]")


# ── Stage 5: Data integrity ───────────────────────────────────────────


def _check_data_integrity(hd: Path) -> dict:
    """Attempt to parse JSON state files. Report corrupt files."""
    checks: dict[str, bool] = {}
    for fname in (".stats.json", ".daemon-state.json", ".pending-improvements.json"):
        path = hd / fname
        if not path.exists():
            continue
        try:
            json.loads(path.read_text())
            checks[fname] = True
        except (json.JSONDecodeError, ValueError):
            checks[fname] = False
    return {"checks": checks}


def _print_stage5(data: dict, warnings: list[str]) -> None:
    console.print("\n  [bold]Stage 5: Data Integrity[/bold]")
    checks = data["checks"]
    if not checks:
        console.print("    [dim]No JSON state files found yet[/dim]")
        return

    corrupt = []
    for fname, ok in checks.items():
        status = "[green]✓ valid[/green]" if ok else "[err]✗ corrupt JSON[/err]"
        console.print(f"    {fname:<28s} {status}")
        if not ok:
            corrupt.append(fname)

    if corrupt:
        w = f"Corrupt JSON files: {', '.join(corrupt)}"
        warnings.append(w)


# ── Stage 6: Magic number audit ───────────────────────────────────────


def _print_stage6(daemon_data: dict) -> None:
    """Display configured thresholds for debugging/tuning."""
    console.print("\n  [bold]Stage 6: Magic Numbers[/bold]")

    tasks = daemon_data.get("tasks", {})
    si = tasks.get("self-improve", {})
    su = tasks.get("soul-update", {})

    si_since = f"{si['days_ago']}d since last run" if si.get("days_ago", -1) >= 0 else "never run"
    su_since = (
        f"{su['hours_ago']:.0f}h since last run" if su.get("hours_ago", -1) >= 0 else "never run"
    )

    console.print(f"    Layer 2 timeout:         {_LAYER2_TIMEOUT_S}s")
    console.print(f"    Self-improve throttle:   {_SELF_IMPROVE_THROTTLE_D}d  ({si_since})")
    console.print(f"    Soul-update throttle:    {_SOUL_UPDATE_THROTTLE_H}h   ({su_since})")
    console.print(f"    Fact/Decision staleness: {_FACT_STALE_D}d / {_DECISION_STALE_D}d")
    console.print(f"    Nudge intervals:         prompt={_NUDGE_PROMPT} stop={_NUDGE_STOP}")


# ── JSON report ──────────────────────────────────────────────────────


def _report_json() -> None:
    hd = hive_dir()
    hook_data = _check_hook_pipeline(hd)
    daemon_data = _check_daemon_tasks(hd)
    queue_data = _check_queue_depths(hd)
    soul_data = _check_soul_freshness()
    integrity_data = _check_data_integrity(hd)

    warnings: list[str] = []
    # Collect warnings (reuse check functions' side-effect via temp list)
    _collect_warnings(hook_data, daemon_data, queue_data, soul_data, integrity_data, warnings)

    privacy_data = _check_privacy_gate()

    pipeline_health: dict = {}
    try:
        from keephive.storage import floor_metrics

        pipeline_health = floor_metrics()
    except Exception:
        pass

    out = {
        "privacy_paused": privacy_data["paused"],
        "force_cli": privacy_data["force_cli"],
        "hook_pipeline": hook_data,
        "daemon_tasks": {
            task: {
                "last_run": info["last_run"],
                "days_ago": info["days_ago"] if info["days_ago"] >= 0 else None,
            }
            for task, info in daemon_data["tasks"].items()
        },
        "queue_depths": queue_data,
        "soul_freshness": soul_data,
        "data_integrity": integrity_data["checks"],
        "pipeline_health": pipeline_health,
        "warnings": warnings,
    }
    print(json.dumps(out, indent=2))


def _collect_warnings(
    hook_data: dict,
    daemon_data: dict,
    queue_data: dict,
    soul_data: dict,
    integrity_data: dict,
    warnings: list[str],
) -> None:
    """Populate warnings list without printing — used by JSON mode."""
    calls = hook_data.get("calls", 0)
    if calls > 0:
        timeout_rate = hook_data["timeouts"] / calls
        if timeout_rate > _TIMEOUT_WARN_RATE:
            warnings.append(f"Layer 2 timeout rate {timeout_rate:.0%}")

    su = daemon_data["tasks"].get("soul-update", {})
    if su.get("days_ago", -1) > _SOUL_WARN_DAYS:
        warnings.append(f"soul-update last ran {su['days_ago']}d ago")

    for k, v in queue_data.items():
        if v > _QUEUE_WARN_DEPTH:
            warnings.append(f"Queue '{k}' overflow: {v} items")

    if soul_data.get("exists") and soul_data.get("days_old", 0) > _SOUL_STALE_DAYS:
        warnings.append(f"SOUL.md is {soul_data['days_old']}d old")

    for fname, ok in integrity_data.get("checks", {}).items():
        if not ok:
            warnings.append(f"Corrupt JSON: {fname}")

    if daemon_data.get("recent_failures"):
        warnings.append(f"{len(daemon_data['recent_failures'])} task failure(s) in last 7 days")


# ── Snapshot ─────────────────────────────────────────────────────────


def _snapshot() -> None:
    """Git-snapshot current hive state for diffing."""
    hd = hive_dir()
    git_dir = hd / ".git"

    # Init if needed
    if not git_dir.exists():
        result = subprocess.run(
            ["git", "init", str(hd)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[err]git init failed: {result.stderr.strip()}[/err]")
            return
        # Write .gitignore excluding personal daily notes by default
        gitignore = hd / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "# Exclude personal daily notes from snapshots by default.\n"
                "# Remove this line to include daily logs in diffs.\n"
                "daily/\n"
                "archive/\n"
            )
        console.print("  [green]✓ Initialized git repo in hive dir[/green]")
        console.print("  [dim]daily/ excluded by default (.gitignore). Remove to include.[/dim]")

    # Stage all files
    subprocess.run(["git", "-C", str(hd), "add", "-A"], capture_output=True)

    # Check if there's anything to commit
    status = subprocess.run(
        ["git", "-C", str(hd), "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if status.returncode == 0:
        console.print("  [dim]Nothing changed since last snapshot[/dim]")
        return

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    result = subprocess.run(
        ["git", "-C", str(hd), "commit", "-m", f"checkup snapshot {ts}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print("  [green]✓ Snapshot committed[/green]")
        console.print("  Run 'hive checkup --diff' after testing to see mutations.")
    else:
        console.print(f"[err]git commit failed: {result.stderr.strip()}[/err]")


# ── Diff ─────────────────────────────────────────────────────────────


def _diff() -> None:
    """Show diff since last snapshot."""
    hd = hive_dir()
    if not (hd / ".git").exists():
        console.print("[warn]No snapshot found. Run 'hive checkup --snapshot' first.[/warn]")
        return

    # Check we have at least 2 commits
    log = subprocess.run(
        ["git", "-C", str(hd), "log", "--oneline", "-2"],
        capture_output=True,
        text=True,
    )
    if log.returncode != 0 or len(log.stdout.strip().splitlines()) < 2:
        console.print("[dim]Only one snapshot exists — no diff available yet.[/dim]")
        console.print("Run 'hive checkup --snapshot' again after making changes.")
        return

    # Stat summary
    stat = subprocess.run(
        ["git", "-C", str(hd), "diff", "HEAD~1", "HEAD", "--stat"],
        capture_output=True,
        text=True,
    )
    if stat.stdout.strip():
        console.print("\n  [bold]Changed files since last snapshot:[/bold]")
        for line in stat.stdout.strip().splitlines():
            console.print(f"    {line}")

    # Full diff
    diff = subprocess.run(
        ["git", "-C", str(hd), "diff", "HEAD~1", "HEAD"],
        capture_output=True,
        text=True,
    )
    if diff.stdout.strip():
        console.print("\n  [bold]Diff:[/bold]")
        for line in diff.stdout.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"  [green]{line}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"  [err]{line}[/err]")
            else:
                console.print(f"  {line}")
    else:
        console.print("  [dim]No changes.[/dim]")
