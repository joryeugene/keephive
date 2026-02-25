"""UserPromptSubmit hook handler.

Called by Claude Code when the user submits a prompt.
Periodic nudge to use hive tools (counter-based, every N prompts).

KB detection: if prompt contains "KingBee", "King B", "King Bee", "king-bee",
or "@KB"/"@kb", the message is queued in .kb-queue.md for soul_update to process,
and a [DIRECT MESSAGE TO KINGBEE] context block is injected (replaces nudge).
"""

from __future__ import annotations

import json
import re
import sys

from keephive.clock import get_now

# Matches: KingBee, King B, King Bee, king-bee, king_bee (case-insensitive)
# Also matches @KB / @kb (requires @ prefix to avoid bare "KB" false positives)
_KB_PATTERN = re.compile(
    r"(?i)\b(king[\s_-]*b(?:ee)?)\b"  # KingBee / King B / King Bee / king-bee
    r"|(?<!\w)@kb(?!\w)",              # @KB / @kb — requires @ prefix
    re.IGNORECASE,
)


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

    prompt_text = input_data.get("prompt", "").strip()

    # Track usage (before UI queue check — prompt happened regardless)
    try:
        from keephive.storage import track_event

        track_event("hooks", "userpromptsubmit", source="hook")
    except Exception:
        pass

    # Session prompt counting removed: Claude Code session-meta provides
    # accurate user_message_count. Hook invocations overcount (~71x) due to
    # sub-agent spawns and tool continuations.

    # Log slash commands to daily log as context breadcrumbs.
    # Captures /clear, /compact, /new, etc. before Claude processes them.
    if prompt_text.startswith("/"):
        try:
            from keephive.storage import append_to_daily

            ts = get_now().strftime("%H:%M:%S")
            append_to_daily(f"- [{ts}] SLASH: {prompt_text[:80]}")
        except Exception:
            pass

    # KB detection — queue direct messages and inject context block
    if prompt_text and _KB_PATTERN.search(prompt_text):
        try:
            from keephive.nudge import build_nudge_output
            from keephive.storage import append_kb_message, kb_queue_depth

            append_kb_message(prompt_text)
            depth = kb_queue_depth()
            kb_context = (
                "[DIRECT MESSAGE TO KINGBEE]\n"
                "You are being addressed as KingBee directly. Respond in character.\n"
                f"This message has been queued for soul_update to process ({depth} pending).\n"
                "soul_update will incorporate any behavioral directives into SOUL.md on next run."
            )
            output = build_nudge_output(kb_context, event_name="UserPromptSubmit")
            sys.stdout.write(output)
            sys.stdout.flush()
            return  # KB message handled; skip nudge this turn
        except Exception:
            pass  # Never block the prompt

    # Check UI feedback queue — inject before nudge, persist to daily log
    try:
        from keephive.storage import drain_ui_queue

        result = drain_ui_queue(input_data.get("cwd", ""), event_name="UserPromptSubmit")
        if result:
            sys.stdout.write(result)
            sys.stdout.flush()
            return  # Queue consumed; skip nudge this turn
    except Exception:
        pass  # Never block the prompt

    try:
        from keephive.nudge import build_nudge_output, get_prompt_nudge, should_nudge

        fire, count = should_nudge("prompt", session_id)
        if fire:
            nudge_text = get_prompt_nudge(count, session_id=session_id)
            output = build_nudge_output(nudge_text, event_name="UserPromptSubmit")
            sys.stdout.write(output)
            sys.stdout.flush()
    except Exception as e:
        # Never block the user's prompt
        try:
            from keephive.storage import hive_dir

            debug_log = hive_dir() / ".hook-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{get_now().isoformat()}] userpromptsubmit error: {e}\n")
        except Exception:
            pass
