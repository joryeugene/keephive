"""SubagentStop hook handler.

Called by Claude Code when a Task-spawned subagent completes.
Logs a completion breadcrumb to the daily log and tracks the event.
No stdout output. Non-blocking.
"""

from __future__ import annotations

import json
import sys

from keephive.clock import get_now


def _extract_output(description: str) -> "SubagentExtractionResponse | None":
    """Micro-extraction via haiku. Returns None on any error (silence gate)."""
    try:
        from keephive.claude import run_claude_pipe
        from keephive.models import SubagentExtractionResponse

        prompt = f"""Task just completed: {description}

What was the key output or decision? One sentence max. If unclear from the task title alone, return empty strings.

Constraints:
- No narration of the task process
- No "The task was" or "The agent" openers
- Empty strings are valid and preferred over guessing
- captured: the concrete finding/output (or "")
- decision: the most important choice made (or "")"""

        return run_claude_pipe(prompt, SubagentExtractionResponse, model="haiku", timeout=30)
    except Exception:
        return None


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

        extraction = _extract_output(desc) if len(desc) > 20 else None

        ts = get_now().strftime("%H:%M:%S")
        if extraction and extraction.captured:
            log_line = f"- [{ts}] SUBAGENT-INSIGHT: {desc} -> {extraction.captured}"
            if extraction.decision:
                log_line += f" (decision: {extraction.decision})"
            append_to_daily(log_line)
        elif desc:
            append_to_daily(f"- [{ts}] SUBAGENT-DONE: {desc}")
        else:
            append_to_daily(f"- [{ts}] SUBAGENT-DONE: subagent task completed")
    except Exception:
        pass
