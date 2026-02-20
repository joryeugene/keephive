"""hive ps: local hive map — active claude sessions, project activity, git state."""

from __future__ import annotations

import os
import subprocess
from datetime import date, timedelta

from keephive.output import console
from keephive.storage import read_stats


def cmd_ps(args: list[str]) -> None:
    """Display active claude sessions, hive activity, and git state."""
    cwd = os.getcwd()
    processes = _count_claude_processes()
    stats = read_stats()
    recent = _recent_projects(stats, cwd, days=7)
    git = _git_info(cwd)
    _render(cwd, processes, recent, git)


def _count_claude_processes() -> int:
    """Count running claude processes (excludes grep itself)."""
    try:
        r = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
        return sum(
            1 for ln in r.stdout.splitlines()
            if " claude" in ln and "grep" not in ln and "keephive" not in ln
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0


def _git_info(cwd: str) -> dict | None:
    """Return git branch and worktree count for cwd, or None if not a repo."""
    try:
        branch_r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if branch_r.returncode != 0:
            return None
        branch = branch_r.stdout.strip()
        if not branch:
            return None

        worktree_r = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        worktree_count = len(worktree_r.stdout.strip().splitlines()) if worktree_r.returncode == 0 else 1

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


def _last_entry_age(stats: dict, project_key: str) -> str:
    """Return a human-readable age of the last log entry for this project."""
    days_data = stats.get("days", {})
    # Find the most recent day this project was active
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


def _recent_projects(stats: dict, cwd: str, days: int = 7) -> list[dict]:
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
                    "is_current": os.path.abspath(proj_key) == os.path.abspath(cwd)
                        if os.path.isabs(proj_key) else proj_key == cwd,
                }

    # Sort: current first, then by last_day descending
    current = [p for p in seen.values() if p["is_current"]]
    others = sorted(
        [p for p in seen.values() if not p["is_current"]],
        key=lambda p: p["last_day"],
        reverse=True,
    )
    return current + others


def _render(cwd: str, processes: int, projects: list[dict], git: dict | None) -> None:
    """Render the ps output."""
    console.print()
    console.print("  [bold]local hive map[/bold]")
    console.print("  " + "\u2500" * 46)

    proc_label = f"{processes} claude session{'s' if processes != 1 else ''} running"
    console.print(f"  Processes: {proc_label}")
    console.print()

    current = [p for p in projects if p["is_current"]]
    others = [p for p in projects if not p["is_current"]]

    if current:
        p = current[0]
        name = p["name"]
        git_str = ""
        if git:
            wt = git["worktrees"]
            wt_label = f"{wt} worktree{'s' if wt != 1 else ''}"
            git_str = f"  [{git['branch']}]  {wt_label}"
        console.print(f"  [bold]This project:[/bold]  {name}{git_str}")
        today_str = f"today: {p['today_cmds']} command{'s' if p['today_cmds'] != 1 else ''}"
        console.print(f"    {today_str}  |  last entry: {p['age']}")
    else:
        name = _project_name(cwd)
        git_str = ""
        if git:
            wt = git["worktrees"]
            wt_label = f"{wt} worktree{'s' if wt != 1 else ''}"
            git_str = f"  [{git['branch']}]  {wt_label}"
        console.print(f"  [bold]This project:[/bold]  {name}{git_str}")
        console.print("    no activity recorded today")

    if others:
        console.print()
        console.print(f"  [bold]Other hives (last 7 days):[/bold]")
        for p in others:
            today_str = f"{p['today_cmds']} cmd today"
            console.print(
                f"    {p['name']:<24}  {p['age']:<10}  |  {today_str}"
            )

    console.print("  " + "\u2500" * 46)
    console.print()
