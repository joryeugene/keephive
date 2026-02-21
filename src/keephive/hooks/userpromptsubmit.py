"""UserPromptSubmit hook handler.

Called by Claude Code when the user submits a prompt.
Periodic nudge to use hive tools (counter-based, every N prompts).
"""

from __future__ import annotations

import json
import sys

from keephive.clock import get_now


def _format_ui_context(data: dict) -> str:
    """Format UI feedback queue data as a context block for Claude."""
    page = data.get("page", "?")
    selector = data.get("selector", "?")
    html_snippet = data.get("html", "")[:400]
    styles = data.get("styles", "")
    note = data.get("note", "")
    # Extract the user's actual note (after "Note: " in the textarea)
    if "\n\nNote: " in note:
        note = note.split("\n\nNote: ")[-1].strip()

    lines = [f"[UI Feedback — {page}]"]
    lines.append(f"Page: {page}")
    lines.append(f"Element: {selector}")
    if html_snippet:
        lines.append(f"HTML: {html_snippet}")
    if styles:
        lines.append(f"Styles:\n{styles}")
    if note:
        lines.append(f"Note: {note}")
    lines.append("[/UI Feedback]")

    output = json.dumps(
        {
            "hookSpecificOutput": {
                "additionalContext": "\n".join(lines),
            }
        }
    )
    return output + "\n"


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

    # Track usage (before UI queue check — prompt happened regardless)
    try:
        from keephive.storage import track_event

        track_event("hooks", "userpromptsubmit", source="hook")
    except Exception:
        pass

    # Track session prompt (before UI queue check — prompt happened regardless)
    try:
        from keephive.storage import track_session_event

        cwd = input_data.get("cwd", "")
        track_session_event(session_id, "prompt", project=cwd)
    except Exception:
        pass

    # Check UI feedback queue — inject before nudge
    try:
        from pathlib import Path

        from keephive.storage import ui_queue_path

        cwd = input_data.get("cwd", "")
        project_name = Path(cwd).name if cwd else ""
        queue = ui_queue_path(project_name) if project_name else ui_queue_path()
        if not queue.exists() and project_name:
            queue = ui_queue_path()  # fall back to legacy global queue
        if queue.exists():
            data = json.loads(queue.read_text())
            context = _format_ui_context(data)
            sys.stdout.write(context)
            queue.unlink()
            return  # Queue consumed; skip nudge this turn
    except Exception:
        pass  # Never block the prompt

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
                f.write(f"[{get_now().isoformat()}] userpromptsubmit error: {e}\n")
        except Exception:
            pass
