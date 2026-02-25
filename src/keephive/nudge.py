"""Shared nudge infrastructure for mid-session hooks.

Counter-based system that fires periodic reminders to use hive tools.
Uses a priority-based state machine instead of static rotations:
highest-priority actionable state always wins.

Recency gate: priorities 1-4 are suppressed if the same category fired
within _RECENCY_THRESHOLD (15) calls this session. Priority 5 (generic
fallback) is never gated — every nudge cycle always produces output.
State is stored as `last_surfaced` dict in counter files; session change
resets it automatically (session_id mismatch check).

Used by UserPromptSubmit, PostToolUse, and Stop hooks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_RECENCY_THRESHOLD = 15
_KB_RECENCY_THRESHOLD = 3      # KB queue nudges more frequently (unread directives)
_REVIEW_RECENCY_THRESHOLD = 8  # Review lag nudge (same cadence as stop hook)


def _counter_path(name: str) -> Path:
    """Path to counter file: ~/.keephive/hive/.{name}-counter (profile aware)."""
    from keephive.storage import hive_dir

    return hive_dir() / f".{name}-counter"


def _nudge_interval() -> int:
    """Interval between nudges. Default 5, configurable via HIVE_NUDGE_INTERVAL."""
    return max(1, int(os.environ.get("HIVE_NUDGE_INTERVAL", "5")))


def read_counter(name: str) -> tuple[int, str]:
    """Read counter from disk. Returns (count, session_id)."""
    path = _counter_path(name)
    if not path.exists():
        return 0, ""
    try:
        data = json.loads(path.read_text())
        return data.get("count", 0), data.get("session_id", "")
    except (json.JSONDecodeError, OSError):
        return 0, ""


def write_counter(name: str, count: int, session_id: str) -> None:
    """Write counter to disk."""
    path = _counter_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": count, "session_id": session_id}))


def read_recency(name: str, session_id: str) -> dict[str, int]:
    """Return last_surfaced dict, scoped to current session. Empty dict on any error or session change."""
    path = _counter_path(name)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if data.get("session_id", "") != session_id:
            return {}
        return data.get("last_surfaced", {})
    except (json.JSONDecodeError, OSError):
        return {}


def record_surfaced(name: str, category: str, count: int, session_id: str) -> None:
    """Write last_surfaced[category] = count into counter file. No-op on error."""
    path = _counter_path(name)
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        if data.get("session_id", "") != session_id:
            data = {"count": data.get("count", 0), "session_id": session_id}
        ls = data.get("last_surfaced", {})
        ls[category] = count
        data["last_surfaced"] = ls
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except (json.JSONDecodeError, OSError):
        pass


def should_nudge(name: str, session_id: str) -> tuple[bool, int]:
    """Increment counter, return (should_fire, count).

    Resets on session change. Fires every `interval` calls.
    Stop hook uses its own interval (default 8); others use default 5.
    """
    interval = _stop_nudge_interval() if name == "stop" else _nudge_interval()
    count, stored_session = read_counter(name)

    # Reset on new session
    if session_id and session_id != stored_session:
        count = 0

    count += 1
    write_counter(name, count, session_id)

    return (count % interval == 0), count


def _pending_facts_count() -> int:
    """Count pending facts awaiting review in .pending-facts.md."""
    try:
        from keephive.storage import hive_dir

        path = hive_dir() / ".pending-facts.md"
        if path.exists():
            content = path.read_text().strip()
            if content:
                return sum(1 for ln in content.splitlines() if ln.strip().startswith("- "))
    except Exception:
        pass
    return 0


def _unreflected_log_count() -> int:
    """Count daily logs since last reflect run."""
    import re

    from keephive.storage import daily_dir, hive_dir

    last_reflect = hive_dir() / ".last-reflect-date"
    cutoff = ""
    if last_reflect.exists():
        cutoff = last_reflect.read_text().strip()
    count = 0
    dd = daily_dir()
    if dd.exists():
        date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for f in dd.glob("*.md"):
            if date_re.match(f.stem) and f.stem > cutoff:
                count += 1
    return count


# ---- Lifecycle-aware nudge state machine ----


def _lifecycle_nudge(
    context: str = "prompt",
    counter_name: str = "prompt",
    count: int = 0,
    session_id: str = "",
) -> str:
    """Return the most actionable nudge based on current hive state.

    Priority order (highest first):
    1. Specific open TODO not addressed in current session
    2. Stale facts needing verification
    3. Pending facts needing review
    4. Daily logs accumulated (7+) needing reflection
    5. Context-specific capture/recall reminder (NEVER gated)
    """
    try:
        from keephive.storage import count_stale_facts, kb_queue_depth, last_cmd_date, open_todos

        recency = read_recency(counter_name, session_id)

        # Priority 1: Specific TODO nudge
        todos = open_todos()
        if todos:
            cat = "todos"
            last = recency.get(cat, 0)
            if last == 0 or count - last >= _RECENCY_THRESHOLD:
                _, _, text = todos[0]
                short = text[:60] + "..." if len(text) > 60 else text
                result = f'Open TODO: "{short}" - resolved? `hive td`'
                record_surfaced(counter_name, cat, count, session_id)
                return result

        # Priority 1.5: KB queue — direct messages pending soul_update processing
        kb_depth = kb_queue_depth()
        if kb_depth > 0:
            cat = "kb_queue"
            last = recency.get(cat, 0)
            if last == 0 or count - last >= _KB_RECENCY_THRESHOLD:
                result = (
                    f"{kb_depth} direct KB message(s) queued. "
                    "soul_update will process on next compaction."
                )
                record_surfaced(counter_name, cat, count, session_id)
                return result

        # Priority 2: Stale facts
        stale = count_stale_facts()
        if stale > 0:
            cat = "stale"
            last = recency.get(cat, 0)
            if last == 0 or count - last >= _RECENCY_THRESHOLD:
                result = f"{stale} fact(s) unverified 30+ days. Run: hive v"
                record_surfaced(counter_name, cat, count, session_id)
                return result

        # Priority 3: Pending facts
        pending = _pending_facts_count()
        if pending > 0:
            cat = "pending"
            last = recency.get(cat, 0)
            if last == 0 or count - last >= _RECENCY_THRESHOLD:
                result = f"{pending} fact(s) pending review. Run: hive mem review"
                record_surfaced(counter_name, cat, count, session_id)
                return result

        # Priority 4: Daily logs need reflection
        log_count = _unreflected_log_count()
        if log_count >= 7:
            cat = "logs"
            last = recency.get(cat, 0)
            if last == 0 or count - last >= _RECENCY_THRESHOLD:
                result = f"{log_count} daily logs since last reflect. Run: hive rf"
                record_surfaced(counter_name, cat, count, session_id)
                return result

        # Priority 4.5: Review lag — no v/a/mem in >1 day
        # Only fires when there is stats history (fresh install = no lag to report)
        from datetime import date

        from keephive.storage import stats_file

        if stats_file().exists():
            today = date.today()
            review_dates = {
                "v": last_cmd_date("v"),
                "a": last_cmd_date("a"),
                "m": last_cmd_date("m"),
            }
            # Find worst offender (longest since last run)
            worst_days = 0
            for _last_date in review_dates.values():
                if _last_date is None:
                    days = 999  # never run = worst
                else:
                    days = (today - _last_date).days
                if days > worst_days:
                    worst_days = days

            if worst_days > 1:
                cat = "review_lag"
                last = recency.get(cat, 0)
                if last == 0 or count - last >= _REVIEW_RECENCY_THRESHOLD:
                    if worst_days >= 3:
                        result = f"{worst_days}d without review. Facts are drifting. hive v"
                    else:
                        result = f"No review in {worst_days}d. Try: hive v · hive a · hive mem review"
                    record_surfaced(counter_name, cat, count, session_id)
                    return result

    except Exception:
        pass

    # Priority 5: Context-specific capture/recall (NEVER gated)
    if context == "tool":
        return "Made changes worth remembering? hive_remember('DECISION: chose X because Y')"
    elif context == "stop":
        return "End of turn. Any decision or insight worth capturing? hive_remember()"
    else:
        return "Working on something new? hive_recall(topic) for previous context."


def get_prompt_nudge(count: int, session_id: str = "") -> str:
    """Get the nudge text for a UserPromptSubmit nudge."""
    return _lifecycle_nudge("prompt", "prompt", count, session_id)


def get_tool_nudge(count: int, session_id: str = "") -> str:
    """Get the nudge text for a PostToolUse nudge."""
    return _lifecycle_nudge("tool", "tool", count, session_id)


def get_stop_nudge(count: int, session_id: str = "") -> str:
    """Get the nudge text for a Stop hook nudge."""
    return _lifecycle_nudge("stop", "stop", count, session_id)


def _stop_nudge_interval() -> int:
    """Interval between stop nudges. Default 8, configurable via HIVE_STOP_NUDGE_INTERVAL."""
    return max(1, int(os.environ.get("HIVE_STOP_NUDGE_INTERVAL", "8")))


def build_nudge_output(context: str, event_name: str = "PostToolUse") -> str:
    """Return JSON string with additionalContext format."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }
    return json.dumps(output)
