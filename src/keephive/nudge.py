"""Shared nudge infrastructure for mid-session hooks.

Counter-based system that fires periodic reminders to use hive tools.
Used by both UserPromptSubmit and PostToolUse hooks.
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
    """Interval between nudges. Default 8, configurable via HIVE_NUDGE_INTERVAL."""
    return int(os.environ.get("HIVE_NUDGE_INTERVAL", "8"))


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
    """
    interval = _nudge_interval()
    count, stored_session = read_counter(name)

    # Reset on new session
    if session_id and session_id != stored_session:
        count = 0

    count += 1
    write_counter(name, count, session_id)

    return (count % interval == 0), count


def _status_nudge() -> str | None:
    """Build a status-aware nudge if there's something actionable."""
    try:
        from keephive.storage import count_stale_facts, open_todos

        parts: list[str] = []

        stale = count_stale_facts()
        if stale > 0:
            parts.append(f"{stale} stale fact(s) need verification.")

        todos = open_todos()
        overdue = [t for d, _, t in todos if d < _today_str()]
        if overdue:
            parts.append(f"{len(overdue)} overdue TODO(s).")

        if parts:
            return " ".join(parts)
    except Exception:
        pass
    return None


def _today_str() -> str:
    from keephive.clock import get_today

    return get_today().isoformat()


# ---- Nudge rotations ----

_PROMPT_NUDGES = [
    "Recording insights? hive_remember() for facts, decisions, corrections. Searching context? hive_recall(topic).",
    None,  # Placeholder for status-aware nudge (falls back to index 0)
    "Before writing new code, check hive_recall(topic) for existing patterns.",
]

_TOOL_NUDGES = [
    "Made code changes. Record the reasoning: hive_remember('DECISION: chose X because Y')",
    "Before writing new utilities, check hive_recall(concept). Consolidate, don't duplicate.",
    None,  # Placeholder for status-aware nudge (falls back to index 0)
]


def get_prompt_nudge(count: int) -> str:
    """Get the nudge text for a UserPromptSubmit nudge."""
    idx = (count // _nudge_interval()) % len(_PROMPT_NUDGES)
    nudge = _PROMPT_NUDGES[idx]
    if nudge is None:
        # Status-aware, fall back to first nudge
        nudge = _status_nudge() or _PROMPT_NUDGES[0]
    return nudge


def get_tool_nudge(count: int) -> str:
    """Get the nudge text for a PostToolUse nudge."""
    idx = (count // _nudge_interval()) % len(_TOOL_NUDGES)
    nudge = _TOOL_NUDGES[idx]
    if nudge is None:
        # Status-aware, fall back to first nudge
        nudge = _status_nudge() or _TOOL_NUDGES[0]
    return nudge


def build_nudge_output(context: str, event_name: str = "PostToolUse") -> str:
    """Return JSON string with additionalContext format."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }
    return json.dumps(output)
