"""Test LLM integration: JSON parsing, routing, and error handling.

This validates that keephive.claude handles:
1. parse_claude_response: the response format from claude -p
2. run_claude_pipe routing: inside CC + API key -> API, inside CC only -> fail fast, terminal/hooks -> subprocess (always)
3. Error paths for all tiers
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from keephive.claude import ClaudePipeError, build_claude_env, parse_claude_response
from keephive.models import PreCompactResponse, VerifyResponse


class TestParseClaudeResponse:
    """Test the centralized JSON parser for claude -p output."""

    def test_array_with_structured_output(self):
        """Real production format: system init + result with structured_output."""
        raw = json.dumps(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "cwd": "/Users/test/.claude",
                    "tools": ["Task", "Bash", "Read", "Write"],
                },
                {
                    "type": "result",
                    "structured_output": {
                        "verdicts": [
                            {"index": 1, "verdict": "VALID", "reason": "OK"},
                        ]
                    },
                },
            ]
        )

        resp = parse_claude_response(raw, VerifyResponse)
        assert len(resp.verdicts) == 1
        assert resp.verdicts[0].verdict.value == "VALID"

    def test_array_with_system_messages(self):
        """The format that broke production: system init with massive tools array + result."""
        raw = json.dumps(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "cwd": "/home/dev/.claude",
                    "tools": [
                        "Task",
                        "TaskOutput",
                        "Bash",
                        "Glob",
                        "Grep",
                        "Read",
                        "Edit",
                        "Write",
                        "NotebookEdit",
                        "WebFetch",
                        "WebSearch",
                        "AskUserQuestion",
                    ],
                },
                {
                    "type": "result",
                    "structured_output": {
                        "verdicts": [
                            {
                                "index": 1,
                                "verdict": "STALE",
                                "reason": "Version changed",
                                "correction": "Python 3.13 now installed",
                            },
                        ]
                    },
                },
            ]
        )

        resp = parse_claude_response(raw, VerifyResponse)
        assert resp.verdicts[0].verdict.value == "STALE"
        assert resp.verdicts[0].correction == "Python 3.13 now installed"

    def test_array_system_only_raises(self):
        """System init messages but no result element should raise."""
        raw = json.dumps(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "cwd": "/Users/test",
                    "tools": ["Task", "Bash"],
                },
                {
                    "type": "system",
                    "subtype": "tool_list",
                    "tools": ["Read", "Write"],
                },
            ]
        )

        with pytest.raises(ClaudePipeError, match="no result element"):
            parse_claude_response(raw, VerifyResponse)

    def test_array_result_not_last(self):
        """Result element before other messages should still be found."""
        raw = json.dumps(
            [
                {
                    "type": "result",
                    "structured_output": {
                        "insights": [
                            {"category": "FACT", "description": "Something learned"},
                        ]
                    },
                },
                {
                    "type": "system",
                    "subtype": "usage",
                    "tokens": 1500,
                },
            ]
        )

        resp = parse_claude_response(raw, PreCompactResponse)
        assert len(resp.insights) == 1
        assert resp.insights[0].category.value == "FACT"

    def test_array_multiple_system_messages(self):
        """Multiple system messages surrounding the result."""
        raw = json.dumps(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "cwd": "/Users/test",
                    "tools": ["Task"],
                },
                {
                    "type": "system",
                    "subtype": "config",
                    "model": "haiku",
                },
                {"type": "result", "structured_output": {"verdicts": []}},
                {
                    "type": "system",
                    "subtype": "usage",
                    "tokens": 500,
                },
            ]
        )

        resp = parse_claude_response(raw, VerifyResponse)
        assert resp.verdicts == []

    def test_direct_object(self):
        """Direct object without array wrapping."""
        raw = json.dumps(
            {
                "verdicts": [
                    {"index": 1, "verdict": "STALE", "reason": "Old", "correction": "New"},
                ]
            }
        )

        resp = parse_claude_response(raw, VerifyResponse)
        assert resp.verdicts[0].verdict.value == "STALE"
        assert resp.verdicts[0].correction == "New"

    def test_object_with_structured_output(self):
        """Object format with structured_output key."""
        raw = json.dumps(
            {
                "structured_output": {
                    "insights": [
                        {"category": "FACT", "description": "Something learned"},
                    ]
                }
            }
        )

        resp = parse_claude_response(raw, PreCompactResponse)
        assert len(resp.insights) == 1

    def test_empty_array_raises(self):
        """Empty array should raise ClaudePipeError."""
        with pytest.raises(ClaudePipeError, match="empty array"):
            parse_claude_response("[]", VerifyResponse)

    def test_invalid_json_raises(self):
        """Invalid JSON should raise ClaudePipeError."""
        with pytest.raises(ClaudePipeError, match="invalid JSON"):
            parse_claude_response("not json at all", VerifyResponse)

    def test_validation_error_raises(self):
        """Response that doesn't match the model should raise ClaudePipeError."""
        raw = json.dumps(
            {
                "verdicts": [
                    {"index": 1, "verdict": "TOTALLY_INVALID", "reason": "Bad"},
                ]
            }
        )

        with pytest.raises(ClaudePipeError, match="validation failed"):
            parse_claude_response(raw, VerifyResponse)

    def test_empty_output_raises(self):
        """Empty stdout should raise ClaudePipeError."""
        with pytest.raises(ClaudePipeError, match="empty output"):
            parse_claude_response("", VerifyResponse)

    def test_claudecode_env_cleared(self):
        """CLAUDECODE env var should be cleared by build_claude_env."""
        os.environ["CLAUDECODE"] = "1"
        try:
            env = build_claude_env()
            assert "CLAUDECODE" not in env
        finally:
            del os.environ["CLAUDECODE"]

    def test_claude_code_entrypoint_env_cleared(self):
        """CLAUDE_CODE_ENTRYPOINT env var should be cleared by build_claude_env."""
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        try:
            env = build_claude_env()
            assert "CLAUDE_CODE_ENTRYPOINT" not in env
        finally:
            del os.environ["CLAUDE_CODE_ENTRYPOINT"]

    def test_build_claude_env_strips_both_blocking_vars(self):
        """Both CLAUDECODE and CLAUDE_CODE_ENTRYPOINT stripped simultaneously."""
        os.environ["CLAUDECODE"] = "1"
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        try:
            env = build_claude_env()
            assert "CLAUDECODE" not in env
            assert "CLAUDE_CODE_ENTRYPOINT" not in env
            # Other env vars pass through
            assert "PATH" in env
        finally:
            del os.environ["CLAUDECODE"]
            del os.environ["CLAUDE_CODE_ENTRYPOINT"]

    def test_whitespace_only_output_raises(self):
        """Whitespace-only output should raise ClaudePipeError."""
        with pytest.raises(ClaudePipeError, match="empty output"):
            parse_claude_response("   \n  \t  ", VerifyResponse)

    def test_fallback_extracts_non_verify_model(self):
        """Fallback text extraction works for models other than VerifyResponse."""
        raw = json.dumps(
            [
                {"type": "system", "subtype": "init", "cwd": "/test", "tools": []},
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"patterns": [{"topic": "testing", "days": 3, "has_guide": false}], "additions": [], "contradictions": [], "actions": []}',
                        }
                    ],
                },
                {"type": "result", "structured_output": None},
            ]
        )

        from keephive.models import ReflectAnalyzeResponse

        resp = parse_claude_response(raw, ReflectAnalyzeResponse)
        assert len(resp.patterns) == 1
        assert resp.patterns[0].topic == "testing"

    def test_stderr_on_timeout(self, monkeypatch, capsys):
        """Verify stderr message when TimeoutExpired fires."""
        import subprocess

        # Force subprocess path (clear env vars that would short-circuit)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)

        # Ensure anthropic_cli backend is selected even when claude CLI is not on PATH
        monkeypatch.setattr("keephive.llm.anthropic_cli._detect", lambda: (True, "mocked for test"))

        def fake_run(*_a, **_kw):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=30)

        monkeypatch.setattr("subprocess.run", fake_run)

        from keephive.claude import run_claude_pipe

        with pytest.raises(ClaudePipeError, match="timed out"):
            run_claude_pipe("test prompt", VerifyResponse, timeout=30)

        err = capsys.readouterr().err
        assert "[keephive]" in err
        assert "timed out" in err

    def test_stderr_on_nonzero_exit(self, monkeypatch, capsys):
        """Verify stderr message on non-zero exit code.

        Non-zero exit is retriable (transient auth/API failures may warrant fallback),
        so we remove all API keys to ensure all backends fail and ClaudePipeError
        propagates. The [keephive] stderr line from anthropic_cli is still printed
        before any retry attempt.
        """
        import subprocess

        # Remove all API keys so every backend fails and the error surfaces.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        # Ensure anthropic_cli backend is selected even when claude CLI is not on PATH
        monkeypatch.setattr("keephive.llm.anthropic_cli._detect", lambda: (True, "mocked for test"))

        def fake_run(*_a, **_kw):
            return subprocess.CompletedProcess(
                args=["claude"], returncode=1, stdout="", stderr="some error from claude"
            )

        monkeypatch.setattr("subprocess.run", fake_run)

        from keephive.claude import run_claude_pipe

        with pytest.raises(ClaudePipeError):
            run_claude_pipe("test prompt", VerifyResponse)

        err = capsys.readouterr().err
        assert "[keephive]" in err
        assert "exited with code 1" in err

    def test_stderr_on_empty_output(self, capsys):
        """Verify stderr message when output is empty."""
        with pytest.raises(ClaudePipeError, match="empty output"):
            parse_claude_response("", VerifyResponse)

        err = capsys.readouterr().err
        assert "[keephive]" in err
        assert "empty output" in err

    def test_error_max_turns_raises_clear_message(self):
        """error_max_turns subtype produces actionable ClaudePipeError, not validation error."""
        raw = json.dumps(
            [
                {"type": "system", "subtype": "init", "cwd": "/test", "tools": []},
                {
                    "type": "assistant",
                    "content": [{"type": "text", "text": "I investigated but ran out of turns..."}],
                },
                {
                    "type": "result",
                    "subtype": "error_max_turns",
                    "total_cost_usd": 0.90,
                    "duration_ms": 120000,
                },
            ]
        )
        with pytest.raises(ClaudePipeError, match="error max turns"):
            parse_claude_response(raw, VerifyResponse)

    def test_error_max_turns_with_valid_fallback_text(self):
        """error_max_turns but model DID produce valid JSON in text: extraction succeeds."""
        raw = json.dumps(
            [
                {"type": "system", "subtype": "init", "cwd": "/test", "tools": []},
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"verdicts": [{"index": 1, "verdict": "VALID", "reason": "Found in code"}]}',
                        }
                    ],
                },
                {
                    "type": "result",
                    "subtype": "error_max_turns",
                    "total_cost_usd": 0.50,
                },
            ]
        )
        resp = parse_claude_response(raw, VerifyResponse)
        assert len(resp.verdicts) == 1
        assert resp.verdicts[0].verdict.value == "VALID"

    def test_fallback_extracts_verify_model_without_response_model(self):
        """Fallback extraction still works for VerifyResponse (backwards compat)."""
        raw = json.dumps(
            [
                {"type": "system", "subtype": "init", "cwd": "/test", "tools": []},
                {
                    "type": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"verdicts": [{"index": 1, "verdict": "VALID", "reason": "Still true"}]}',
                        }
                    ],
                },
                {"type": "result", "structured_output": None},
            ]
        )

        resp = parse_claude_response(raw, VerifyResponse)
        assert len(resp.verdicts) == 1
        assert resp.verdicts[0].verdict.value == "VALID"


class TestRouting:
    """Test multi-backend priority routing in run_claude_pipe."""

    def _fake_cli(self, called: list):
        def _fn(*_a, **_kw):
            called.append("cli")
            return VerifyResponse(verdicts=[])

        return _fn

    def _fake_api(self, called: list):
        def _fn(*_a, **_kw):
            called.append("api")
            return VerifyResponse(verdicts=[])

        return _fn

    def test_cli_backend_has_highest_priority(self, monkeypatch):
        """anthropic_cli (priority 10) is tried first when available."""
        import keephive.llm.anthropic_cli as cli_mod

        called: list = []
        monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
        monkeypatch.setattr(cli_mod.backend, "call_structured", self._fake_cli(called))

        from keephive.claude import run_claude_pipe

        result = run_claude_pipe("test prompt", VerifyResponse)
        assert result.verdicts == []
        assert called == ["cli"]

    def test_api_backend_used_when_cli_unavailable(self, monkeypatch):
        """When CLI unavailable, anthropic_api (priority 20) takes over."""
        import keephive.llm.anthropic_api as api_mod
        import keephive.llm.anthropic_cli as cli_mod

        called: list = []
        monkeypatch.setattr(cli_mod.backend, "detect", lambda: (False, "cli unavailable"))
        monkeypatch.setattr(api_mod.backend, "detect", lambda: (True, "api mocked"))
        monkeypatch.setattr(api_mod.backend, "call_structured", self._fake_api(called))

        from keephive.claude import run_claude_pipe

        result = run_claude_pipe("test prompt", VerifyResponse)
        assert result.verdicts == []
        assert called == ["api"]

    def test_claudecode_does_not_affect_routing(self, monkeypatch):
        """CLAUDECODE env no longer changes routing; CLI is tried first regardless."""
        import keephive.llm.anthropic_cli as cli_mod

        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        called: list = []
        monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
        monkeypatch.setattr(cli_mod.backend, "call_structured", self._fake_cli(called))

        from keephive.claude import run_claude_pipe

        result = run_claude_pipe("test prompt", VerifyResponse)
        assert result.verdicts == []
        assert called == ["cli"]

    def test_tools_route_to_cli_backend(self, monkeypatch):
        """Tools are handled by the CLI backend (supports_tools=True, priority 10)."""
        import keephive.llm.anthropic_cli as cli_mod

        called: list = []
        monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
        monkeypatch.setattr(cli_mod.backend, "call_structured", self._fake_cli(called))

        from keephive.claude import run_claude_pipe

        result = run_claude_pipe("test", VerifyResponse, tools=["Read", "Grep"])
        assert result.verdicts == []
        assert called == ["cli"]

    def test_cli_preferred_over_api_at_terminal(self, monkeypatch):
        """API key in env but CLI takes priority (free over paid)."""
        import keephive.llm.anthropic_cli as cli_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        called: list = []
        monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
        monkeypatch.setattr(cli_mod.backend, "call_structured", self._fake_cli(called))

        from keephive.claude import run_claude_pipe

        result = run_claude_pipe("test prompt", VerifyResponse)
        assert result.verdicts == []
        assert called == ["cli"]

    def test_cli_preferred_for_tools_even_with_api_key(self, monkeypatch):
        """Tools + API key at terminal: CLI still takes priority (priority 10 vs 20)."""
        import keephive.llm.anthropic_cli as cli_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        called: list = []
        monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
        monkeypatch.setattr(cli_mod.backend, "call_structured", self._fake_cli(called))

        from keephive.claude import run_claude_pipe

        result = run_claude_pipe("test", VerifyResponse, tools=["Read"])
        assert result.verdicts == []
        assert called == ["cli"]


class TestRunViaApi:
    """Test the direct Anthropic API path (_run_via_api = anthropic_api._call_structured)."""

    def test_api_timeout_raises(self, monkeypatch):
        """API timeout produces ClaudePipeError."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("CLAUDECODE", raising=False)

        import anthropic

        mock_client_cls = MagicMock()
        mock_client = mock_client_cls.return_value
        mock_client.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())

        monkeypatch.setattr("anthropic.Anthropic", mock_client_cls)

        from keephive.claude import _run_via_api

        # New signature: (prompt, response_model, model, stdin_text, tools, max_turns, timeout, verbose)
        with pytest.raises(ClaudePipeError, match="timed out"):
            _run_via_api("test", VerifyResponse, "haiku", None, None, None, 30, False)

    def test_api_extracts_tool_use_block(self, monkeypatch):
        """API response with tool_use block is correctly parsed."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "structured_output"
        mock_block.input = {"verdicts": [{"index": 1, "verdict": "VALID", "reason": "OK"}]}

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.stop_reason = "end_turn"

        mock_client_cls = MagicMock()
        mock_client = mock_client_cls.return_value
        mock_client.messages.create.return_value = mock_response

        monkeypatch.setattr("anthropic.Anthropic", mock_client_cls)

        from keephive.claude import _run_via_api

        result = _run_via_api("test", VerifyResponse, "haiku", None, None, None, 30, False)
        assert len(result.verdicts) == 1
        assert result.verdicts[0].verdict.value == "VALID"

    def test_api_no_tool_use_block_raises(self, monkeypatch):
        """API response without tool_use block raises error."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Some text response"

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.stop_reason = "end_turn"

        mock_client_cls = MagicMock()
        mock_client = mock_client_cls.return_value
        mock_client.messages.create.return_value = mock_response

        monkeypatch.setattr("anthropic.Anthropic", mock_client_cls)

        from keephive.claude import _run_via_api

        with pytest.raises(ClaudePipeError, match="no tool_use block"):
            _run_via_api("test", VerifyResponse, "haiku", None, None, None, 30, False)

    def test_api_stdin_text_appended_to_prompt(self, monkeypatch):
        """stdin_text is appended to the prompt content for API calls."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "structured_output"
        mock_block.input = {"verdicts": []}

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.stop_reason = "end_turn"

        mock_client_cls = MagicMock()
        mock_client = mock_client_cls.return_value
        mock_client.messages.create.return_value = mock_response

        monkeypatch.setattr("anthropic.Anthropic", mock_client_cls)

        from keephive.claude import _run_via_api

        _run_via_api("prompt here", VerifyResponse, "haiku", "extra context", None, None, 30, False)

        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs["messages"]
        content = messages[0]["content"]
        assert "prompt here" in content
        assert "extra context" in content

    def test_api_model_mapping(self, monkeypatch):
        """Model shorthand is mapped to full API model name."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "structured_output"
        mock_block.input = {"verdicts": []}

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.stop_reason = "end_turn"

        mock_client_cls = MagicMock()
        mock_client = mock_client_cls.return_value
        mock_client.messages.create.return_value = mock_response

        monkeypatch.setattr("anthropic.Anthropic", mock_client_cls)

        from keephive.claude import _run_via_api

        _run_via_api("test", VerifyResponse, "sonnet", None, None, None, 30, False)

        call_args = mock_client.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-sonnet-4.6"

    def test_missing_anthropic_gives_setup_guidance(self, monkeypatch):
        """Missing anthropic package error tells user to run keephive setup."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("CLAUDECODE", raising=False)

        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from keephive.claude import _run_via_api

        with pytest.raises(ClaudePipeError, match="keephive setup"):
            _run_via_api("test", VerifyResponse, "haiku", None, None, None, 30, False)
