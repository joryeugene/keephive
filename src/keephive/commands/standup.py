"""hive standup: PR-centric standup with Slack-ready output."""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from keephive.output import console, copy_to_clipboard, prompt_yn
from keephive.storage import collect_todos, daily_dir, get_meaningful_entries, safe_read_text, today


def _weekend_aware_cutoff() -> date:
    """Return the cutoff date for 'yesterday' activity.

    On Monday, returns Friday. Otherwise returns yesterday.
    """
    t = date.today()
    weekday = t.weekday()  # 0=Monday
    if weekday == 0:
        return t - timedelta(days=3)  # Friday
    return t - timedelta(days=1)


def _run_gh(args: list[str], timeout: int = 10) -> list[dict]:
    """Run a gh CLI command and return parsed JSON, or [] on failure."""
    try:
        r = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return []


def _gather_pr_data() -> dict:
    """Gather open, merged, and closed PRs in parallel.

    Returns dict with keys: open_prs, merged_prs, closed_prs.
    """
    cutoff = _weekend_aware_cutoff()
    cutoff_str = cutoff.isoformat()

    def fetch_open():
        return _run_gh([
            "pr", "list", "--author", "@me", "--state", "open",
            "--limit", "20",
            "--json", "number,title,isDraft,url,updatedAt,headRefName,createdAt",
        ])

    def fetch_merged():
        return _run_gh([
            "pr", "list", "--author", "@me", "--state", "merged",
            "--search", f"merged:>={cutoff_str}",
            "--limit", "20",
            "--json", "number,title,url,mergedAt",
        ])

    def fetch_closed():
        return _run_gh([
            "pr", "list", "--author", "@me", "--state", "closed",
            "--search", f"closed:>={cutoff_str}",
            "--limit", "20",
            "--json", "number,title,url,closedAt",
        ])

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_open = pool.submit(fetch_open)
        f_merged = pool.submit(fetch_merged)
        f_closed = pool.submit(fetch_closed)

    return {
        "open_prs": f_open.result(),
        "merged_prs": f_merged.result(),
        "closed_prs": f_closed.result(),
    }


def _gather_raw_data() -> dict:
    """Gather all raw data for standup generation.

    Returns dict with keys: recent_done, open_todos, insights,
    open_prs, merged_prs, closed_prs, daily_text.
    """
    t = date.today()
    cutoff = _weekend_aware_cutoff()
    todos, _ = collect_todos()

    # Build done set for filtering open todos
    done_set: set[str] = set()

    all_dones: list[tuple[str, str]] = []
    dd = daily_dir()
    scan_cutoff = (t - timedelta(days=30)).isoformat()

    if dd.exists():
        for fpath in sorted(dd.glob("*.md")):
            fname = fpath.stem
            if fname < scan_cutoff:
                continue
            for line in safe_read_text(fpath).splitlines():
                line = line.rstrip()
                m = re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*DONE:\s*(.*)", line)
                if m:
                    all_dones.append((fname, m.group(1).strip()))
                    done_set.add(m.group(1).strip().lower())
                    continue
                m = re.match(r"^- DONE:\s*(.*)", line)
                if m:
                    all_dones.append((fname, m.group(1).strip()))
                    done_set.add(m.group(1).strip().lower())

    # Recent completed: DONEs since cutoff
    recent_done = [(d, text) for d, text in all_dones if d >= cutoff.isoformat()]

    # Open TODOs
    open_list = [(d, ts, text) for d, ts, text in todos if text.lower() not in done_set]

    # Key insights from daily log
    entries = get_meaningful_entries(limit=10)
    insight_keywords = ("DECISION:", "INSIGHT:", "FACT:", "CORRECTION:")
    insights = [e.strip().lstrip("~ ").strip() for e in entries if any(k in e for k in insight_keywords)]

    # PR data (parallel gh calls)
    pr_data = _gather_pr_data()

    # Raw daily text for the last 2 days (for LLM context)
    daily_text = ""
    if dd.exists():
        yday = t - timedelta(days=1)
        for fpath in sorted(dd.glob("*.md")):
            if fpath.stem >= yday.isoformat():
                daily_text += f"--- {fpath.stem} ---\n{safe_read_text(fpath)}\n\n"

    return {
        "recent_done": recent_done,
        "open_todos": open_list,
        "insights": insights[:5],
        "open_prs": pr_data["open_prs"],
        "merged_prs": pr_data["merged_prs"],
        "closed_prs": pr_data["closed_prs"],
        "daily_text": daily_text,
    }


def _display_deterministic(data: dict) -> str:
    """Display standup using raw data (no LLM). Returns Slack-formatted clipboard text."""
    merged = data.get("merged_prs", [])
    closed = data.get("closed_prs", [])
    recent_done = data["recent_done"]
    open_prs = data.get("open_prs", [])
    open_todos = data["open_todos"]

    # Yesterday section
    yesterday_items: list[str] = []
    yesterday_slack: list[str] = []

    for pr in merged:
        line = f"Merged {pr['title']} #{pr['number']}"
        yesterday_items.append(line)
        yesterday_slack.append(f"- Merged {pr['title']} #{pr['number']} {pr['url']}")

    for _, text in recent_done:
        line = f"Completed {text}"
        yesterday_items.append(line)
        yesterday_slack.append(f"- Completed {text}")

    for pr in closed:
        line = f"Closed {pr['title']} #{pr['number']}"
        yesterday_items.append(line)
        yesterday_slack.append(f"- Closed {pr['title']} #{pr['number']} {pr['url']}")

    # Today section
    today_items: list[str] = []
    today_slack: list[str] = []

    for pr in open_prs:
        if pr.get("isDraft"):
            line = f"Continue {pr['title']} #{pr['number']}"
        else:
            line = f"Get #{pr['number']} reviewed and merged"
        today_items.append(line)
        today_slack.append(f"- {line} {pr['url']}")

    for _, _, text in open_todos:
        today_items.append(text)
        today_slack.append(f"- {text}")

    # Console output (Rich)
    if yesterday_items:
        console.print("[bold]Yesterday:[/bold]")
        for item in yesterday_items:
            console.print(f"  - {item}")
        console.print()

    if today_items:
        console.print("[bold]Today:[/bold]")
        for item in today_items:
            console.print(f"  - {item}")
        console.print()

    console.print("[bold]Blockers:[/bold]")
    console.print("  - None")
    console.print()

    # Build Slack clipboard text
    return format_standup_slack_text(yesterday_slack, today_slack, [])


def format_standup_slack_text(
    yesterday: list[str], today: list[str], blockers: list[str]
) -> str:
    """Build Slack-formatted standup text from line lists."""
    parts: list[str] = []

    parts.append("*Yesterday:*")
    if yesterday:
        parts.extend(yesterday)
    else:
        parts.append("- (no activity)")

    parts.append("")
    parts.append("*Today:*")
    if today:
        parts.extend(today)
    else:
        parts.append("- (no planned work)")

    parts.append("")
    parts.append("*Blockers:*")
    if blockers:
        parts.extend(f"- {b}" for b in blockers)
    else:
        parts.append("- None")

    return "\n".join(parts) + "\n"


def _display_llm(data: dict) -> str:
    """Generate standup using LLM synthesis. Returns Slack-formatted clipboard text."""
    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.models import StandupResponse

    # Build context for the LLM
    done_text = "\n".join(f"- [{d}] {text}" for d, text in data["recent_done"]) or "(no completed items in logs)"
    todo_text = "\n".join(f"- [{d} {ts}] {text}" for d, ts, text in data["open_todos"]) or "(no open TODOs found)"

    merged_text = "\n".join(
        f"- #{pr['number']} {pr['title']} {pr['url']}"
        for pr in data.get("merged_prs", [])
    ) or "(no merged PRs)"

    closed_text = "\n".join(
        f"- #{pr['number']} {pr['title']} {pr['url']}"
        for pr in data.get("closed_prs", [])
    ) or "(no closed PRs)"

    open_pr_text = "\n".join(
        f"- #{pr['number']} {pr['title']}" + (" (draft)" if pr.get("isDraft") else "") + f" {pr['url']}"
        for pr in data.get("open_prs", [])
    ) or "(no open PRs)"

    insight_text = "\n".join(f"- {e}" for e in data["insights"]) or "(no insights recorded)"

    # If all structured data is empty AND daily log is empty, skip LLM entirely
    all_empty = (
        not data["daily_text"].strip()
        and done_text.startswith("(no ")
        and todo_text.startswith("(no ")
        and merged_text.startswith("(no ")
        and closed_text.startswith("(no ")
        and open_pr_text.startswith("(no ")
        and insight_text.startswith("(no ")
    )
    if all_empty:
        return _display_deterministic(data)

    cutoff = _weekend_aware_cutoff()
    prompt = f"""Generate a Slack-ready developer standup from this activity data.

Output format: three sections (yesterday, today, blockers). Each item is a single line.

Rules:
- yesterday: work completed since {cutoff.isoformat()}. Use action verbs (Merged, Completed, Shipped, Closed, Fixed). Every PR reference MUST include #{'{number}'} and the URL from the data.
- today: planned work from open PRs, drafts, and TODOs. For non-draft open PRs: "Get #{'{number}'} reviewed and merged". For drafts: "Continue {{title}} #{'{number}'}". Include open TODOs with action verbs.
- blockers: ONLY real blockers. If nothing qualifies, return empty list.

CRITICAL: Every item you output must trace to a specific entry in the data. Do NOT synthesize, infer, or invent work items. If a section has no data, return an empty list.

=== MERGED PRs (since {cutoff.isoformat()}) ===
{merged_text}

=== CLOSED PRs (since {cutoff.isoformat()}) ===
{closed_text}

=== COMPLETED (DONEs from logs) ===
{done_text}

=== OPEN PRs ===
{open_pr_text}

=== OPEN TODOs ===
{todo_text}

=== KEY INSIGHTS ===
{insight_text}

=== DAILY LOG (last 2 days) ===
{data['daily_text'][:3000]}"""

    try:
        with console.status("  Generating standup...", spinner="dots"):
            response = run_claude_pipe(prompt, StandupResponse, model="haiku")
    except ClaudePipeError as e:
        console.print(f"[err]LLM failed: {e}[/err]")
        console.print("[dim]Falling back to raw data. Check: claude -p availability, CLAUDECODE env var[/dim]")
        console.print()
        return _display_deterministic(data)

    display_standup(response)
    return format_standup_slack(response)


def display_standup(response) -> None:
    """Display formatted standup to console."""
    if response.yesterday:
        console.print("[bold]Yesterday:[/bold]")
        for item in response.yesterday:
            console.print(f"  - {item}")
        console.print()

    if response.today:
        console.print("[bold]Today:[/bold]")
        for item in response.today:
            console.print(f"  - {item}")
        console.print()

    console.print("[bold]Blockers:[/bold]")
    if response.blockers:
        for item in response.blockers:
            console.print(f"  - [warn]{item}[/warn]")
    else:
        console.print("  - None")
    console.print()


def format_standup_slack(response) -> str:
    """Format LLM standup response for Slack clipboard."""
    yesterday = [f"- {item}" for item in response.yesterday]
    today_items = [f"- {item}" for item in response.today]
    return format_standup_slack_text(yesterday, today_items, response.blockers)


def cmd_standup(args: list[str]) -> None:
    console.print(f"[bold]Standup for {today()}[/bold]")
    console.print()

    data = _gather_raw_data()

    # Check if there's any activity at all
    has_pr_data = data["open_prs"] or data["merged_prs"] or data["closed_prs"]
    has_hive_data = data["recent_done"] or data["open_todos"] or data["insights"]
    if not has_pr_data and not has_hive_data:
        console.print("[dim]No recent activity. Start with:[/dim]")
        console.print('  hive t "what you\'re working on"')
        console.print('  hive r "FACT: something you learned"')
        return

    # LLM mode vs deterministic
    if os.environ.get("HIVE_SKIP_LLM"):
        standup_text = _display_deterministic(data)
    elif not prompt_yn("  Format with LLM?"):
        standup_text = _display_deterministic(data)
    else:
        standup_text = _display_llm(data)

    import sys
    if sys.stdout.isatty():
        if copy_to_clipboard(standup_text):
            console.print("[dim]Copied to clipboard[/dim]")
    else:
        print(standup_text)
