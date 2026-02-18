"""Tests for tool flag passing to claude -p command building."""

from __future__ import annotations

import json

from keephive.claude import build_claude_command
from keephive.models import VerifyResponse


def test_tools_flag_passed_to_command():
    """--tools flag appears in the built command."""
    schema = json.dumps(VerifyResponse.model_json_schema())
    cmd = build_claude_command("test", schema, tools=["Read", "Grep"])

    assert "--tools" in cmd
    idx = cmd.index("--tools")
    assert cmd[idx + 1] == "Read,Grep"
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" in cmd


def test_max_turns_flag_passed():
    """--max-turns flag appears in the built command."""
    schema = json.dumps(VerifyResponse.model_json_schema())
    cmd = build_claude_command("test", schema, max_turns=5)

    assert "--max-turns" in cmd
    idx = cmd.index("--max-turns")
    assert cmd[idx + 1] == "5"


def test_no_tools_preserves_current_behavior():
    """Without tools param, no --tools flag (backward compat)."""
    schema = json.dumps(VerifyResponse.model_json_schema())
    cmd = build_claude_command("test", schema)

    assert "--tools" not in cmd
    assert "--strict-mcp-config" not in cmd
    assert "--max-turns" not in cmd


def test_prompt_is_last_argument():
    """Prompt text is the last element in the command."""
    schema = json.dumps(VerifyResponse.model_json_schema())
    cmd = build_claude_command("my test prompt", schema, model="sonnet")

    assert cmd[-1] == "my test prompt"


def test_model_flag_passed():
    """Model flag is set correctly."""
    schema = json.dumps(VerifyResponse.model_json_schema())
    cmd = build_claude_command("test", schema, model="sonnet")

    assert "--model" in cmd
    idx = cmd.index("--model")
    assert cmd[idx + 1] == "sonnet"
