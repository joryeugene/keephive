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
        "html": "<div class=\"todo-item\">Fix modal</div>",
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
    textarea_value = (
        "[UI Feedback]\nPage: http://localhost\nElement: .btn\n\nNote: make it orange"
    )
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
        json.dumps({
            "page": "http://localhost:3847",
            "selector": ".card",
            "note": "alignment is off",
        })
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
        json.dumps({
            "page": "http://localhost:3847/daily",
            "selector": ".log-entry",
            "note": "timestamp too dim",
        })
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
        json.dumps({"page": "http://localhost:3847", "selector": ".card", "note": "scoped feedback"})
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
    with patch("sys.stdout", StringIO()) as mock_stdout:
        _call_hook({"session_id": "s3", "cwd": "/Users/jory/myproject"})
        output = mock_stdout.getvalue()

    # The other-project queue must still exist (not consumed)
    assert other_queue.exists(), "Queue for a different project must not be consumed"
