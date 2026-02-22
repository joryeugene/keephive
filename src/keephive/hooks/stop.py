"""Stop hook handler.

Called by Claude Code when the agent's turn ends.
Increments a turn counter per session and fires a periodic micro-nudge
to encourage capturing wrap-up items (TODOs, decisions, facts).
"""

from __future__ import annotations

import json
import sys

from keephive.clock import get_now


def hook_stop(_args: list[str]) -> None:
    """Main entry point for Stop hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        return

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    # Track hook event
    try:
        from keephive.storage import track_event

        track_event("hooks", "stop", source="hook")
    except Exception:
        pass

    # Increment turn counter for this session
    try:
        from keephive.storage import track_session_event

        track_session_event(session_id, "stop")
    except Exception:
        pass

    # Check UI feedback queue — inject at end of every turn, persist to daily log
    try:
        from keephive.storage import drain_ui_queue

        result = drain_ui_queue(input_data.get("cwd", ""))
        if result:
            sys.stdout.write(result)
            return  # Queue consumed; skip nudge this turn
    except Exception:
        pass  # Never block the turn

    # Periodic nudge (every 12 turns by default)
    try:
        from keephive.nudge import build_nudge_output, get_stop_nudge, should_nudge

        fire, count = should_nudge("stop", session_id)
        if fire:
            nudge_text = get_stop_nudge(count)
            output = build_nudge_output(nudge_text, event_name="Stop")
            sys.stdout.write(output)
    except Exception as e:
        try:
            from keephive.storage import hive_dir

            debug_log = hive_dir() / ".hook-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{get_now().isoformat(timespec='seconds')}] stop error: {e}\n")
        except Exception:
            pass
