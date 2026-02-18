"""keephive MCP server. Exposes hive tools to Claude Code natively."""

from __future__ import annotations

import re
from datetime import date, datetime

from mcp.server.fastmcp import FastMCP

from keephive.storage import (
    append_to_daily,
    count_stale_facts,
    daily_file,
    ensure_daily,
    ensure_dirs,
    get_meaningful_entries,
    guides_dir,
    memory_file,
    open_todos,
    safe_read_text,
    track_event,
)

mcp = FastMCP("keephive")


def _track_mcp(tool_name: str) -> None:
    """Track MCP tool usage. Silent on error."""
    try:
        track_event("commands", tool_name, source="mcp")
    except Exception:
        pass


@mcp.tool()
def hive_remember(text: str) -> str:
    """Save an insight to today's daily log.
    Prefix with: FACT, DECISION, CORRECTION, TODO, or INSIGHT."""
    _track_mcp("remember")
    ensure_dirs()
    ensure_daily()
    ts = datetime.now().strftime("%H:%M:%S")
    append_to_daily(f"- [{ts}] {text}")

    category = ""
    for cat in ("FACT", "DECISION", "CORRECTION", "TODO", "INSIGHT"):
        if text.startswith(f"{cat}:") or text.startswith(f"{cat} "):
            category = cat
            break

    df = daily_file()
    count = sum(1 for line in df.read_text().splitlines() if line.startswith("- ["))
    tag = f" [{category}]" if category else ""
    return f"Remembered{tag} at {ts} ({count} entries today)"


@mcp.tool()
def hive_recall(query: str) -> str:
    """Search all memory tiers: working memory, knowledge, daily logs (30d), archive."""
    _track_mcp("recall")
    from keephive.commands.remember import _search_all_tiers

    results = _search_all_tiers(query)
    if not results:
        return f"No results for: {query}"

    lines = [f"Found {len(results)} result(s) for: {query}\n"]
    for r in results[:15]:
        line = r["line"]
        # Strip grep line-number prefix for daily/archive tiers only
        if r["tier"] in ("daily", "archive") and ":" in line:
            line = line.split(":", 1)[-1].strip()
        if r["tier"] == "working":
            line = re.sub(r"^-\s*", "", line)
            line = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line)
        date_str = f" {r.get('date', '')}" if r.get("date") else ""
        lines.append(f"[{r['score']:>3}] ({r['tier']}){date_str} {line}")
    return "\n".join(lines)


@mcp.tool()
def hive_status() -> str:
    """Status overview: facts, stale warnings, TODOs, recent entries."""
    _track_mcp("status")
    ensure_dirs()
    from keephive import __version__
    from keephive.storage import count_daily_entries

    mem = memory_file()
    total_verified = stale = 0
    if mem.exists():
        text = mem.read_text()
        total_verified = len(re.findall(r"\[verified:", text))
        stale = count_stale_facts()

    guide_count = sum(1 for _ in guides_dir().glob("*.md")) if guides_dir().exists() else 0
    today_entries = count_daily_entries()

    from keephive.health import health_summary
    hooks_ok, mcp_ok, data_ok = health_summary()
    health_icons = " ".join(
        f"{'●' if ok else '○'} {label}"
        for ok, label in [(hooks_ok, "hooks"), (mcp_ok, "mcp"), (data_ok, "data")]
    )

    parts = [f"keephive v{__version__}", f"Health: {health_icons}"]
    if total_verified:
        ok = total_verified - stale
        parts.append(
            f"{total_verified} facts ({ok} ok, {stale} STALE)"
            if stale
            else f"{total_verified} facts ({ok} ok)"
        )
    parts.append(f"{today_entries} entries today | {guide_count} guides")

    if stale:
        parts.append(f"\nWarning: {stale} stale fact(s). Run: hive v")

    todos = open_todos()
    if todos:
        parts.append(f"\n{len(todos)} open TODO(s):")
        t = date.today()
        for d, ts, text in reversed(todos[-5:]):
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
            time_part = f" {ts}" if ts else ""
            parts.append(f"  [{age_s}{time_part}] {text}")

    entries = get_meaningful_entries(limit=5)
    if entries:
        parts.append("\nRecent:")
        parts.extend(reversed(entries))
    return "\n".join(parts)


@mcp.tool()
def hive_todo() -> str:
    """List all open TODOs with ages."""
    _track_mcp("todo")
    todos = open_todos()
    if not todos:
        return "No open TODOs"

    lines = [f"{len(todos)} open TODO(s):"]
    t = date.today()
    for d, ts, text in reversed(todos):
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
        time_part = f" {ts}" if ts else ""
        lines.append(f"  [{age_s}{time_part}] {text}")
    return "\n".join(lines)


@mcp.tool()
def hive_todo_done(pattern: str) -> str:
    """Mark the first open TODO matching pattern as done."""
    _track_mcp("todo_done")
    todos = open_todos()
    match = None
    for _d, _ts, text in todos:
        if pattern.lower() in text.lower():
            match = text
            break

    if not match:
        # Fall through to recurring tasks (mirrors CLI behavior)
        from keephive.storage import mark_recurring_done
        result = mark_recurring_done(pattern)
        if result:
            task_text, _ = result
            return f"Done: {task_text} (next due per schedule)"
        return f'No open TODO or recurring task matching "{pattern}"'

    ensure_daily()
    ts = datetime.now().strftime("%H:%M:%S")
    append_to_daily(f"- [{ts}] DONE: {match}")
    return f"Completed: {match}"


@mcp.tool()
def hive_knowledge(name: str = "") -> str:
    """List knowledge guides, or view one by name (prefix matching)."""
    _track_mcp("knowledge")
    gd = guides_dir()
    if not gd.exists():
        return "No knowledge guides yet."

    if not name:
        guides = sorted(gd.glob("*.md"))
        if not guides:
            return "No knowledge guides yet."
        lines = ["Knowledge guides:"]
        for g in guides:
            text = g.read_text()
            first_line = next(
                (ln.strip()[:80] for ln in text.splitlines() if ln.strip() and not ln.startswith("#")),
                "",
            )
            lines.append(f"  {g.stem}: {first_line}")
        return "\n".join(lines)

    guides = list(gd.glob("*.md"))

    # Exact match
    match = None
    for g in guides:
        if g.stem == name:
            match = g
            break

    # Prefix match
    if not match:
        for g in guides:
            if g.stem.startswith(name):
                match = g
                break

    # Substring match
    if not match:
        for g in guides:
            if name.lower() in g.stem.lower():
                match = g
                break

    if not match:
        return f'No guide matching "{name}". Available: {", ".join(g.stem for g in guides)}'

    return safe_read_text(match)


@mcp.tool()
def hive_log(day: str = "") -> str:
    """View daily log. Defaults to today. Pass YYYY-MM-DD, 'yesterday', or N (days ago)."""
    _track_mcp("log")
    from keephive.commands.log import _nearby_logs
    from keephive.storage import parse_date_arg

    target = parse_date_arg(day)
    df = daily_file(target)
    if not df.exists():
        parts = [f"No log for {target}"]
        nearby = _nearby_logs()
        if nearby:
            parts.append("\nNearby:")
            for day_str, count in nearby:
                entry_word = "entry" if count == 1 else "entries"
                parts.append(f"  {day_str}  ({count} {entry_word})")
        return "\n".join(parts)
    return safe_read_text(df)


@mcp.tool()
def hive_audit() -> str:
    """Quality audit: three perspectives (Vault, Cleaner, Strategist), LLM synthesis, and score.

    Uses 3 parallel LLM calls for perspectives + 1 synthesis call.
    Falls back to metrics-only when HIVE_SKIP_LLM is set."""
    _track_mcp("audit")
    import os
    from keephive.commands.audit import (
        _analyze_cleaner,
        _analyze_strategist,
        _analyze_vault,
        _check_previous_play,
        _compute_score,
    )

    ensure_dirs()
    vault = _analyze_vault()
    cleaner = _analyze_cleaner()
    strategist = _analyze_strategist()
    score = _compute_score(vault, cleaner, strategist)
    previous_play = _check_previous_play()

    parts = [f"Quality Pulse: {score}/100"]

    if previous_play:
        status = "completed" if previous_play["completed"] else f"open ({previous_play['age_days']}d)"
        parts.append(f"Previous Play: {previous_play['action']} [{status}]")

    # Vault summary
    v_ok = vault["total_facts"] - vault["stale_facts"]
    if vault["stale_facts"] > 0:
        parts.append(f"Vault: {vault['total_facts']} facts ({v_ok} ok, {vault['stale_facts']} stale)")
    elif vault["total_facts"] > 0:
        parts.append(f"Vault: {vault['total_facts']} facts ({v_ok} ok)")

    # Cleaner summary
    rate_pct = int(cleaner["todo_completion_rate"] * 100)
    vel = cleaner["todo_velocity_7d"]
    parts.append(f"Cleaner: {rate_pct}% completion, {vel['created']}c/{vel['completed']}d 7d velocity")

    # LLM synthesis (skip if HIVE_SKIP_LLM is set)
    if os.environ.get("HIVE_SKIP_LLM"):
        parts.append("\n(LLM synthesis skipped)")
        return "\n".join(parts)

    from keephive.commands.audit import (
        _run_cook,
        _run_perspectives,
        save_audit_insights,
    )
    try:
        vault_r, cleaner_r, strategist_r = _run_perspectives(vault, cleaner, strategist)
    except Exception as e:
        parts.append(f"\nPerspective analysis failed: {e}")
        return "\n".join(parts)

    try:
        synthesis = _run_cook(
            vault_r, cleaner_r, strategist_r,
            vault, cleaner, strategist,
            score, previous_play,
        )
    except Exception as e:
        parts.append(f"\nVault: {vault_r.analysis}")
        parts.append(f"Cleaner: {cleaner_r.analysis}")
        parts.append(f"Strategist: {strategist_r.analysis}")
        parts.append(f"\nSynthesis failed: {e}")
        return "\n".join(parts)

    parts.append(f"\nConnection: {synthesis.connection}")
    parts.append(f"Tension: {synthesis.tension}")
    if synthesis.plays:
        parts.append("\nDo next:")
        for i, play in enumerate(synthesis.plays, 1):
            parts.append(f"  {i}. {play.issue} -> {play.command}")
    parts.append(f"\nWild Card: {synthesis.wild_card}")

    save_audit_insights(synthesis, score)

    return "\n".join(parts)


@mcp.tool()
def hive_recurring() -> str:
    """List due recurring tasks. Shows tasks past their schedule."""
    _track_mcp("recurring")
    from keephive.storage import due_recurring
    due = due_recurring()
    if not due:
        return "No recurring tasks due."
    lines = [f"{len(due)} recurring task(s) due:"]
    for freq, text, overdue in due:
        over_s = f"+{overdue}d overdue" if overdue > 0 else "due today"
        lines.append(f"  [{freq}] {text} ({over_s})")
    return "\n".join(lines)


@mcp.tool()
def hive_stats(project: str = "", date: str = "") -> str:
    """Usage stats. Optionally filter by project (prefix match) or date."""
    _track_mcp("stats")
    from keephive.commands.stats import stats_text

    return stats_text(project=project, date_arg=date)


@mcp.tool()
def hive_knowledge_write(name: str, content: str) -> str:
    """Create or update a knowledge guide. Provide full markdown content.

    name: guide name (alphanumeric + hyphens, e.g. 'mcp-patterns')
    content: full markdown content for the guide"""
    _track_mcp("knowledge_write")
    import re as _re

    ensure_dirs()
    safe_name = _re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    if not safe_name:
        return "Invalid guide name."

    gd = guides_dir()
    gd.mkdir(parents=True, exist_ok=True)
    path = gd / f"{safe_name}.md"
    existed = path.exists()
    path.write_text(content)

    action = "Updated" if existed else "Created"
    return f"{action} guide: {safe_name} ({len(content)} chars)"


@mcp.tool()
def hive_prompt_write(name: str, content: str) -> str:
    """Create or update a prompt template. Provide full markdown content.

    name: prompt name (alphanumeric + hyphens)
    content: full markdown content for the prompt"""
    _track_mcp("prompt_write")
    import re as _re
    from keephive.storage import prompts_dir

    ensure_dirs()
    safe_name = _re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    if not safe_name:
        return "Invalid prompt name."

    pd = prompts_dir()
    pd.mkdir(parents=True, exist_ok=True)
    path = pd / f"{safe_name}.md"
    existed = path.exists()
    path.write_text(content)

    action = "Updated" if existed else "Created"
    return f"{action} prompt: {safe_name} ({len(content)} chars)"


@mcp.tool()
def hive_mem(action: str = "list", text: str = "") -> str:
    """Manage working memory facts.

    action='list': show all facts (default)
    action='add' + text: add a new verified fact
    action='rm' + text: remove first fact matching text"""
    _track_mcp("mem")
    from keephive.storage import backup_and_write, today as today_str

    ensure_dirs()
    mem = memory_file()

    if action == "list" or (action == "" and not text):
        if not mem.exists() or not mem.read_text().strip():
            return "No working memory yet."
        content = mem.read_text()
        facts = [line for line in content.splitlines() if line.startswith("- ")]
        return f"Working Memory ({len(facts)} facts):\n\n{content}"

    if action == "add" and text:
        if not mem.exists():
            mem.write_text("# Working Memory\n\n")
        content = mem.read_text()
        if not content.endswith("\n"):
            content += "\n"
        day = today_str()
        content += f"- {text} [verified:{day}]\n"
        backup_and_write(mem, content)
        return f"Added to memory: {text} [verified:{day}]"

    if action == "rm" and text:
        if not mem.exists():
            return "No working memory to remove from."
        lines = mem.read_text().splitlines(keepends=True)
        new_lines = []
        removed = ""
        for line in lines:
            if not removed and text.lower() in line.lower():
                removed = line.rstrip()
            else:
                new_lines.append(line)
        if not removed:
            return f'No line matching "{text}" in memory.'
        backup_and_write(mem, "".join(new_lines))
        return f"Removed: {removed}"

    return f"Unknown action: {action}. Use 'list', 'add', or 'rm'."


@mcp.tool()
def hive_rule(action: str = "list", text: str = "") -> str:
    """Manage behavioral rules.

    action='list': show all rules (default)
    action='add' + text: add a new rule
    action='rm' + text: remove first rule matching text"""
    _track_mcp("rule")
    from keephive.storage import backup_and_write, rules_file

    ensure_dirs()
    rf = rules_file()

    if action == "list" or (action == "" and not text):
        if not rf.exists() or not rf.read_text().strip():
            return "No working rules yet."
        content = rf.read_text()
        rules = [line for line in content.splitlines() if line.startswith("- ") or line.startswith("-> ")]
        return f"Working Rules ({len(rules)} rules):\n\n{content}"

    if action == "add" and text:
        if not rf.exists():
            rf.write_text("# Working Rules\n\n")
        content = rf.read_text()
        if not content.endswith("\n"):
            content += "\n"
        content += f"- {text}\n"
        backup_and_write(rf, content)
        return f"Added rule: {text}"

    if action == "rm" and text:
        if not rf.exists():
            return "No working rules to remove from."
        lines = rf.read_text().splitlines(keepends=True)
        new_lines = []
        removed = ""
        for line in lines:
            if not removed and text.lower() in line.lower():
                removed = line.rstrip()
            else:
                new_lines.append(line)
        if not removed:
            return f'No line matching "{text}" in rules.'
        backup_and_write(rf, "".join(new_lines))
        return f"Removed: {removed}"

    return f"Unknown action: {action}. Use 'list', 'add', or 'rm'."


def main() -> None:
    ensure_dirs()
    mcp.run(transport="stdio")
