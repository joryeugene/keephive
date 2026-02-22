"""TaskCompleted hook handler.

Called by Claude Code when a task is marked done.
Auto-logs a DONE entry to the daily log and tracks task completion stats.
No stdout output. Non-blocking.
"""

from __future__ import annotations

import json
import sys

from keephive.clock import get_now


def hook_taskcompleted(_args: list[str]) -> None:
    """Main entry point for TaskCompleted hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        return

    task_subject = input_data.get("task_subject", "")

    # Track hook event
    try:
        from keephive.storage import track_event

        track_event("hooks", "taskcompleted", source="hook")
        track_event("meta", "tasks_completed", source="hook")
    except Exception:
        pass

    # Sanitize task_subject before writing to daily log
    task_subject = task_subject.replace("\n", " ").replace("\r", " ").strip()

    # Auto-log DONE entry to daily log
    if task_subject:
        try:
            from keephive.storage import append_to_daily

            timestamp = get_now().strftime("%H:%M:%S")
            append_to_daily(f"- [{timestamp}] DONE: {task_subject}")
        except Exception:
            pass
