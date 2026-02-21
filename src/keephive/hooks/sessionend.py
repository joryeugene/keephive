"""SessionEnd hook handler.

Called by Claude Code when a session terminates.
Finalizes session stats with an accurate end timestamp.
No stdout output. Pure stats finalization.
"""

from __future__ import annotations

import json
import sys


def hook_sessionend(_args: list[str]) -> None:
    """Main entry point for SessionEnd hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        return

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    reason = input_data.get("reason", "")

    # Track hook event
    try:
        from keephive.storage import track_event

        track_event("hooks", "sessionend", source="hook")
    except Exception:
        pass

    # Finalize session with accurate end timestamp
    try:
        from keephive.storage import track_session_event

        track_session_event(session_id, "end", reason=reason)
    except Exception:
        pass
