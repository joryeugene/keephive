"""hive audit: Three Perspectives + Cook synthesis + Quality Pulse score.

Architecture: 3 parallel LLM perspective calls + 1 sequential Cook synthesis.
Perspective calls use haiku (fast, focused). Cook uses sonnet (cross-domain reasoning).
Falls back to metrics-only when HIVE_SKIP_LLM is set.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from difflib import SequenceMatcher

from keephive.clock import get_now, get_today
from keephive.output import console, notify_sound, prompt_yn
from keephive.storage import (
    append_to_daily,
    collect_todos,
    count_stale_facts,
    daily_dir,
    due_recurring,
    ensure_daily,
    guides_dir,
    memory_file,
    open_todos,
    read_memory,
    read_rules,
    recent_dones,
    rules_file,
    safe_read_text,
)

# ---------------------------------------------------------------------------
# Perspective 1: The Vault (knowledge quality)
# ---------------------------------------------------------------------------


def _analyze_vault() -> dict:
    """Knowledge quality analysis from memory.md + daily logs.

    Every metric is a concrete count or date.
    """
    mem = memory_file()
    stale = count_stale_facts()
    total = 0
    oldest_stale: str | None = None

    if mem.exists():
        text = mem.read_text()
        cutoff = get_today() - timedelta(days=30)
        for line in text.splitlines():
            m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
            if m:
                total += 1
                try:
                    vdate = date.fromisoformat(m.group(1))
                    if vdate < cutoff:
                        if oldest_stale is None or m.group(1) < oldest_stale:
                            oldest_stale = m.group(1)
                except ValueError:
                    pass

    return {
        "stale_facts": stale,
        "total_facts": total,
        "correction_count_7d": _count_category_entries("CORRECTION", days=7),
        "insight_count_30d": _count_category_entries("INSIGHT", days=30),
        "guide_count": _guide_count(),
        "memory_line_count": _memory_line_count(),
        "oldest_stale_date": oldest_stale,
    }


def _guide_count() -> int:
    """Count knowledge guides."""
    gd = guides_dir()
    if not gd.exists():
        return 0
    return sum(1 for _ in gd.glob("*.md"))


def _memory_line_count() -> int:
    """Count non-empty lines in memory.md."""
    mem = memory_file()
    if not mem.exists():
        return 0
    return sum(1 for line in mem.read_text().splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Perspective 2: The Cleaner (execution discipline)
# ---------------------------------------------------------------------------


def _analyze_cleaner() -> dict:
    """Execution discipline from TODOs + recurring.

    Every metric is a concrete count, ratio, or date comparison.
    """
    todos_all, dones_set = collect_todos()
    ot = open_todos()
    t = get_today()

    velocity = _todo_velocity_7d()

    # Stale TODOs (>7 days old)
    stale_todos = sum(1 for d, _, _ in ot if d < (t - timedelta(days=7)).isoformat())

    # Duplicate detection (SequenceMatcher > 0.7 on text)
    texts = [text for _, _, text in ot]
    dupe_count = 0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if SequenceMatcher(None, texts[i].lower(), texts[j].lower()).ratio() > 0.7:
                dupe_count += 1

    # Completion rate
    total_created = len(todos_all)
    total_done = len(dones_set)
    completion_rate = total_done / max(1, total_created)

    # Days since last activity (DONE or TODO entry)
    last_activity = _days_since_last_entry()

    return {
        "todo_completion_rate": round(completion_rate, 2),
        "stale_todos": stale_todos,
        "duplicate_todos": dupe_count,
        "overdue_recurring": len(due_recurring()),
        "todo_velocity_7d": velocity,
        "open_count": len(ot),
        "days_since_last_entry": last_activity,
    }


def _todo_velocity_7d() -> dict:
    """TODOs created vs completed per day over last 7 days."""
    d = daily_dir()
    if not d.exists():
        return {"created": 0, "completed": 0}

    cutoff = (get_today() - timedelta(days=7)).isoformat()
    created = 0
    completed = 0

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            if re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*TODO:", line) or re.match(r"^- TODO:", line):
                created += 1
            elif re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*DONE:", line) or re.match(r"^- DONE:", line):
                completed += 1

    return {"created": created, "completed": completed}


def _days_since_last_entry() -> int:
    """Days since the most recent daily log entry. 0 = today."""
    d = daily_dir()
    if not d.exists():
        return 999

    files = sorted(d.glob("*.md"), reverse=True)
    if not files:
        return 999

    # Most recent file with actual entries (not just header)
    for fpath in files:
        lines = safe_read_text(fpath).splitlines()
        has_entries = any(line.startswith("- ") for line in lines)
        if has_entries:
            try:
                file_date = date.fromisoformat(fpath.stem)
                return (get_today() - file_date).days
            except ValueError:
                continue
    return 999


# ---------------------------------------------------------------------------
# Perspective 3: The Strategist (direction alignment)
# ---------------------------------------------------------------------------


def _analyze_strategist() -> dict:
    """Direction alignment from concrete counts.

    No semantic matching. Just: do you have a strategy guide,
    how many decisions are you making, what is your entry distribution.
    """
    return {
        "has_strategy": _strategy_guide_exists(),
        "has_rules": rules_file().exists(),
        "decision_count_7d": _count_category_entries("DECISION", days=7),
        "fact_count_7d": _count_category_entries("FACT", days=7),
        "topic_distribution_7d": _topic_distribution_7d(),
        "active_days_7d": _active_days(7),
    }


def _strategy_guide_exists() -> bool:
    """Check if a strategy knowledge guide exists."""
    gd = guides_dir()
    if not gd.exists():
        return False
    return any(
        "strategy" in g.stem.lower() or "direction" in g.stem.lower() for g in gd.glob("*.md")
    )


def _topic_distribution_7d() -> dict[str, int]:
    """Distribution of entry categories over 7 days. Pure counting."""
    d = daily_dir()
    if not d.exists():
        return {}

    cutoff = (get_today() - timedelta(days=7)).isoformat()
    categories: Counter[str] = Counter()

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            for cat in ("FACT", "DECISION", "CORRECTION", "TODO", "INSIGHT", "DONE"):
                if re.match(rf"^- \[\d{{2}}:\d{{2}}:\d{{2}}\]\s*{cat}:", line) or re.match(
                    rf"^- {cat}:", line
                ):
                    categories[cat] += 1
                    break

    return dict(categories)


def _active_days(window: int = 7) -> int:
    """Count days with at least one entry in the last N days."""
    d = daily_dir()
    if not d.exists():
        return 0

    cutoff = (get_today() - timedelta(days=window)).isoformat()
    count = 0
    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        lines = safe_read_text(fpath).splitlines()
        if any(line.startswith("- ") for line in lines):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------


def _compute_score(vault: dict, cleaner: dict, strategist: dict) -> int:
    """Quality Pulse score: 0-100.

    Every deduction maps to a concrete, fixable issue.
    Each perspective caps at 30 points.
    """
    score = 100

    # Vault penalties (capped at 30)
    vault_penalty = 0
    vault_penalty += vault["stale_facts"] * 10  # Each stale fact = -10
    vault_penalty += min(10, vault["correction_count_7d"])  # High corrections = churn
    score -= min(30, vault_penalty)

    # Cleaner penalties (capped at 30)
    cleaner_penalty = 0
    cleaner_penalty += cleaner["stale_todos"] * 3  # Each stale TODO = -3
    cleaner_penalty += cleaner["duplicate_todos"] * 5  # Each dupe pair = -5
    cleaner_penalty += cleaner["overdue_recurring"] * 5  # Each overdue = -5
    score -= min(30, cleaner_penalty)
    # Bonus for completing TODOs
    score += int(cleaner["todo_completion_rate"] * 10)

    # Strategist penalties (capped at 30)
    strategist_penalty = 0
    if not strategist["has_strategy"]:
        strategist_penalty += 10
    if not strategist["has_rules"]:
        strategist_penalty += 5
    if strategist["decision_count_7d"] == 0:
        strategist_penalty += 5  # Making no explicit decisions
    if strategist["active_days_7d"] < 3:
        strategist_penalty += 10  # Low usage
    score -= min(30, strategist_penalty)

    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# LLM perspective calls
# ---------------------------------------------------------------------------


def _gather_daily_logs_text(days: int = 7) -> str:
    """Collect daily log text for the last N days."""
    d = daily_dir()
    if not d.exists():
        return "(no daily logs)"

    cutoff = (get_today() - timedelta(days=days)).isoformat()
    parts: list[str] = []

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        content = safe_read_text(fpath).strip()
        if content:
            parts.append(f"--- {fpath.stem} ---\n{content}")

    return "\n\n".join(parts) if parts else "(no daily logs)"


def _gather_corrections_text(days: int = 7) -> str:
    """Extract CORRECTION entries from recent daily logs."""
    d = daily_dir()
    if not d.exists():
        return "(no corrections)"

    cutoff = (get_today() - timedelta(days=days)).isoformat()
    lines: list[str] = []

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            if re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*CORRECTION:", line) or re.match(
                r"^- CORRECTION:", line
            ):
                lines.append(f"[{fpath.stem}] {line}")

    return "\n".join(lines) if lines else "(no corrections)"


def _gather_decisions_text(days: int = 7) -> str:
    """Extract DECISION entries from recent daily logs."""
    d = daily_dir()
    if not d.exists():
        return "(no decisions)"

    cutoff = (get_today() - timedelta(days=days)).isoformat()
    lines: list[str] = []

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            if re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*DECISION:", line) or re.match(
                r"^- DECISION:", line
            ):
                lines.append(f"[{fpath.stem}] {line}")

    return "\n".join(lines) if lines else "(no decisions)"


def _gather_verify_results(days: int = 7) -> str:
    """Extract verify-related entries from recent daily logs.

    Looks for FACT entries about verification verdicts and CORRECTION entries
    that resulted from verification.
    """
    d = daily_dir()
    if not d.exists():
        return "(no verify results)"

    cutoff = (get_today() - timedelta(days=days)).isoformat()
    lines: list[str] = []

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            # Match verify verdicts and verify-triggered corrections
            if re.search(r"(VALID|STALE|UNCERTAIN|verified|verify)", line, re.IGNORECASE):
                if re.match(r"^- \[\d{2}:\d{2}:\d{2}\]", line) or re.match(
                    r"^- (FACT|CORRECTION):", line
                ):
                    lines.append(f"[{fpath.stem}] {line}")

    return "\n".join(lines) if lines else "(no recent verify results)"


def _get_strategy_text() -> str:
    """Read the strategy guide content, if one exists."""
    gd = guides_dir()
    if not gd.exists():
        return "No strategy guide exists."

    for g in gd.glob("*.md"):
        if "strategy" in g.stem.lower() or "direction" in g.stem.lower():
            return safe_read_text(g)

    return "No strategy guide exists."


def _call_vault(vault_metrics: dict, verbose: bool = False):
    """Run The Vault perspective via claude -p."""
    from keephive.claude import run_claude_pipe
    from keephive.models import VaultPerspective

    memory_text = read_memory() or "(empty)"
    daily_logs_text = _gather_daily_logs_text(7)
    metrics_json = json.dumps(vault_metrics, indent=2)

    # Extract recent verify results from daily logs
    verify_text = _gather_verify_results(7)

    prompt = f"""You are The Vault, analyzing knowledge quality in a developer's memory system.

Read the working memory and daily logs below. Produce a candid analysis:
- Are facts current or decaying? Check [verified:DATE] tags in working memory.
  Cross-reference with recent verify results below to see if verification is happening.
- Are insights being captured and promoted to lasting knowledge?
- Is working memory lean and useful, or bloated with noise?
- Are corrections happening (good: learning) or piling up (bad: unstable foundation)?

Be specific. Cite actual entries you find concerning or noteworthy.

=== WORKING MEMORY ===
{memory_text}

=== DAILY LOGS (7 days) ===
{daily_logs_text}

=== RECENT VERIFY RESULTS (7 days) ===
{verify_text}

=== METRICS ===
{metrics_json}"""

    return run_claude_pipe(prompt, VaultPerspective, model="haiku", verbose=verbose, timeout=240)


def _call_cleaner(cleaner_metrics: dict, verbose: bool = False):
    """Run The Cleaner perspective via claude -p."""
    from keephive.claude import run_claude_pipe
    from keephive.models import CleanerPerspective

    rules_text = read_rules() or "(no rules)"
    ot = open_todos()
    open_todos_text = (
        "\n".join(f"[{d} {t}] {text}" for d, t, text in ot) if ot else "(no open TODOs)"
    )
    rd = recent_dones(days=7)
    recent_dones_text = (
        "\n".join(f"[{d}] {text}" for d, text in rd) if rd else "(no recent completions)"
    )
    corrections_text = _gather_corrections_text(7)
    metrics_json = json.dumps(cleaner_metrics, indent=2)

    prompt = f"""You are The Cleaner, analyzing execution discipline.

Read the rules and TODO/DONE history below. Produce a candid analysis:
- Say/do gap: are rules being followed? Do CORRECTION entries reveal rules being violated?
- Are TODOs getting completed or accumulating?
- Are there patterns of starting things but not finishing?
- Are recurring tasks being maintained or ignored?

Be specific. Quote actual rules and corrections that conflict.

=== RULES ===
{rules_text}

=== OPEN TODOs ===
{open_todos_text}

=== RECENT DONEs ===
{recent_dones_text}

=== DAILY LOG CORRECTIONS (7 days) ===
{corrections_text}

=== METRICS ===
{metrics_json}"""

    return run_claude_pipe(prompt, CleanerPerspective, model="haiku", verbose=verbose, timeout=240)


def _call_strategist(strategist_metrics: dict, verbose: bool = False):
    """Run The Strategist perspective via claude -p."""
    from keephive.claude import run_claude_pipe
    from keephive.models import StrategistPerspective

    strategy_text = _get_strategy_text()
    decisions_text = _gather_decisions_text(7)
    topics_json = json.dumps(strategist_metrics.get("topic_distribution_7d", {}), indent=2)
    metrics_json = json.dumps(strategist_metrics, indent=2)

    prompt = f"""You are The Strategist, analyzing direction alignment.

Read the strategy guide and recent decisions below. Produce a candid analysis:
- Does daily work actually serve stated strategic goals?
- Are decisions consistent or reversing?
- What areas get heavy effort but have no strategic justification?
- What strategic priorities are stated but receive no activity?

If no strategy guide exists, note that as a gap.

=== STRATEGY GUIDE ===
{strategy_text}

=== RECENT DECISIONS (7 days) ===
{decisions_text}

=== TOPIC DISTRIBUTION (7 days) ===
{topics_json}

=== METRICS ===
{metrics_json}"""

    return run_claude_pipe(
        prompt, StrategistPerspective, model="haiku", verbose=verbose, timeout=240
    )


def _run_perspectives(
    vault_metrics: dict, cleaner_metrics: dict, strategist_metrics: dict, verbose: bool = False
):
    """Run 3 perspective calls in parallel with live progress display."""
    from rich.live import Live
    from rich.text import Text

    names = {"vault": "Vault", "cleaner": "Cleaner", "strategist": "Strategist"}
    states: dict[str, str] = {k: "analyzing" for k in names}
    finished_at: dict[str, float] = {}
    results: dict[str, object] = {}
    start = time.monotonic()

    def render() -> Text:
        lines = []
        for key, label in names.items():
            elapsed = finished_at.get(key, time.monotonic()) - start
            if states[key] == "done":
                lines.append(
                    f"  [green]{label:<14}[/green] [green]\u2713[/green] complete ({elapsed:.0f}s)"
                )
            else:
                lines.append(
                    f"  [blue]{label:<14}[/blue] [blue]\u25cf[/blue] analyzing... ({elapsed:.0f}s)"
                )
        return Text.from_markup("\n".join(lines))

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_call_vault, vault_metrics, verbose): "vault",
            pool.submit(_call_cleaner, cleaner_metrics, verbose): "cleaner",
            pool.submit(_call_strategist, strategist_metrics, verbose): "strategist",
        }
        with Live(render(), console=console, refresh_per_second=4) as live:
            for future in as_completed(futures):
                key = futures[future]
                states[key] = "done"
                finished_at[key] = time.monotonic()
                results[key] = future.result()
                live.update(render())

    return results["vault"], results["cleaner"], results["strategist"]


def _run_cook(
    vault_r,
    cleaner_r,
    strategist_r,
    vault_m: dict,
    cleaner_m: dict,
    strategist_m: dict,
    score: int,
    previous_play: dict | None,
    verbose: bool = False,
):
    """Run The Cook synthesis after all 3 perspectives complete."""
    from keephive.claude import run_claude_pipe
    from keephive.models import AuditSynthesis

    all_metrics = {
        "vault": vault_m,
        "cleaner": cleaner_m,
        "strategist": strategist_m,
    }
    metrics_json = json.dumps(all_metrics, indent=2)

    prev_play_text = "No previous audit"
    if previous_play:
        status = (
            "completed"
            if previous_play["completed"]
            else f"open ({previous_play['age_days']}d old)"
        )
        prev_play_text = f"{previous_play['action']} [{status}] (from {previous_play['date']})"

    prompt = f"""You are The Cook. Three analysts have independently examined a developer's
knowledge management system. Read their reports and produce a synthesis.

CONNECTION: A non-obvious pattern where two perspectives align on something
the developer probably hasn't noticed. Be specific and cite from the reports.

TENSION: "You say [X]. You do [Y]. The cost is [Z]."
The say/do gap. Cite specific rules, facts, or decisions that conflict
with actual behavior shown in the data.

PLAYS: 3-5 specific actions ranked by impact. Most impactful first.
Each action needs:
- issue: one-line description of the problem found
- command: the exact hive command that fixes it
Available commands: hive v, hive todo, hive todo done <pat>,
hive ke <name>, hive r "...", hive rf, hive doctor, hive dr,
hive session verify, hive session todo
Do not include explanations. Just the issue and command.

WILD CARD: An unexpected opportunity or pattern hiding in the data
that none of the three analysts would flag on their own.

=== VAULT REPORT ===
{vault_r.analysis}
Issues: {json.dumps(vault_r.issues)}

=== CLEANER REPORT ===
{cleaner_r.analysis}
Issues: {json.dumps(cleaner_r.issues)}

=== STRATEGIST REPORT ===
{strategist_r.analysis}
Issues: {json.dumps(strategist_r.issues)}

=== CONCRETE METRICS ===
Score: {score}/100
{metrics_json}

=== PREVIOUS PLAY ===
{prev_play_text}"""

    return run_claude_pipe(prompt, AuditSynthesis, model="sonnet", verbose=verbose, timeout=240)


# ---------------------------------------------------------------------------
# Closed-loop tracking
# ---------------------------------------------------------------------------


def _check_previous_play() -> dict | None:
    """Check if the previous audit's Play was completed."""
    d = daily_dir()
    if not d.exists():
        return None

    cutoff = (get_today() - timedelta(days=14)).isoformat()
    plays: list[tuple[str, str]] = []

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            m = re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*TODO: \[audit\]\s*(.*)", line)
            if m:
                plays.append((fpath.stem, m.group(1).strip()))

    if not plays:
        return None

    last_date, last_play = plays[-1]
    _, dones = collect_todos()

    # Match play against dones, accounting for [audit] prefix in DONE entries.
    # collect_todos() preserves bracketed prefixes (e.g. "[audit] Fix X") in
    # DONE text, but our regex stripped the [audit] prefix from last_play.
    # Check both the raw text and prefix-stripped forms.
    norm_play = last_play.lower()
    completed = norm_play in dones
    if not completed:
        completed = any(
            re.sub(r"^\[[\w-]+\]\s*", "", d).strip() == norm_play for d in dones
        )
    age = (get_today() - date.fromisoformat(last_date)).days

    return {
        "action": last_play,
        "date": last_date,
        "completed": completed,
        "age_days": age,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _score_color(score: int) -> str:
    """Return Rich color tag for score."""
    if score >= 80:
        return "ok"
    if score >= 60:
        return "warn"
    return "err"


def _display_metrics(
    score: int, vault: dict, cleaner: dict, strategist: dict, previous_play: dict | None
) -> None:
    """Render metrics-only output (no LLM synthesis). Used by HIVE_SKIP_LLM path."""
    console.print()

    color = _score_color(score)
    console.print(f"  [{color}]Quality Pulse: {score}/100[/{color}]")
    console.print()

    if previous_play:
        if previous_play["completed"]:
            console.print(f"  [ok]Previous action completed:[/ok] {previous_play['action']}")
        else:
            age = previous_play["age_days"]
            console.print(
                f"  [warn]Unfinished action ({age}d old):[/warn] {previous_play['action']}"
            )
            console.print(
                '    \u2192 [bold]hive audit[/bold] (re-assess)  |  [bold]hive todo done "audit"[/bold] (mark done)'
            )
        console.print()

    _display_metrics_body(vault, cleaner, strategist)


def _display_metrics_body(vault: dict, cleaner: dict, strategist: dict) -> None:
    """Vault/cleaner/strategist metric details (no score header)."""
    # Vault
    console.print("  [bold]The Vault[/bold] (knowledge quality)")
    v_ok = vault["total_facts"] - vault["stale_facts"]
    if vault["total_facts"] > 0:
        if vault["stale_facts"] > 0:
            console.print(
                f"    {vault['total_facts']} facts ({v_ok} ok, "
                f"[warn]{vault['stale_facts']} stale[/warn])"
            )
        else:
            console.print(f"    {vault['total_facts']} facts ({v_ok} ok)")
    else:
        console.print("    No verified facts yet")

    if vault["correction_count_7d"] > 0:
        console.print(f"    {vault['correction_count_7d']} correction(s) this week")
    if vault["guide_count"] > 0:
        console.print(f"    {vault['guide_count']} knowledge guide(s)")
    if vault["memory_line_count"] > 40:
        console.print(
            f"    [warn]Memory: {vault['memory_line_count']} lines (consider trimming)[/warn]"
        )
    console.print()

    # Cleaner
    console.print("  [bold]The Cleaner[/bold] (execution discipline)")
    rate_pct = int(cleaner["todo_completion_rate"] * 100)
    console.print(f"    Completion rate: {rate_pct}%")

    vel = cleaner["todo_velocity_7d"]
    console.print(f"    7d velocity: {vel['created']} created, {vel['completed']} completed")

    issues: list[str] = []
    if cleaner["stale_todos"] > 0:
        issues.append(f"{cleaner['stale_todos']} stale TODO(s)")
    if cleaner["duplicate_todos"] > 0:
        issues.append(f"{cleaner['duplicate_todos']} duplicate(s)")
    if cleaner["overdue_recurring"] > 0:
        issues.append(f"{cleaner['overdue_recurring']} overdue recurring")
    if issues:
        console.print(f"    [warn]{', '.join(issues)}[/warn]")
    console.print()

    # Strategist
    console.print("  [bold]The Strategist[/bold] (direction alignment)")
    if strategist["has_strategy"]:
        console.print("    Strategy guide: yes")
    else:
        console.print("    [dim]No strategy guide (hive ke strategy to create)[/dim]")

    console.print(
        f"    {strategist['decision_count_7d']} decision(s) | {strategist['fact_count_7d']} fact(s) this week"
    )
    console.print(f"    Active {strategist['active_days_7d']}/7 days")

    topics = strategist.get("topic_distribution_7d", {})
    if topics:
        top_items = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:4]
        topic_str = ", ".join(f"{k}:{v}" for k, v in top_items)
        console.print(f"    Topics: {topic_str}")
    console.print()


def _display_perspective(name: str, perspective) -> None:
    """Render one perspective's full analysis essay + issues."""
    console.print(f"  [bold]{name} Analysis[/bold]")
    console.print(f"    {perspective.analysis}")
    if perspective.issues:
        for issue in perspective.issues:
            console.print(f"    [warn]- {issue}[/warn]")
    console.print()


def display_audit(
    score: int,
    vault_r,
    cleaner_r,
    strategist_r,
    synthesis,
    vault: dict,
    cleaner: dict,
    strategist: dict,
    previous_play: dict | None,
    verbose: bool = False,
) -> None:
    """Render audit output. Compact by default, full essays with verbose=True."""
    console.print()

    # Score
    color = _score_color(score)
    console.print(f"  [{color}]Quality Pulse: {score}/100[/{color}]")
    console.print()

    # Previous action status
    if previous_play:
        if previous_play["completed"]:
            console.print(f"  [ok]Previous action completed:[/ok] {previous_play['action']}")
        else:
            age = previous_play["age_days"]
            console.print(
                f"  [warn]Unfinished action ({age}d old):[/warn] {previous_play['action']}"
            )
            console.print(
                '    \u2192 [bold]hive audit[/bold] (re-assess)  |  [bold]hive todo done "audit"[/bold] (mark done)'
            )
        console.print()

    # Verbose: metrics + full perspective essays
    if verbose:
        _display_metrics_body(vault, cleaner, strategist)
        _display_perspective("Vault", vault_r)
        _display_perspective("Cleaner", cleaner_r)
        _display_perspective("Strategist", strategist_r)

    # Ranked action list
    if synthesis.plays:
        console.print("  [bold]Do next:[/bold]")
        for i, play in enumerate(synthesis.plays, 1):
            console.print(f"    {i}. {play.issue}")
            console.print(f"       \u2192 [bold]{play.command}[/bold]")
        console.print()

    # Synthesis insights
    console.print(f"  [info]Connection:[/info] {synthesis.connection}")
    console.print()
    console.print(f"  [info]Tension:[/info] {synthesis.tension}")
    console.print()
    console.print(f"  [dim]Wild Card:[/dim] {synthesis.wild_card}")
    console.print()
    # Footer is printed by cmd_audit() after save_audit_insights()


def display_perspectives_only(vault_r, cleaner_r, strategist_r, score: int) -> None:
    """Show perspective reports when Cook synthesis fails."""
    console.print()
    console.print(f"  Quality Pulse: {score}/100")
    console.print()

    console.print("  [bold]Vault Analysis[/bold]")
    console.print(f"    {vault_r.analysis}")
    console.print()

    console.print("  [bold]Cleaner Analysis[/bold]")
    console.print(f"    {cleaner_r.analysis}")
    console.print()

    console.print("  [bold]Strategist Analysis[/bold]")
    console.print(f"    {strategist_r.analysis}")
    console.print()

    console.print("  [warn]Cook synthesis failed. Showing perspective reports only.[/warn]")
    console.print()


# ---------------------------------------------------------------------------
# Save insights to daily log
# ---------------------------------------------------------------------------


def save_audit_insights(synthesis, score: int) -> int:
    """Append audit insights to today's daily log. Returns count saved."""
    ensure_daily()
    ts = get_now().strftime("%H:%M:%S")
    count = 0

    # 3 INSIGHTs: connection, tension, wild card
    append_to_daily(f"- [{ts}] INSIGHT: [audit] Connection: {synthesis.connection}")
    append_to_daily(f"- [{ts}] INSIGHT: [audit] Tension: {synthesis.tension}")
    append_to_daily(f"- [{ts}] INSIGHT: [audit] Wild Card: {synthesis.wild_card}")
    count += 3

    # 1 TODO: top play only (most impactful, avoids pile-up)
    if synthesis.plays:
        top = synthesis.plays[0]
        if top.issue.lower() != "no urgent action needed":
            append_to_daily(f"- [{ts}] TODO: [audit] {top.issue}")
            count += 1

    # 1 FACT: score
    append_to_daily(f"- [{ts}] FACT: Quality Pulse score: {score}/100")
    count += 1

    return count


# ---------------------------------------------------------------------------
# Helpers: daily log scanning
# ---------------------------------------------------------------------------


def _count_category_entries(category: str, days: int = 7) -> int:
    """Count entries of a category in recent daily logs."""
    d = daily_dir()
    if not d.exists():
        return 0

    cutoff = (get_today() - timedelta(days=days)).isoformat()
    count = 0
    pattern = re.compile(rf"^- \[\d{{2}}:\d{{2}}:\d{{2}}\]\s*{category}:|^- {category}:")

    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            if pattern.match(line):
                count += 1

    return count


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def cmd_audit(args: list[str]) -> None:
    """Run the three perspectives, score, synthesize, display."""
    verbose = "--verbose" in args or "-v" in args
    json_mode = "--json" in args

    # 1. Gather concrete metrics (fast, <100ms)
    vault = _analyze_vault()
    cleaner = _analyze_cleaner()
    strategist = _analyze_strategist()
    score = _compute_score(vault, cleaner, strategist)
    previous_play = _check_previous_play()

    # Metrics-only mode (for tests and fast checks)
    if os.environ.get("HIVE_SKIP_LLM"):
        if json_mode:
            print(
                json.dumps(
                    {
                        "score": score,
                        "vault": vault,
                        "cleaner": cleaner,
                        "strategist": strategist,
                        "previous_play": previous_play,
                    }
                )
            )
            return

        _display_metrics(score, vault, cleaner, strategist, previous_play)
        return

    # Confirm before LLM calls
    if not prompt_yn("  Run 3-perspective LLM analysis?"):
        _display_metrics(score, vault, cleaner, strategist, previous_play)
        return

    console.print()

    # 2. Run 3 perspective LLM calls in parallel (with live progress)
    try:
        vault_r, cleaner_r, strategist_r = _run_perspectives(vault, cleaner, strategist, verbose)
    except Exception as e:
        notify_sound(False)
        console.print(f"  [err]Perspective analysis failed: {e}[/err]")
        console.print("  [dim]Check: claude -p availability, CLAUDECODE env var[/dim]")
        return

    # 3. Run Cook synthesis (sequential, needs all 3 outputs)
    try:
        with console.status("  Synthesizing insights...", spinner="dots"):
            synthesis = _run_cook(
                vault_r,
                cleaner_r,
                strategist_r,
                vault,
                cleaner,
                strategist,
                score,
                previous_play,
                verbose,
            )
    except Exception as e:
        notify_sound(False)
        console.print(f"  [err]Synthesis failed: {e}[/err]")
        console.print("  [dim]Check: claude -p availability, CLAUDECODE env var[/dim]")
        display_perspectives_only(vault_r, cleaner_r, strategist_r, score)
        return

    # 4. Display
    display_audit(
        score,
        vault_r,
        cleaner_r,
        strategist_r,
        synthesis,
        vault,
        cleaner,
        strategist,
        previous_play,
        verbose=verbose,
    )

    # 5. Save insights to daily log
    count = save_audit_insights(synthesis, score)
    notify_sound(True)

    # 6. Footer (after save so we have the count)
    if not verbose:
        console.print(f"  [dim]Top action saved as TODO. {count} entries saved to daily log.[/dim]")
        console.print("  [dim]hive a -v for full perspective analyses[/dim]")
        console.print()

    if json_mode:
        print(
            json.dumps(
                {
                    "score": score,
                    "vault": vault,
                    "cleaner": cleaner,
                    "strategist": strategist,
                    "synthesis": {
                        "plays": [
                            {"issue": p.issue, "command": p.command} for p in synthesis.plays
                        ],
                        "connection": synthesis.connection,
                        "tension": synthesis.tension,
                        "wild_card": synthesis.wild_card,
                    },
                    "previous_play": previous_play,
                }
            )
        )
