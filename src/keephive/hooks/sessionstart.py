"""SessionStart hook handler.

Called by Claude Code at the start of each session.
Outputs additionalContext JSON to inject working memory,
rules, TODOs, warnings, and recent entries into context.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from keephive.storage import (
    active_slot,
    backup_and_write,
    count_stale_facts,
    due_recurring,
    get_meaningful_entries,
    get_stale_facts,
    guides_dir,
    hive_dir,
    memory_file,
    open_todos,
    read_memory,
    read_rules,
    slot_file,
)


def hook_sessionstart(args: list[str]) -> None:
    """Main entry point for SessionStart hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        input_data = {}

    import os as _os
    import time as _time

    _skip = False
    try:
        _sig = hive_dir() / ".session-launched"
        if _sig.exists():
            _ts = int(_sig.read_text().strip())
            if _time.time() - _ts < 15:
                _skip = True
            _sig.unlink(missing_ok=True)
    except Exception:
        pass
    if not _skip:
        _skip = bool(_os.environ.get("HIVE_SESSION_LAUNCHED"))  # env var fallback
    if _skip:
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "",
                    }
                }
            )
        )
        return

    cwd = input_data.get("cwd", "")
    project_name = Path(cwd).name if cwd else ""

    # Track usage
    try:
        from keephive.storage import track_event

        track_event("hooks", "sessionstart", project=cwd, source="hook")
    except Exception:
        pass

    # Seed missing guides; never overwrite existing (preserve user customizations)
    try:
        from keephive.commands.setup import _seed_bundled_content

        _seed_bundled_content(quiet=True, seed_only=True)
    except Exception:
        pass

    # Build context
    context = build_context(cwd, project_name)

    # Output as JSON for additionalContext
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    try:
        sys.stdout.write(json.dumps(output))
    except Exception:
        debug_log = hive_dir() / ".hook-debug.log"
        with open(debug_log, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] sessionstart encoding FAILED\n")
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "hive: encoding failed, see .hook-debug.log",
                    }
                }
            )
        )


def _active_draft_hint() -> str:
    """Return a one-line hint about the active note slot, or empty string."""
    slot = active_slot()
    path = slot_file(slot)
    if not path.exists():
        return ""
    content = path.read_text().strip()
    if not content:
        return ""
    words = len(content.split())
    flat = content.replace("\n", " ")
    preview = flat[:40] + ("..." if len(flat) > 40 else "")
    return f'slot {slot} · "{preview}" ({words} words)'


def build_context(cwd: str, project_name: str) -> str:
    """Build the context string injected into Claude Code.

    Optimized for model focus: injects only actionable context.
    Maintenance noise (Quality Pulse, accumulation warnings, guide update
    notifications, data quality warnings) lives in `hive s` output where
    the USER sees it, not in model context. Recent entries and past-week
    entries are available on demand via hive_recall.
    """
    parts: list[str] = []

    # 0. Auto-reverify stale facts (deterministic, no LLM, silent)
    _auto_reverify()

    # 1. Working memory
    mem = read_memory()
    if mem:
        parts.append(mem)

    # 2. Rules (actionable section only)
    rules = read_rules()
    if rules:
        # Try to extract just the actionable part
        lines = rules.splitlines()
        start_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("## When"):
                start_idx = i
                break
        if start_idx > 0:
            parts.append("\n".join(lines[start_idx:]))
        else:
            parts.append(rules)

    # 3. Stale fact warning (critical, always inject)
    stale = count_stale_facts()
    if stale > 0:
        parts.append(f"Warning: {stale} stale fact(s) need verification. Run: hive v")

    # 4. Open TODOs
    todos = open_todos()
    if todos:
        from datetime import date

        t = date.today()
        todo_lines = ["## Open TODOs"]
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
                time_part = f" {ts}" if ts else ""
                # Mark old TODOs as critical
                prefix = "CRITICAL: " if age > 3 else ""
                todo_lines.append(f"- [{age_s}{time_part}] {prefix}{text}")
            except ValueError:
                todo_lines.append(f"- [?] {text}")
        parts.append("\n".join(todo_lines))

    # 4b. Active draft hint
    draft_hint = _active_draft_hint()
    if draft_hint:
        parts.append(f"## Active Draft\n{draft_hint}")

    # 5. Due recurring tasks
    due = due_recurring()
    if due:
        recurring_lines = ["## Due Recurring Tasks"]
        for freq, text, overdue in due:
            over_s = f"+{overdue}d overdue" if overdue > 0 else "due today"
            recurring_lines.append(f"- [{freq}] {text} ({over_s})")
        parts.append("\n".join(recurring_lines))

    # 6. Smart guide injection based on cwd
    if cwd and project_name:
        guide_text = _match_guides(project_name, cwd)
        if guide_text:
            parts.append(guide_text)

    return "\n\n".join(parts)


def _data_quality_warnings() -> list[str]:
    """Generate lightweight data quality warnings."""
    from datetime import date, timedelta
    from difflib import SequenceMatcher

    from keephive.storage import collect_todos

    todos_all, dones_set = collect_todos()
    ot = [(d, t, text) for d, t, text in todos_all if text.lower() not in dones_set]
    warnings = []

    # Duplicate detection
    texts = [text for _, _, text in ot]
    dupe_count = 0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if SequenceMatcher(None, texts[i].lower(), texts[j].lower()).ratio() > 0.7:
                dupe_count += 1
    if dupe_count:
        warnings.append(f"{dupe_count} duplicate TODO pair(s) found. Run: hive doctor")

    # Stale TODOs
    t = date.today()
    stale = [text for d, _, text in ot if d < (t - timedelta(days=7)).isoformat()]
    if stale:
        warnings.append(f"{len(stale)} TODO(s) older than 7 days")

    # Accumulation
    if len(ot) > 10:
        warnings.append(f"{len(ot)} open TODOs. Consider consolidating.")

    return warnings


def _match_guides(project_name: str, cwd: str = "") -> str:
    """Find guides matching the current project or working directory path."""
    import re as _re

    gd = guides_dir()
    if not gd.exists():
        return ""

    matched_parts: list[str] = []
    total_words = 0
    max_words = 1500
    max_guides = 3
    count = 0

    for guide in sorted(gd.glob("*.md")):
        if count >= max_guides:
            break

        text = guide.read_text()
        matched = False
        paths_patterns: list[str] = []

        # Check tags/projects in front matter
        if text.startswith("---"):
            fm_lines = []
            for line in text.splitlines()[1:]:
                if line.startswith("---"):
                    break
                fm_lines.append(line)
            fm_text = " ".join(fm_lines).lower()
            if project_name.lower() in fm_text:
                matched = True

            # Extract paths: [...] from front matter for cwd matching
            paths_match = _re.search(r"paths:\s*\[([^\]]+)\]", " ".join(fm_lines))
            if paths_match:
                paths_patterns = [p.strip().strip("'\"") for p in paths_match.group(1).split(",")]

        # Check cwd against paths patterns
        if not matched and cwd and paths_patterns:
            for pattern in paths_patterns:
                if pattern and pattern in cwd:
                    matched = True
                    break

        # Fallback: filename matches project
        if not matched and project_name.lower() in guide.stem.lower():
            matched = True

        if matched:
            # Strip front matter for injection
            content = text
            if text.startswith("---"):
                lines = text.splitlines()
                end_idx = 0
                for i, line in enumerate(lines[1:], 1):
                    if line.startswith("---"):
                        end_idx = i + 1
                        break
                content = "\n".join(lines[end_idx:])

            words = len(content.split())
            if total_words + words <= max_words:
                matched_parts.append(f"--- Guide: {guide.stem} ---\n{content}")
                total_words += words
                count += 1

    if matched_parts:
        return "## Relevant Knowledge Guides\n" + "\n".join(matched_parts)
    return ""


def _auto_reverify() -> list[str]:
    """Deterministic re-verification of stale facts using recent daily logs.

    Checks if stale facts have matching entries in the last 7 days of daily logs.
    If word overlap > 50%, refreshes the [verified:YYYY-MM-DD] date.
    No LLM call. Runs in <100ms.

    Returns list of re-verified fact descriptions for the summary line.
    """
    import re
    from datetime import date, timedelta

    stale = get_stale_facts()
    if not stale:
        return []

    # Collect recent daily entries (7 days)
    recent_entries: list[str] = []
    today = date.today()
    for days_ago in range(7):
        day_str = (today - timedelta(days=days_ago)).isoformat()
        entries = get_meaningful_entries(day=day_str, limit=50)
        for entry in entries:
            # Strip formatting prefixes
            clean = re.sub(r"^\s*~?\s*\[[\d:]+\]\s*", "", entry)
            clean = re.sub(r"^(FACT|DECISION|CORRECTION|INSIGHT|TODO):\s*", "", clean)
            recent_entries.append(clean.lower())

    if not recent_entries:
        return []

    # Match stale facts against recent entries by word overlap
    mem_path = memory_file()
    if not mem_path.exists():
        return []

    content = mem_path.read_text()
    lines = content.split("\n")
    reverified: list[str] = []
    today_str = today.isoformat()
    changed = False

    for line_num, fact_text, _ in stale:
        fact_words = set(w.lower() for w in fact_text.split() if len(w) > 3)
        if not fact_words:
            continue

        # Check against recent daily entries
        for entry in recent_entries:
            entry_words = set(w.lower() for w in entry.split() if len(w) > 3)
            if not entry_words:
                continue
            overlap = len(fact_words & entry_words) / len(fact_words)
            if overlap > 0.5:
                # Update the verified date in-place
                idx = line_num - 1  # 1-based to 0-based
                if idx < len(lines):
                    clean = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", lines[idx]).rstrip(
                        "\n"
                    )
                    new_line = f"{clean} [verified:{today_str}]\n"
                    if new_line != lines[idx]:
                        lines[idx] = new_line
                        reverified.append(fact_text[:80])
                        changed = True
                break

    if changed:
        backup_and_write(mem_path, "\n".join(lines))

    return reverified


def _accumulation_warnings(mem_content: str) -> list[str]:
    """Generate accumulation warnings for memory.md.

    Returns actionable warning strings when memory.md grows large
    or has too many auto-captured facts.
    """
    import re
    from datetime import date, timedelta

    warnings: list[str] = []
    if not mem_content:
        return warnings

    # Count total facts (lines starting with "- ")
    fact_count = sum(1 for line in mem_content.splitlines() if line.startswith("- "))
    if fact_count > 40:
        warnings.append(f"Memory has {fact_count} facts. Consider consolidating: hive rf")

    # Count auto-captured facts
    in_auto = False
    auto_count = 0
    for line in mem_content.splitlines():
        if line.strip() == "## Auto-Captured":
            in_auto = True
            continue
        if line.startswith("#") and in_auto:
            break
        if in_auto and line.startswith("- "):
            auto_count += 1

    if auto_count > 5:
        warnings.append(f"{auto_count} auto-captured facts pending review. Curate: hive rf apply")

    # Check for critically stale facts (>60 days)
    cutoff_60 = (date.today() - timedelta(days=60)).isoformat()
    critical_stale = 0
    for line in mem_content.splitlines():
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
        if m and m.group(1) < cutoff_60:
            critical_stale += 1
    if critical_stale > 0:
        warnings.append(f"CRITICAL: {critical_stale} fact(s) unverified for 60+ days. Run: hive v")

    return warnings
