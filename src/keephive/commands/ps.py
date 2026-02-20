"""hive ps: local hive map — active claude sessions, project activity, git state."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import date, timedelta

from keephive.output import console
from keephive.storage import daily_file, read_stats


def cmd_ps(args: list[str]) -> None:
    """Display active claude sessions, hive activity, and git state."""
    cwd = os.getcwd()
    stats = read_stats()
    recent = _recent_projects(stats, cwd)
    git = _git_info(cwd)
    session_dirs = _get_active_session_dirs()
    _render(cwd, recent, git, session_dirs)


def _same_path(a: str, b: str) -> bool:
    """Compare two paths for identity, handling case-insensitive FS and ~ expansion."""
    try:
        return os.path.samefile(os.path.expanduser(a), b)
    except (OSError, ValueError):
        # Paths don't exist on disk; fall back to normalized string comparison
        return os.path.normcase(os.path.abspath(os.path.expanduser(a))) == os.path.normcase(
            os.path.abspath(b)
        )


def _count_claude_processes() -> int:
    """Count interactive Claude sessions (excludes: -p, Electron helpers, grep)."""
    try:
        r = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
        count = 0
        for ln in r.stdout.splitlines():
            if "grep" in ln:
                continue
            if " claude" not in ln:
                continue
            if " -p " in ln or " --print " in ln:
                continue
            if "Helper" in ln or "Contents/MacOS" in ln:
                continue
            count += 1
        return count
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0


def _get_active_session_dirs() -> list[str]:
    """Get working directories of active interactive Claude Code sessions via lsof."""
    try:
        # Get interactive PIDs from ps
        ps_result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
        interactive_pids: set[str] = set()
        for ln in ps_result.stdout.splitlines():
            if "grep" in ln or " -p " in ln or "Helper" in ln or "Contents/MacOS" in ln:
                continue
            if " claude" in ln:
                parts = ln.split()
                if len(parts) >= 2:
                    interactive_pids.add(parts[1])
        if not interactive_pids:
            return []

        # Get cwds for all claude processes in one lsof call (avoids per-PID child expansion)
        lsof_result = subprocess.run(
            ["lsof", "-c", "claude", "-d", "cwd", "-F", "pn"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Parse field format: p<pid>\nf<fd>\nn<path>
        pid_to_cwd: dict[str, str] = {}
        cur_pid: str | None = None
        for line in lsof_result.stdout.splitlines():
            if line.startswith("p") and len(line) > 1:
                cur_pid = line[1:]
            elif line.startswith("n") and len(line) > 1 and cur_pid is not None:
                pid_to_cwd[cur_pid] = line[1:]
                cur_pid = None

        return [cwd for pid, cwd in pid_to_cwd.items() if pid in interactive_pids]
    except Exception:
        return []


def _git_info(cwd: str) -> dict | None:
    """Return git branch and worktree count for cwd, or None if not a repo."""
    try:
        branch_r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if branch_r.returncode != 0:
            return None
        branch = branch_r.stdout.strip()
        if not branch:
            return None

        worktree_r = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        worktree_count = (
            len(worktree_r.stdout.strip().splitlines()) if worktree_r.returncode == 0 else 1
        )

        return {"branch": branch, "worktrees": worktree_count}
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return None


def _project_name(path: str) -> str:
    """Extract short project name from a full path."""
    return path.rstrip("/").split("/")[-1] or path


def _today_cmd_count(stats: dict, project_key: str) -> int:
    """Count commands for a project today."""
    today_str = date.today().isoformat()
    day_data = stats.get("days", {}).get(today_str, {})
    return day_data.get("projects", {}).get(project_key, {}).get("commands", 0)


def _last_log_time() -> str | None:
    """Return the last [HH:MM:SS] timestamp from today's daily log, formatted as HH:MM."""
    try:
        log_path = daily_file()
        if not log_path.exists():
            return None
        content = log_path.read_text()
        matches = re.findall(r"\[(\d{2}:\d{2}):\d{2}\]", content)
        return matches[-1] if matches else None
    except (OSError, ValueError):
        return None


def _last_entry_age(stats: dict, project_key: str) -> str:
    """Return a human-readable age of the last log entry for this project."""
    days_data = stats.get("days", {})
    last_day = None
    for day_str in sorted(days_data.keys(), reverse=True):
        if project_key in days_data[day_str].get("projects", {}):
            last_day = day_str
            break
    if not last_day:
        return "never"
    try:
        d = date.fromisoformat(last_day)
    except ValueError:
        return last_day
    delta = (date.today() - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "1d ago"
    return f"{delta}d ago"


def _recent_projects(stats: dict, cwd: str, days: int = 30) -> list[dict]:
    """Return projects active in the last N days, sorted by recency."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    days_data = stats.get("days", {})

    seen: dict[str, dict] = {}
    for day_str in sorted(days_data.keys(), reverse=True):
        if day_str < cutoff:
            continue
        for proj_key in days_data[day_str].get("projects", {}):
            if proj_key not in seen:
                seen[proj_key] = {
                    "key": proj_key,
                    "name": _project_name(proj_key),
                    "last_day": day_str,
                    "today_cmds": _today_cmd_count(stats, proj_key),
                    "age": _last_entry_age(stats, proj_key),
                    "is_current": _same_path(proj_key, cwd),
                }

    current = [p for p in seen.values() if p["is_current"]]
    others = sorted(
        [p for p in seen.values() if not p["is_current"]],
        key=lambda p: p["last_day"],
        reverse=True,
    )
    return current + others


def _format_git(git: dict | None) -> str:
    """Format git branch + worktree info as a display string."""
    if not git:
        return ""
    wt = git["worktrees"]
    wt_label = f"{wt} worktree{'s' if wt != 1 else ''}"
    return f"  [{git['branch']}]  {wt_label}"


def _render_text(
    cwd: str,
    projects: list[dict],
    git: dict | None,
    session_dirs: list[str],
) -> str:
    """Return ps output as a plain string (for MCP)."""
    lines: list[str] = []
    lines.append("")
    lines.append("  local hive map")
    lines.append("  " + "\u2500" * 46)

    current = [p for p in projects if p["is_current"]]
    others = [p for p in projects if not p["is_current"]]

    active_others = [p for p in others if any(_same_path(p["key"], d) for d in session_dirs)]
    recent_others = [p for p in others if not any(_same_path(p["key"], d) for d in session_dirs)]

    active_count = _count_claude_processes()
    proc_label = f"{active_count} active session{'s' if active_count != 1 else ''}"
    lines.append(f"  Claude: {proc_label}")
    lines.append("")

    if current:
        p = current[0]
        git_str = _format_git(git)
        lines.append(f"  This project:  {p['name']}{git_str}")
        today_str = f"today: {p['today_cmds']} command{'s' if p['today_cmds'] != 1 else ''}"
        age = _last_log_time() or p["age"] if p["age"] == "today" else p["age"]
        lines.append(f"    {today_str}  |  last entry: {age}")
    else:
        git_str = _format_git(git)
        lines.append(f"  This project:  {_project_name(cwd)}{git_str}")
        lines.append("    no activity recorded today")

    if active_others:
        lines.append("")
        lines.append("  Active sessions:")
        for p in active_others:
            label = f"{p['today_cmds']} cmd today" if p["today_cmds"] else p["age"]
            lines.append(f"    \u25cf {p['name']:<22}  {label}")

    if recent_others:
        lines.append("")
        lines.append("  Recent hives (30d):")
        for p in recent_others:
            label = f"{p['today_cmds']} cmd today" if p["today_cmds"] else p["age"]
            lines.append(f"    {p['name']:<24}  {label}")

    lines.append("")
    lines.append("  " + "\u2500" * 46)
    lines.append("")
    return "\n".join(lines)


def _render(
    cwd: str,
    projects: list[dict],
    git: dict | None,
    session_dirs: list[str],
) -> None:
    """Render the ps output."""
    console.print()
    console.print("  [bold]local hive map[/bold]")
    console.print("  " + "\u2500" * 46)

    current = [p for p in projects if p["is_current"]]
    others = [p for p in projects if not p["is_current"]]

    # Identify which other projects have active sessions
    active_others = [p for p in others if any(_same_path(p["key"], d) for d in session_dirs)]
    recent_others = [p for p in others if not any(_same_path(p["key"], d) for d in session_dirs)]

    # Use process count for display (session_dirs is for directory mapping, not count)
    active_count = _count_claude_processes()
    proc_label = f"{active_count} active session{'s' if active_count != 1 else ''}"
    console.print(f"  Claude: {proc_label}")
    console.print()

    # This project
    if current:
        p = current[0]
        git_str = _format_git(git)
        console.print(f"  [bold]This project:[/bold]  {p['name']}{git_str}")
        today_str = f"today: {p['today_cmds']} command{'s' if p['today_cmds'] != 1 else ''}"
        age = _last_log_time() or p["age"] if p["age"] == "today" else p["age"]
        console.print(f"    {today_str}  |  last entry: {age}")
    else:
        git_str = _format_git(git)
        console.print(f"  [bold]This project:[/bold]  {_project_name(cwd)}{git_str}")
        console.print("    no activity recorded today")

    # Active sessions in other projects
    if active_others:
        console.print()
        console.print("  [bold]Active sessions:[/bold]")
        for p in active_others:
            label = f"{p['today_cmds']} cmd today" if p["today_cmds"] else p["age"]
            console.print(f"    \u25cf {p['name']:<22}  {label}")

    # Recent hives (not currently active)
    if recent_others:
        console.print()
        console.print("  [bold]Recent hives (30d):[/bold]")
        for p in recent_others:
            label = f"{p['today_cmds']} cmd today" if p["today_cmds"] else p["age"]
            console.print(f"    {p['name']:<24}  {label}")

    console.print()
    console.print("  " + "\u2500" * 46)
    console.print()
