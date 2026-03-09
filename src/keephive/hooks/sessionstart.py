"""SessionStart hook handler.

Called by Claude Code at the start of each session.
Outputs additionalContext JSON to inject working memory,
rules, TODOs, warnings, and recent entries into context.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from keephive.clock import get_now, get_today
from keephive.storage import (
    active_slot,
    backup_and_write,
    count_stale_facts,
    daily_file,
    due_recurring,
    get_meaningful_entries,
    get_stale_facts,
    guides_dir,
    hive_dir,
    memory_file,
    read_memory,
    read_rules,
    safe_read_text,
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

    # Track session start
    try:
        from keephive.storage import track_session_event

        session_id = input_data.get("session_id", "")
        if session_id:
            track_session_event(session_id, "start", project=cwd)
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
            f.write(f"[{get_now().isoformat()}] sessionstart encoding FAILED\n")
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


def extract_active_themes(days: int = 7) -> list[str]:
    """Return active theme words from the last N days of daily logs.

    Uses the same parsing logic as extract_style_hint(). Returns the top
    words (appearing >= 2 times, filtered by STOPWORDS) up to 3 items.
    Returns an empty list when fewer than 5 entries exist or on any error.
    """
    import re
    from collections import Counter
    from datetime import timedelta

    try:
        from keephive.commands.wander import STOPWORDS
    except ImportError:
        STOPWORDS = frozenset()

    try:
        today = get_today()
        cat_re = re.compile(
            r"^- \[\d{2}:\d{2}:\d{2}\]\s*(FACT|DECISION|INSIGHT|TODO|CORRECTION):\s*(.*)"
        )
        entry_count = 0
        word_counts: Counter[str] = Counter()

        for days_ago in range(days):
            day_str = (today - timedelta(days=days_ago)).isoformat()
            df = daily_file(day_str)
            if not df.exists():
                continue
            try:
                content = safe_read_text(df)
            except OSError:
                continue
            for line in content.splitlines():
                m = cat_re.match(line)
                if not m:
                    continue
                text = m.group(2).strip()
                entry_count += 1
                for word in re.findall(r"[a-z]{4,}", text.lower()):
                    if word not in STOPWORDS:
                        word_counts[word] += 1

        if entry_count < 5:
            return []

        return [w for w, c in word_counts.most_common(10) if c >= 2][:3]

    except Exception:
        return []


def extract_style_hint(days: int = 7) -> str:
    """Compute a one-line writing style hint from recent daily log entries.

    Returns "[style: ~N chars/entry; dominant: CAT/CAT; active themes: x, y]"
    or "" if insufficient data or any error.
    """
    import re
    from collections import Counter
    from datetime import timedelta

    try:
        from keephive.commands.wander import STOPWORDS
    except ImportError:
        STOPWORDS = frozenset()

    try:
        today = get_today()
        cat_re = re.compile(
            r"^- \[\d{2}:\d{2}:\d{2}\]\s*(FACT|DECISION|INSIGHT|TODO|CORRECTION):\s*(.*)"
        )
        lengths: list[int] = []
        category_counts: Counter[str] = Counter()
        word_counts: Counter[str] = Counter()

        for days_ago in range(days):
            day_str = (today - timedelta(days=days_ago)).isoformat()
            df = daily_file(day_str)
            if not df.exists():
                continue
            try:
                content = safe_read_text(df)
            except OSError:
                continue
            for line in content.splitlines():
                m = cat_re.match(line)
                if not m:
                    continue
                cat, text = m.group(1), m.group(2).strip()
                category_counts[cat] += 1
                lengths.append(len(text))
                for word in re.findall(r"[a-z]{4,}", text.lower()):
                    if word not in STOPWORDS:
                        word_counts[word] += 1

        if len(lengths) < 5:
            return ""

        avg_len = sum(lengths) // len(lengths)
        top_cats = [cat for cat, _ in category_counts.most_common(2)]
        cats_str = "/".join(top_cats) if top_cats else "mixed"
        themes = [w for w, c in word_counts.most_common(10) if c >= 2][:3]

        parts = [f"~{avg_len} chars/entry", f"dominant: {cats_str}"]
        if themes:
            parts.append(f"active themes: {', '.join(themes)}")
        return f"[style: {'; '.join(parts)}]"

    except Exception:
        return ""


def build_context(cwd: str, project_name: str) -> str:
    """Build the context string injected into Claude Code.

    Optimized for model focus: injects only actionable context.
    Maintenance noise (Quality Pulse, accumulation warnings, guide update
    notifications, data quality warnings) lives in `hive s` output where
    the USER sees it, not in model context. Recent entries and past-week
    entries are available on demand via hive_recall.
    """
    parts: list[str] = []

    # 0a. Background loop: inject first-iteration prompt from .loop-prompt-{id}.txt.
    # HIVE_LOOP_ID is set in the tmux window environment by _launch_tmux_window().
    # Reading the already-written file avoids re-generating and keeps this hook fast.
    try:
        import os as _loop_os

        _loop_id = _loop_os.environ.get("HIVE_LOOP_ID")
        if _loop_id:
            _prompt_file = hive_dir() / f".loop-prompt-{_loop_id}.txt"
            if _prompt_file.exists():
                parts.append(_prompt_file.read_text())
    except Exception:
        pass  # Never let loop injection crash the sessionstart hook

    # 0. Auto-reverify stale facts (deterministic, no LLM, silent)
    _auto_reverify()

    # 0b. Expire experimental rules (deterministic, no LLM)
    try:
        from keephive.commands.memory import expire_experimental_rules

        expired = expire_experimental_rules()
        if expired:
            from keephive.storage import daily_dir

            dd = daily_dir()
            dd.mkdir(parents=True, exist_ok=True)
            log_file = dd / f"{get_today().isoformat()}.md"
            with open(log_file, "a") as f:
                for rule_text in expired:
                    f.write(f"- EXPIRED-RULE: {rule_text}\n")
    except Exception:
        pass  # Never crash sessionstart

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

    # 2b. Style hint (deterministic, never crashes)
    style_hint = extract_style_hint()
    if style_hint:
        parts.append(style_hint)

    # 3. Stale fact warning (critical, always inject)
    stale = count_stale_facts()
    if stale > 0:
        parts.append(f"Warning: {stale} fact(s) unverified 30+ days. Run: hive v")

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

    # 6. Smart guide injection based on cwd (with active-themes enrichment)
    if cwd and project_name:
        themes = extract_active_themes()
        guide_text = _match_guides(project_name, cwd, active_themes=themes)
        if guide_text:
            parts.append(guide_text)

    # 7. Cross-project activity hint
    if project_name:
        cross_hint = _cross_project_hint(project_name)
        if cross_hint:
            parts.append(cross_hint)

    # 7b. Pending rule suggestions (close data flow gap)
    try:
        pending_rules_path = hive_dir() / ".pending-rules.md"
        if pending_rules_path.exists():
            pr_content = pending_rules_path.read_text().strip()
            if pr_content:
                n = sum(1 for ln in pr_content.splitlines() if ln.strip().startswith("- "))
                if n > 0:
                    s = "s" if n != 1 else ""
                    parts.append(f"{n} pending rule suggestion{s}. Run: hive rule review")
    except Exception:
        pass

    # 7c. Pending facts — auto-triage clean ones into memory, count remainder
    try:
        import time as _time

        from keephive.commands.reflect import _promote_facts_to_memory
        from keephive.storage import (
            auto_triage_pending_facts,
            normalize_memory,
            write_pending_facts,
        )

        pending_facts_path = hive_dir() / ".pending-facts.md"
        triage_stamp = hive_dir() / ".auto-triage.stamp"

        if pending_facts_path.exists() and pending_facts_path.stat().st_size > 0:
            # Hourly gate: run at most once per hour to avoid over-aggressiveness
            last_run = triage_stamp.stat().st_mtime if triage_stamp.exists() else 0
            if _time.time() - last_run > 3600:
                memory_text = read_memory()
                promotable, needs_review = auto_triage_pending_facts(memory_text)
                if promotable:
                    promoted_count = _promote_facts_to_memory(promotable)
                    if promoted_count > 0:
                        write_pending_facts(needs_review)
                        triage_stamp.touch()

        # Silently prune double-[auto] tags and stale unverified auto facts on every session start
        normalize_memory(memory_file())

        # Count remaining pending facts for the nudge
        if pending_facts_path.exists():
            pf_content = pending_facts_path.read_text().strip()
            if pf_content:
                n = sum(1 for ln in pf_content.splitlines() if ln.strip().startswith("- "))
                if n > 0:
                    s = "s" if n != 1 else ""
                    parts.append(f"{n} fact{s} pending review. Run: hive mem review")
    except Exception:
        pass

    # 7d. KingBee activity today
    try:
        from keephive.storage import count_kingbee_today

        kb_count = count_kingbee_today()
        if kb_count > 0:
            s = "entry" if kb_count == 1 else "entries"
            parts.append(f"KingBee wrote {kb_count} {s} today. Run: hive inbox")
    except Exception:
        pass

    # 8. Session context (brief productivity signal)
    try:
        from keephive.storage import session_metrics

        sm = session_metrics(days_back=7)
        if sm["total_sessions"] > 2:
            parts.append(
                f"[session context: {sm['sessions_today']} sessions today, "
                f"avg {sm['avg_prompts_per_session']:.0f} prompts/session this week]"
            )
    except Exception:
        pass

    # 9. Next action hint
    try:
        from keephive.commands.status import _suggest_next

        next_cmd, next_reason = _suggest_next()
        if next_cmd:
            parts.append(f"Suggested next action: {next_cmd} ({next_reason})")
    except Exception:
        pass

    # 10. Agent identity (KingBee SOUL.md ## Summary + filtered learnings)
    try:
        from keephive.storage import read_soul_summary

        soul_summary = read_soul_summary(project_name=project_name or None)
        if soul_summary:
            parts.append("## Agent Identity\n" + soul_summary)
    except Exception:
        pass

    # 11. Fresh wander question (only if <= 2 days old)
    try:
        from datetime import date as _date

        from keephive.storage import list_wander_docs

        _recent = list_wander_docs(limit=1)
        if _recent:
            _doc = _recent[0]
            _date_str = _doc.get("date", "")
            if len(_date_str) == 8:
                _doc_date = _date.fromisoformat(f"{_date_str[:4]}-{_date_str[4:6]}-{_date_str[6:]}")
                _age = (get_today() - _doc_date).days
                if _age <= 2 and _doc.get("question"):
                    parts.append(f"KingBee wonders: {_doc['question']}")
    except Exception:
        pass

    return "\n\n".join(parts)


def _data_quality_warnings() -> list[str]:
    """Generate lightweight data quality warnings."""
    from datetime import timedelta
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
        warnings.append(
            f"{dupe_count} likely-duplicate TODO(s). Run: hive doctor (review and merge)"
        )

    # Stale TODOs
    t = get_today()
    stale = [text for d, _, text in ot if d < (t - timedelta(days=7)).isoformat()]
    if stale:
        warnings.append(
            f"{len(stale)} TODO(s) older than 7 days. Run: hive todo (review and close stale items)"
        )

    # Accumulation
    if len(ot) > 10:
        warnings.append(f"{len(ot)} open TODOs. Triage: hive todo done <pat>  |  hive doctor")

    return warnings


def _match_guides(
    project_name: str,
    cwd: str = "",
    active_themes: list[str] | None = None,
) -> str:
    """Find guides matching the current project or working directory path.

    Three-pass approach (priority order for slot allocation):
    1. Always-inject guides (``always: true`` in front matter)
    2. Project-matched guides (by tags/projects field, paths, or filename)
    3. Theme-matched guides (``tags:`` overlaps with active_themes)
    Budget: max 3 guides, 1500 words total.
    """
    import re as _re

    gd = guides_dir()
    if not gd.exists():
        return ""

    max_words = 1500
    max_guides = 3
    _active_themes = [t.lower() for t in (active_themes or [])]

    # Intermediate: (guide_path, content_without_frontmatter)
    always_guides: list[tuple[Path, str]] = []
    project_guides: list[tuple[Path, str]] = []
    theme_guides: list[tuple[Path, str]] = []

    for guide in sorted(gd.glob("*.md")):
        text = guide.read_text()
        is_always = False
        matched = False
        theme_matched = False
        paths_patterns: list[str] = []
        guide_tags: list[str] = []

        # Parse front matter
        if text.startswith("---"):
            fm_lines = []
            for line in text.splitlines()[1:]:
                if line.startswith("---"):
                    break
                fm_lines.append(line)
            fm_text = " ".join(fm_lines).lower()

            # Check always: true
            always_match = _re.search(r"always:\s*(true|yes)", fm_text)
            if always_match:
                is_always = True

            # Check tags/projects match
            if not is_always and project_name.lower() in fm_text:
                matched = True

            # Extract paths: [...] from front matter for cwd matching
            paths_match = _re.search(r"paths:\s*\[([^\]]+)\]", " ".join(fm_lines))
            if paths_match:
                paths_patterns = [p.strip().strip("'\"") for p in paths_match.group(1).split(",")]

            # Extract tags: [...] for theme matching
            if _active_themes:
                tags_match = _re.search(r"tags:\s*\[([^\]]+)\]", " ".join(fm_lines))
                if tags_match:
                    guide_tags = [
                        t.strip().strip("'\"").lower()
                        for t in tags_match.group(1).split(",")
                        if t.strip()
                    ]

        # Check cwd against paths patterns
        if not is_always and not matched and cwd and paths_patterns:
            for pattern in paths_patterns:
                if pattern and pattern in cwd:
                    matched = True
                    break

        # Fallback: filename matches project
        if not is_always and not matched and project_name.lower() in guide.stem.lower():
            matched = True

        # Theme match: tags overlap with active_themes (only when not already matched)
        if not is_always and not matched and guide_tags and _active_themes:
            if set(guide_tags) & set(_active_themes):
                theme_matched = True

        if is_always or matched or theme_matched:
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

            if is_always:
                always_guides.append((guide, content))
            elif matched:
                project_guides.append((guide, content))
            else:
                theme_guides.append((guide, content))

    # Fill slots: always guides first, then project-matched, then theme-matched
    matched_parts: list[str] = []
    injected_stems: list[str] = []
    total_words = 0
    count = 0

    for guide, content in always_guides + project_guides + theme_guides:
        if count >= max_guides:
            break
        words = len(content.split())
        if total_words + words <= max_words:
            matched_parts.append(f"--- Guide: {guide.stem} ---\n{content}")
            injected_stems.append(guide.stem)
            total_words += words
            count += 1

    if matched_parts:
        # Track guide injection hits for floor metrics
        try:
            from keephive.storage import track_event as _track_event

            for stem in injected_stems:
                _track_event("guide_hits", stem)
        except Exception:
            pass
        return "## Relevant Knowledge Guides\n" + "\n".join(matched_parts)
    return ""


def _cross_project_hint(current_project: str) -> str:
    """Scan recent daily logs for insights from other projects.

    Looks for ``[project:X]`` tags in the last 7 days of daily logs,
    counts insights per project (excluding the current one), and returns
    a one-liner hint enabling cross-project recall.

    Returns empty string when no cross-project tags are found.
    """
    import re
    from datetime import timedelta

    tag_re = re.compile(r"\[project:([^\]]+)\]")
    counts: dict[str, int] = {}
    today = get_today()

    for days_ago in range(7):
        day_str = (today - timedelta(days=days_ago)).isoformat()
        df = daily_file(day_str)
        if not df.exists():
            continue
        content = safe_read_text(df)
        for match in tag_re.finditer(content):
            proj = match.group(1)
            if proj.lower() != current_project.lower():
                counts[proj] = counts.get(proj, 0) + 1

    if not counts:
        return ""

    # Sort by count descending
    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    parts = [f"{name} ({n} insight{'s' if n != 1 else ''})" for name, n in ranked]
    return f"Recent work on other projects: {', '.join(parts)}. Use hive_recall() to search across projects."


def _auto_reverify() -> list[str]:
    """Deterministic re-verification of stale facts using recent daily logs.

    Checks if stale facts have matching entries in the last 7 days of daily logs.
    If word overlap > 50%, refreshes the [verified:YYYY-MM-DD] date.
    No LLM call. Runs in <100ms.

    Returns list of re-verified fact descriptions for the summary line.
    """
    import re
    from datetime import timedelta

    stale = get_stale_facts()
    if not stale:
        return []

    # Collect recent daily entries (7 days)
    recent_entries: list[str] = []
    today = get_today()
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
    from datetime import timedelta

    warnings: list[str] = []
    if not mem_content:
        return warnings

    # Count total facts (lines starting with "- ")
    fact_count = sum(1 for line in mem_content.splitlines() if line.startswith("- "))
    if fact_count > 40:
        warnings.append(f"Memory has {fact_count} facts. Consolidate: hive rf (scan for patterns)")

    # Count pending facts from .pending-facts.md
    try:
        pending_facts_path = hive_dir() / ".pending-facts.md"
        if pending_facts_path.exists():
            pf_content = pending_facts_path.read_text().strip()
            if pf_content:
                pf_count = sum(1 for ln in pf_content.splitlines() if ln.strip().startswith("- "))
                if pf_count > 0:
                    s = "s" if pf_count != 1 else ""
                    warnings.append(
                        f"{pf_count} fact{s} auto-captured, pending review. Run: hive mem review"
                    )
    except Exception:
        pass

    # Check for critically stale facts (>60 days)
    cutoff_60 = (get_today() - timedelta(days=60)).isoformat()
    critical_stale = 0
    for line in mem_content.splitlines():
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
        if m and m.group(1) < cutoff_60:
            critical_stale += 1
    if critical_stale > 0:
        warnings.append(
            f"CRITICAL: {critical_stale} fact(s) unverified 60+ days. Run: hive v (re-check against codebase)"
        )

    return warnings
