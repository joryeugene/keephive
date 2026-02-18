"""UserPromptSubmit hook handler.

Called by Claude Code when the user submits a prompt.
Periodic nudge to use hive tools (counter-based, every N prompts).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime


def hook_userpromptsubmit(args: list[str]) -> None:
    """Main entry point for UserPromptSubmit hook."""
    try:
        raw = sys.stdin.read()
        input_data = json.loads(raw)
    except Exception:
        return  # Silent exit on bad input

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    # Track usage
    try:
        from keephive.storage import track_event
        track_event("hooks", "userpromptsubmit", source="hook")
    except Exception:
        pass

    try:
        from keephive.nudge import build_nudge_output, get_prompt_nudge, should_nudge

        fire, count = should_nudge("prompt", session_id)
        if fire:
            nudge_text = get_prompt_nudge(count)
            output = build_nudge_output(nudge_text, event_name="UserPromptSubmit")
            sys.stdout.write(output)
    except Exception as e:
        # Never block the user's prompt
        try:
            from keephive.storage import hive_dir

            debug_log = hive_dir() / ".hook-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{datetime.now().isoformat()}] userpromptsubmit error: {e}\n")
        except Exception:
            pass
