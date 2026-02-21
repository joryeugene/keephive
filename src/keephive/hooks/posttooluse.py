"""PostToolUse hook handler.

Called by Claude Code after Edit or Write tool use.
Periodic nudge to record decisions and check for patterns (counter-based).
"""

from __future__ import annotations

import json
import sys

from keephive.clock import get_now


def hook_posttooluse(_args: list[str]) -> None:
    """Main entry point for PostToolUse hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        return

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    # Track usage
    try:
        from keephive.storage import track_event

        track_event("hooks", "posttooluse", source="hook")
    except Exception:
        pass

    # Session tool counting removed: Claude Code session-meta provides
    # full tool_counts for ALL tools. PostToolUse only fires for Edit|Write,
    # giving at most 2 tool types (of ~15 available).

    try:
        from keephive.nudge import build_nudge_output, get_tool_nudge, should_nudge

        fire, count = should_nudge("tool", session_id)
        if fire:
            nudge_text = get_tool_nudge(count)
            output = build_nudge_output(nudge_text, event_name="PostToolUse")
            sys.stdout.write(output)
    except Exception as e:
        try:
            from keephive.storage import hive_dir

            debug_log = hive_dir() / ".hook-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{get_now().isoformat(timespec='seconds')}] posttooluse error: {e}\n")
        except Exception:
            pass
