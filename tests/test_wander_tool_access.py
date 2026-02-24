"""Tests for build_claude_command tool access flags (restrict_mcp / allowed_dirs).

These tests verify the Wander feature's architectural requirement: built-in tools
like WebSearch can be enabled without blocking all MCP servers.
"""

from __future__ import annotations


class TestBuildCommandToolAccess:
    def test_builtin_tools_no_mcp_restriction(self):
        """restrict_mcp=False omits --mcp-config even when tools are specified."""
        from keephive.llm.anthropic_cli import build_claude_command

        cmd = build_claude_command(
            "test prompt",
            "{}",
            tools=["WebSearch"],
            restrict_mcp=False,
        )

        assert "--mcp-config" not in cmd
        assert "--strict-mcp-config" not in cmd
        assert "--tools" in cmd
        assert "WebSearch" in cmd

    def test_existing_behavior_unchanged_with_tools(self):
        """restrict_mcp defaults True: --mcp-config is passed when tools are given."""
        from keephive.llm.anthropic_cli import build_claude_command

        cmd = build_claude_command(
            "test prompt",
            "{}",
            tools=["SomeMcpTool"],
        )

        assert "--mcp-config" in cmd
        assert "--strict-mcp-config" in cmd
        assert "--tools" in cmd

    def test_no_mcp_flag_without_tools(self):
        """Without tools, neither --mcp-config nor --tools appears."""
        from keephive.llm.anthropic_cli import build_claude_command

        cmd = build_claude_command("test prompt", "{}")

        assert "--mcp-config" not in cmd
        assert "--tools" not in cmd

    def test_allowed_dirs_adds_add_dir_flags(self):
        """allowed_dirs appends one --add-dir per directory."""
        from keephive.llm.anthropic_cli import build_claude_command

        cmd = build_claude_command(
            "test prompt",
            "{}",
            allowed_dirs=["/home/user/hive", "/tmp/scratch"],
        )

        assert "--add-dir" in cmd
        # Both dirs should appear
        idx = cmd.index("--add-dir")
        assert cmd[idx + 1] == "/home/user/hive"
        # Second --add-dir
        remaining = cmd[idx + 2 :]
        assert "--add-dir" in remaining
        assert "/tmp/scratch" in remaining

    def test_allowed_dirs_none_adds_no_flags(self):
        """allowed_dirs=None (default) does not add --add-dir flags."""
        from keephive.llm.anthropic_cli import build_claude_command

        cmd = build_claude_command("test prompt", "{}")

        assert "--add-dir" not in cmd

    def test_restrict_mcp_false_with_allowed_dirs(self):
        """Wander call pattern: tools + restrict_mcp=False + allowed_dirs all work together."""
        from keephive.llm.anthropic_cli import build_claude_command

        cmd = build_claude_command(
            "wander prompt",
            '{"type":"object"}',
            tools=["WebSearch"],
            max_turns=3,
            allowed_dirs=["/home/user/.claude/hive"],
            restrict_mcp=False,
        )

        # WebSearch tool enabled
        assert "--tools" in cmd
        assert "WebSearch" in cmd
        # No MCP restriction
        assert "--mcp-config" not in cmd
        # Hive dir accessible
        assert "--add-dir" in cmd
        assert "/home/user/.claude/hive" in cmd
        # Max turns set
        assert "--max-turns" in cmd

    def test_multiple_tools_joined_as_comma_list(self):
        """Multiple tools are joined with commas in a single --tools argument."""
        from keephive.llm.anthropic_cli import build_claude_command

        cmd = build_claude_command(
            "test prompt",
            "{}",
            tools=["WebSearch", "Read"],
            restrict_mcp=False,
        )

        idx = cmd.index("--tools")
        tools_arg = cmd[idx + 1]
        assert "WebSearch" in tools_arg
        assert "Read" in tools_arg
        assert "," in tools_arg
