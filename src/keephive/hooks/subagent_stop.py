"""SubagentStop hook handler.

Called by Claude Code when a Task-spawned subagent completes.
Logs a completion breadcrumb to the daily log and tracks the event.
No stdout output. Non-blocking.
"""

from __future__ import annotations

import json
import sys

from keephive.clock import get_now


def hook_subagent_stop(_args: list[str]) -> None:
    """Main entry point for SubagentStop hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        return

    # Track hook event
    try:
        from keephive.storage import track_event

        track_event("hooks", "subagent_stop", source="hook")
    except Exception:
        pass

    # Extract any available description from the payload.
    # Exact field names are not documented; try common candidates defensively.
    desc = (
        input_data.get("task_subject")
        or input_data.get("description")
        or input_data.get("subagent_name")
        or ""
    )
    desc = desc.replace("\n", " ").replace("\r", " ").strip()

    # Log subagent completion to daily log as a breadcrumb
    try:
        from keephive.storage import append_to_daily

        ts = get_now().strftime("%H:%M:%S")
        if desc:
            append_to_daily(f"- [{ts}] SUBAGENT-DONE: {desc}")
        else:
            append_to_daily(f"- [{ts}] SUBAGENT-DONE: subagent task completed")
    except Exception:
        pass
