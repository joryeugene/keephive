"""Shared nudge infrastructure for mid-session hooks.

Counter-based system that fires periodic reminders to use hive tools.
Uses a priority-based state machine instead of static rotations:
highest-priority actionable state always wins.

Used by UserPromptSubmit, PostToolUse, and Stop hooks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _counter_path(name: str) -> Path:
    """Path to counter file: ~/.claude/hive/.{name}-counter."""
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


def _lifecycle_nudge(context: str = "prompt") -> str:
    """Return the most actionable nudge based on current hive state.

    Priority order (highest first):
    1. Specific open TODO not addressed in current session
    2. Stale facts needing verification
    3. Pending facts needing review
    4. Daily logs accumulated (7+) needing reflection
    5. Context-specific capture/recall reminder
    """
    try:
        from keephive.storage import count_stale_facts, open_todos

        # Priority 1: Specific TODO nudge
        todos = open_todos()
        if todos:
            _, _, text = todos[0]
            short = text[:60] + "..." if len(text) > 60 else text
            return f'Open TODO: "{short}" - resolved? `hive td`'

        # Priority 2: Stale facts
        stale = count_stale_facts()
        if stale > 0:
            return f"{stale} fact(s) unverified 30+ days. Run: hive v"

        # Priority 3: Pending facts
        pending = _pending_facts_count()
        if pending > 0:
            return f"{pending} fact(s) pending review. Run: hive mem review"

        # Priority 4: Daily logs need reflection
        log_count = _unreflected_log_count()
        if log_count >= 7:
            return f"{log_count} daily logs since last reflect. Run: hive rf"

    except Exception:
        pass

    # Priority 5: Context-specific capture/recall
    if context == "tool":
        return "Made changes worth remembering? hive_remember('DECISION: chose X because Y')"
    elif context == "stop":
        return "End of turn. Any decision or insight worth capturing? hive_remember()"
    else:
        return "Working on something new? hive_recall(topic) for previous context."


def get_prompt_nudge(count: int) -> str:
    """Get the nudge text for a UserPromptSubmit nudge."""
    return _lifecycle_nudge("prompt")


def get_tool_nudge(count: int) -> str:
    """Get the nudge text for a PostToolUse nudge."""
    return _lifecycle_nudge("tool")


def get_stop_nudge(count: int) -> str:
    """Get the nudge text for a Stop hook nudge."""
    return _lifecycle_nudge("stop")


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
