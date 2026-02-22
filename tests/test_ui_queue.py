"""Tests for UI feedback queue: storage path, cmd_ui, and UserPromptSubmit injection."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

# ---- ui_queue_path ----


def test_ui_queue_path_under_hive_dir(hive_env):
    """ui_queue_path() returns .ui-queue inside HIVE_HOME."""
    from keephive.storage import ui_queue_path

    path = ui_queue_path()
    assert path.name == ".ui-queue"
    assert path.parent == hive_env


# ---- _format_ui_context ----


def test_format_ui_context_all_fields():
    """_format_ui_context formats all fields into a [UI Feedback] block."""
    from keephive.hooks.userpromptsubmit import _format_ui_context

    data = {
        "page": "http://localhost:3847/daily",
        "selector": ".todo-item",
        "html": '<div class="todo-item">Fix modal</div>',
        "styles": "padding: 8px",
        "note": "fix the color",
    }
    result = json.loads(_format_ui_context(data).strip())
    ctx = result["hookSpecificOutput"]["additionalContext"]

    assert "[UI Feedback" in ctx
    assert "http://localhost:3847/daily" in ctx
    assert ".todo-item" in ctx
    assert "Fix modal" in ctx
    assert "padding: 8px" in ctx
    assert "fix the color" in ctx
    assert "[/UI Feedback]" in ctx


def test_format_ui_context_extracts_note_after_marker():
    """_format_ui_context strips the textarea prefix and extracts only the user's note."""
    from keephive.hooks.userpromptsubmit import _format_ui_context

    # Bookmarklet pre-fills textarea with context header; user appends after "Note: "
    textarea_value = "[UI Feedback]\nPage: http://localhost\nElement: .btn\n\nNote: make it orange"
    data = {
        "page": "http://localhost",
        "selector": ".btn",
        "note": textarea_value,
    }
    result = json.loads(_format_ui_context(data).strip())
    ctx = result["hookSpecificOutput"]["additionalContext"]

    assert "make it orange" in ctx
    # The raw textarea prefix should not appear verbatim
    assert "[UI Feedback]\nPage:" not in ctx


def test_format_ui_context_omits_empty_fields():
    """_format_ui_context omits html/styles/note lines when those fields are empty."""
    from keephive.hooks.userpromptsubmit import _format_ui_context

    data = {"page": "http://localhost", "selector": "body"}
    result = json.loads(_format_ui_context(data).strip())
    ctx = result["hookSpecificOutput"]["additionalContext"]

    assert "HTML:" not in ctx
    assert "Styles:" not in ctx
    assert "Note:" not in ctx


def test_format_ui_context_returns_valid_json():
    """_format_ui_context always returns valid JSON followed by a newline."""
    from keephive.hooks.userpromptsubmit import _format_ui_context

    raw = _format_ui_context({"page": "x", "selector": "y"})
    assert raw.endswith("\n")
    obj = json.loads(raw.strip())
    assert "hookSpecificOutput" in obj
    assert "additionalContext" in obj["hookSpecificOutput"]


# ---- cmd_ui ----


def test_cmd_ui_no_queue(hive_env, capsys):
    """cmd_ui prints 'No pending' message when no queue file exists."""
    from keephive.commands.ui import cmd_ui

    cmd_ui([])
    out = capsys.readouterr().out
    assert "No pending" in out


def test_cmd_ui_shows_pending(hive_env, capsys):
    """cmd_ui shows queue contents when .ui-queue exists."""
    from keephive.commands.ui import cmd_ui
    from keephive.storage import ui_queue_path

    queue = ui_queue_path()
    queue.write_text(
        json.dumps(
            {
                "page": "http://localhost:3847",
                "selector": ".card",
                "note": "alignment is off",
            }
        )
    )

    cmd_ui([])
    out = capsys.readouterr().out
    assert "http://localhost:3847" in out
    assert ".card" in out
    assert "alignment is off" in out


def test_cmd_ui_clear_removes_file(hive_env, capsys):
    """cmd_ui_clear deletes the queue file."""
    from keephive.commands.ui import cmd_ui_clear
    from keephive.storage import ui_queue_path

    queue = ui_queue_path()
    queue.write_text(json.dumps({"page": "x", "selector": "y"}))
    assert queue.exists()

    cmd_ui_clear([])
    assert not queue.exists()
    out = capsys.readouterr().out
    assert "cleared" in out.lower()


def test_cmd_ui_clear_empty_is_graceful(hive_env, capsys):
    """cmd_ui_clear on an already-empty queue does not crash."""
    from keephive.commands.ui import cmd_ui_clear

    cmd_ui_clear([])
    out = capsys.readouterr().out
    assert "empty" in out.lower()


# ---- UserPromptSubmit hook: queue injection ----


def _call_hook(input_data):
    from keephive.hooks.userpromptsubmit import hook_userpromptsubmit

    stdin_text = json.dumps(input_data) if isinstance(input_data, dict) else input_data
    with patch("sys.stdin", StringIO(stdin_text)):
        hook_userpromptsubmit([])


def test_hook_injects_queue_to_stdout(hive_env, capsys):
    """When .ui-queue exists, hook writes [UI Feedback] JSON to stdout."""
    from keephive.storage import ui_queue_path

    queue = ui_queue_path()
    queue.write_text(
        json.dumps(
            {
                "page": "http://localhost:3847/daily",
                "selector": ".log-entry",
                "note": "timestamp too dim",
            }
        )
    )

    _call_hook({"session_id": "test-session-ui"})

    out = capsys.readouterr().out
    assert out.strip(), "Expected output but got empty stdout"
    obj = json.loads(out.strip())
    ctx = obj["hookSpecificOutput"]["additionalContext"]
    assert "localhost:3847/daily" in ctx
    assert ".log-entry" in ctx
    assert "timestamp too dim" in ctx


def test_hook_deletes_queue_after_injection(hive_env):
    """After injecting queue data, the hook deletes the queue file."""
    from keephive.storage import ui_queue_path

    queue = ui_queue_path()
    queue.write_text(json.dumps({"page": "http://x", "selector": ".y"}))

    with patch("sys.stdout", StringIO()):
        _call_hook({"session_id": "test-session-del"})

    assert not queue.exists(), "Queue file should be deleted after injection"


def test_hook_no_queue_does_not_crash(hive_env, capsys):
    """When no queue exists, hook proceeds without error."""
    from keephive.storage import ui_queue_path

    assert not ui_queue_path().exists()
    # Should not raise; nudge logic runs (or doesn't) silently
    _call_hook({"session_id": "test-session-noq"})
    # No assertion on output — nudge may or may not fire; we just verify no crash


def test_hook_queue_injection_skips_nudge(hive_env, capsys):
    """When queue is consumed, hook returns early and does not write nudge output."""
    from keephive.storage import ui_queue_path

    queue = ui_queue_path()
    queue.write_text(json.dumps({"page": "http://x", "selector": ".y"}))

    # Force nudge interval to 1 so it would normally fire
    with patch.dict("os.environ", {"HIVE_NUDGE_INTERVAL": "1"}):
        _call_hook({"session_id": "test-session-skip"})

    out = capsys.readouterr().out
    # Output should be exactly the queue JSON, not a nudge
    obj = json.loads(out.strip())
    # Queue output has hookSpecificOutput.additionalContext
    assert "additionalContext" in obj["hookSpecificOutput"]
    # Nudge output has hookSpecificOutput.suppressOutput or similar — distinct format
    # Most importantly: only ONE JSON object written (queue consumed, nudge skipped)
    assert out.count("{") == out.count("}")  # balanced JSON, not two concatenated objects


def test_hook_queue_still_tracks_event(hive_env, capsys):
    """UI queue consumption still records daily aggregate event tracking.

    Session-level prompt tracking (track_session_event) was removed because
    Claude Code session-meta is the source of truth for session analytics.
    But the daily aggregate track_event("hooks", "userpromptsubmit") must
    still fire even when the UI queue triggers an early return.
    """
    from keephive.storage import read_stats, ui_queue_path

    session_id = "test-session-queue-prompt"

    # Set up UI queue so the early return fires
    queue = ui_queue_path()
    queue.write_text(json.dumps({"page": "http://x", "selector": ".y"}))

    _call_hook({"session_id": session_id, "cwd": "/tmp/fake"})

    # Queue was consumed (early return happened)
    assert not queue.exists(), "Queue should have been consumed"

    # Daily aggregate event tracking must still fire
    stats = read_stats()
    today = list(stats.get("days", {}).keys())
    assert today, "Stats should have today's entry"
    hooks = stats["days"][today[0]].get("hooks", {})
    assert hooks.get("userpromptsubmit", 0) >= 1, (
        "Daily hook event tracking should fire even when UI queue triggers early return"
    )


# ---- Queue session scoping (Fix 5) ----


def test_ui_queue_path_with_project_name(hive_env):
    """ui_queue_path(project) returns a project-scoped filename."""
    from keephive.storage import ui_queue_path

    path = ui_queue_path("myproject")
    assert path.name == ".ui-queue-myproject"
    assert path.parent == hive_env


def test_ui_queue_path_no_project_returns_global(hive_env):
    """ui_queue_path() with no arg returns the legacy global .ui-queue."""
    from keephive.storage import ui_queue_path

    path = ui_queue_path()
    assert path.name == ".ui-queue"
    assert path.parent == hive_env


def test_ui_queue_path_none_returns_global(hive_env):
    """ui_queue_path(None) also returns the legacy global .ui-queue."""
    from keephive.storage import ui_queue_path

    path = ui_queue_path(None)
    assert path.name == ".ui-queue"


def test_hook_reads_project_scoped_queue(hive_env, capsys):
    """Hook reads .ui-queue-{project} when cwd is provided matching the project."""
    from keephive.storage import ui_queue_path

    # Write to project-scoped queue
    project_queue = ui_queue_path("keephive-serve")
    project_queue.write_text(
        json.dumps(
            {"page": "http://localhost:3847", "selector": ".card", "note": "scoped feedback"}
        )
    )

    _call_hook({"session_id": "s1", "cwd": "/Users/jory/Documents/GitHub/keephive-serve"})

    out = capsys.readouterr().out
    assert out.strip(), "Expected output from project-scoped queue"
    obj = json.loads(out.strip())
    ctx = obj["hookSpecificOutput"]["additionalContext"]
    assert "scoped feedback" in ctx
    assert not project_queue.exists(), "Project-scoped queue should be deleted after injection"


def test_hook_falls_back_to_global_queue(hive_env, capsys):
    """Hook falls back to global .ui-queue when no project-scoped queue exists."""
    from keephive.storage import ui_queue_path

    # Write to global queue only (no project-scoped one)
    global_queue = ui_queue_path()
    global_queue.write_text(
        json.dumps({"page": "http://localhost", "selector": "body", "note": "global fallback"})
    )

    _call_hook({"session_id": "s2", "cwd": "/Users/jory/Documents/GitHub/someproject"})

    out = capsys.readouterr().out
    assert out.strip()
    obj = json.loads(out.strip())
    ctx = obj["hookSpecificOutput"]["additionalContext"]
    assert "global fallback" in ctx


def test_hook_ignores_other_project_queue(hive_env, capsys):
    """Hook does not consume a queue for a different project."""
    from keephive.storage import ui_queue_path

    # Write to a different project's queue
    other_queue = ui_queue_path("other-project")
    other_queue.write_text(
        json.dumps({"page": "http://other", "selector": ".x", "note": "wrong project"})
    )

    # This hook call is for 'myproject' — should not consume 'other-project' queue
    # No global queue exists, no myproject queue exists — hook should produce no queue output
    with patch("sys.stdout", StringIO()):
        _call_hook({"session_id": "s3", "cwd": "/Users/jory/myproject"})

    # The other-project queue must still exist (not consumed)
    assert other_queue.exists(), "Queue for a different project must not be consumed"


# ---- drain_ui_queue: daily log persistence ----


def test_drain_ui_queue_writes_to_daily_log(hive_env):
    """drain_ui_queue persists feedback to daily log so it survives compaction."""
    from keephive.storage import daily_file, drain_ui_queue, ui_queue_path

    queue = ui_queue_path()
    queue.write_text(
        json.dumps(
            {
                "page": "http://localhost:3847/stats",
                "selector": ".gauge-row",
                "note": "gauge bars not visible in dark mode",
            }
        )
    )

    result = drain_ui_queue("")

    # Returns the additionalContext JSON string
    assert result is not None
    obj = json.loads(result.strip())
    assert "additionalContext" in obj["hookSpecificOutput"]
    assert "gauge bars not visible" in obj["hookSpecificOutput"]["additionalContext"]

    # Queue file is deleted
    assert not queue.exists()

    # Content is also in the daily log as a TODO
    log_text = daily_file().read_text()
    assert "gauge bars not visible" in log_text
    assert "TODO" in log_text


def test_drain_ui_queue_returns_none_when_empty(hive_env):
    """drain_ui_queue returns None when no queue file exists."""
    from keephive.storage import drain_ui_queue

    result = drain_ui_queue("/Users/jory/Documents/GitHub/keephive")
    assert result is None


# ---- Multi-item JSONL queue ----


def test_drain_ui_queue_multi_item_context_blocks(hive_env):
    """drain_ui_queue with two queued items produces two context blocks separated by blank line."""
    from keephive.storage import drain_ui_queue, ui_queue_path

    queue = ui_queue_path()
    item1 = json.dumps(
        {"page": "http://localhost:3847/stats", "selector": ".chart", "note": "chart too small"}
    )
    item2 = json.dumps(
        {"page": "http://localhost:3847/daily", "selector": ".entry", "note": "text too dim"}
    )
    queue.write_text(item1 + "\n" + item2 + "\n")

    result = drain_ui_queue("")

    assert result is not None
    obj = json.loads(result.strip())
    ctx = obj["hookSpecificOutput"]["additionalContext"]

    # Both items must appear in the context
    assert "chart too small" in ctx
    assert "text too dim" in ctx
    assert ".chart" in ctx
    assert ".entry" in ctx

    # The two blocks must be separated
    assert "[/UI Feedback]" in ctx
    assert ctx.count("[UI Feedback") == 2

    # Queue deleted after drain
    assert not queue.exists()


def test_drain_ui_queue_multi_item_daily_log(hive_env):
    """drain_ui_queue with two items writes a TODO log entry for each."""
    from keephive.storage import daily_file, drain_ui_queue, ui_queue_path

    queue = ui_queue_path()
    item1 = json.dumps({"page": "http://localhost/a", "selector": ".foo", "note": "first note"})
    item2 = json.dumps({"page": "http://localhost/b", "selector": ".bar", "note": "second note"})
    queue.write_text(item1 + "\n" + item2 + "\n")

    drain_ui_queue("")

    log_text = daily_file().read_text()
    assert "first note" in log_text
    assert "second note" in log_text
    # Each item gets its own TODO line
    todo_lines = [ln for ln in log_text.splitlines() if "TODO" in ln and "UI Feedback" in ln]
    assert len(todo_lines) == 2


def test_cmd_ui_shows_multiple_items(hive_env, capsys):
    """cmd_ui displays a count header and all items when queue has more than one entry."""
    from keephive.commands.ui import cmd_ui
    from keephive.storage import ui_queue_path

    queue = ui_queue_path()
    item1 = json.dumps(
        {"page": "http://localhost/x", "selector": ".alpha", "note": "first feedback"}
    )
    item2 = json.dumps(
        {"page": "http://localhost/y", "selector": ".beta", "note": "second feedback"}
    )
    queue.write_text(item1 + "\n" + item2 + "\n")

    cmd_ui([])
    out = capsys.readouterr().out

    assert "2 items" in out
    assert "http://localhost/x" in out
    assert ".alpha" in out
    assert "first feedback" in out
    assert "http://localhost/y" in out
    assert ".beta" in out
    assert "second feedback" in out


def test_hook_injects_all_items_from_multi_item_queue(hive_env, capsys):
    """Hook injects all queued items when multiple submissions exist before a prompt."""
    from keephive.storage import ui_queue_path

    queue = ui_queue_path()
    item1 = json.dumps({"page": "http://localhost/p1", "selector": ".s1", "note": "note one"})
    item2 = json.dumps({"page": "http://localhost/p2", "selector": ".s2", "note": "note two"})
    queue.write_text(item1 + "\n" + item2 + "\n")

    _call_hook({"session_id": "test-multi-queue"})

    out = capsys.readouterr().out
    assert out.strip(), "Expected output from multi-item queue"
    obj = json.loads(out.strip())
    ctx = obj["hookSpecificOutput"]["additionalContext"]
    assert "note one" in ctx
    assert "note two" in ctx
    # Queue consumed
    assert not queue.exists()


def test_drain_ui_queue_skips_malformed_lines(hive_env):
    """drain_ui_queue silently skips lines that are not valid JSON."""
    from keephive.storage import drain_ui_queue, ui_queue_path

    queue = ui_queue_path()
    good = json.dumps({"page": "http://localhost", "selector": ".ok", "note": "valid item"})
    queue.write_text("NOT_JSON\n" + good + "\n{bad: json}\n")

    result = drain_ui_queue("")

    assert result is not None
    obj = json.loads(result.strip())
    ctx = obj["hookSpecificOutput"]["additionalContext"]
    assert "valid item" in ctx
    # Only one context block (the malformed lines were skipped)
    assert ctx.count("[UI Feedback") == 1
