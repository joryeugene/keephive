"""Tests for session command (commands/session.py)."""

from __future__ import annotations

import pytest


class TestResolveMode:
    def test_no_args_returns_default(self, hive_env):
        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode([])
        assert mode == "default"
        assert "keephive context" in prompt.lower()

    def test_builtin_todo_mode(self, hive_env):
        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode(["todo"])
        assert mode == "todo"
        assert "TODO" in prompt

    def test_builtin_verify_mode(self, hive_env):
        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode(["verify"])
        assert mode == "verify"
        assert "stale" in prompt.lower()

    def test_builtin_learn_mode(self, hive_env):
        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode(["learn"])
        assert mode == "learn"
        assert "recall" in prompt.lower()

    def test_builtin_reflect_mode(self, hive_env):
        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode(["reflect"])
        assert mode == "reflect"
        assert "pattern" in prompt.lower()

    def test_case_insensitive(self, hive_env):
        from keephive.commands.session import _resolve_mode

        mode, _prompt = _resolve_mode(["TODO"])
        assert mode == "todo"

    def test_custom_prompt_file(self, hive_env):
        """Loads prompt from knowledge/prompts/ when mode matches filename."""
        pd = hive_env / "knowledge" / "prompts"
        (pd / "code-review.md").write_text("Review the code for security issues.")

        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode(["code-review"])
        assert mode == "code-review"
        assert "security" in prompt.lower()

    def test_custom_prompt_prefix_match(self, hive_env):
        """Prefix-matches prompt filenames."""
        pd = hive_env / "knowledge" / "prompts"
        (pd / "architecture-review.md").write_text("Review the architecture.")

        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode(["arch"])
        assert mode == "architecture-review"
        assert "architecture" in prompt.lower()

    def test_freeform_prompt_fallback(self, hive_env):
        """Unrecognized args become a freeform prompt."""
        from keephive.commands.session import _resolve_mode

        mode, prompt = _resolve_mode(["help", "me", "debug", "this"])
        assert mode == "custom"
        assert prompt == "help me debug this"


class TestParseArgs:
    def test_no_flags(self):
        from keephive.commands.session import _parse_args

        flags, remaining = _parse_args(["todo"])
        assert flags == []
        assert remaining == ["todo"]

    def test_continue_flag(self):
        from keephive.commands.session import _parse_args

        flags, remaining = _parse_args(["-c", "fix the auth bug"])
        assert flags == ["--continue"]
        assert remaining == ["fix the auth bug"]

    def test_continue_long_flag(self):
        from keephive.commands.session import _parse_args

        flags, remaining = _parse_args(["--continue", "keep going"])
        assert flags == ["--continue"]
        assert remaining == ["keep going"]

    def test_resume_with_id(self):
        from keephive.commands.session import _parse_args

        flags, remaining = _parse_args(["-r", "abc-123-uuid", "resume prompt"])
        assert flags == ["--resume", "abc-123-uuid"]
        assert remaining == ["resume prompt"]

    def test_resume_without_id(self):
        """When -r is the last flag before the prompt, prompt stays in remaining."""
        from keephive.commands.session import _parse_args

        flags, remaining = _parse_args(["-r", "just the prompt"])
        assert flags == ["--resume"]
        assert remaining == ["just the prompt"]

    def test_continue_with_mode(self):
        from keephive.commands.session import _parse_args

        flags, remaining = _parse_args(["-c", "todo"])
        assert flags == ["--continue"]
        assert remaining == ["todo"]

    def test_no_flags_empty(self):
        from keephive.commands.session import _parse_args

        flags, remaining = _parse_args([])
        assert flags == []
        assert remaining == []


class TestBuildSessionPrompt:
    def test_includes_context_and_prompt(self, hive_env):
        from keephive.commands.session import _build_session_prompt

        result = _build_session_prompt("todo", "Walk through TODOs", "Memory content here")
        assert "Memory content here" in result
        assert "Walk through TODOs" in result
        assert "todo" in result.lower()

    def test_includes_mode_header(self, hive_env):
        from keephive.commands.session import _build_session_prompt

        result = _build_session_prompt("verify", "Check facts", "ctx")
        assert "Session mode: verify" in result

    def test_piped_content_injected(self, hive_env):
        from keephive.commands.session import _build_session_prompt

        result = _build_session_prompt("custom", "review this", "ctx", piped="error log content")
        assert "Piped input" in result
        assert "error log content" in result

    def test_no_piped_content(self, hive_env):
        from keephive.commands.session import _build_session_prompt

        result = _build_session_prompt("custom", "review this", "ctx", piped=None)
        assert "Piped input" not in result


class TestCmdSession:
    def test_exits_if_claude_not_found(self, hive_env, monkeypatch):
        """Exits with error when claude CLI isn't available."""
        monkeypatch.setattr("shutil.which", lambda x: None)

        from keephive.commands.session import cmd_session

        with pytest.raises(SystemExit) as exc_info:
            cmd_session([])
        assert exc_info.value.code == 1

    def test_builds_context_from_sessionstart(self, hive_env, monkeypatch):
        """Verifies session reuses build_context from sessionstart."""
        captured_args = {}

        def mock_execvpe(file, args, env):
            captured_args["file"] = file
            captured_args["args"] = args
            captured_args["env"] = env
            raise SystemExit(0)  # Prevent actual exec

        monkeypatch.setattr("os.execvpe", mock_execvpe)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/claude")

        from keephive.commands.session import cmd_session

        with pytest.raises(SystemExit):
            cmd_session([])

        assert captured_args["file"] == "claude"
        assert captured_args["args"][0] == "claude"
        # The prompt should contain context from build_context
        prompt = captured_args["args"][1]
        assert "Working Memory" in prompt
        assert "keephive session context" in prompt

    def test_strips_claudecode_env(self, hive_env, monkeypatch):
        """CLAUDECODE env var is stripped to avoid blocking nested sessions."""
        captured_env = {}

        def mock_execvpe(file, args, env):
            captured_env.update(env)
            raise SystemExit(0)

        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setattr("os.execvpe", mock_execvpe)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/claude")

        from keephive.commands.session import cmd_session

        with pytest.raises(SystemExit):
            cmd_session([])

        assert "CLAUDECODE" not in captured_env

    def test_continue_flag_passed_to_claude(self, hive_env, monkeypatch):
        """-c flag is forwarded to claude argv before the prompt."""
        captured = {}

        def mock_execvpe(file, args, env):
            captured["args"] = args
            raise SystemExit(0)

        monkeypatch.setattr("os.execvpe", mock_execvpe)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/claude")
        monkeypatch.setattr("keephive.commands.session._read_stdin_if_piped", lambda: None)

        from keephive.commands.session import cmd_session

        with pytest.raises(SystemExit):
            cmd_session(["-c", "fix the bug"])

        args = captured["args"]
        assert "--continue" in args
        assert args.index("--continue") < args.index(args[-1])  # flag before prompt
        assert "fix the bug" in args[-1]

    def test_resume_flag_passed_to_claude(self, hive_env, monkeypatch):
        """-r flag is forwarded to claude argv."""
        captured = {}

        def mock_execvpe(file, args, env):
            captured["args"] = args
            raise SystemExit(0)

        monkeypatch.setattr("os.execvpe", mock_execvpe)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/claude")
        monkeypatch.setattr("keephive.commands.session._read_stdin_if_piped", lambda: None)

        from keephive.commands.session import cmd_session

        with pytest.raises(SystemExit):
            cmd_session(["-r", "abc-uuid", "resume task"])

        args = captured["args"]
        assert "--resume" in args
        assert "abc-uuid" in args

    def test_piped_stdin_injected_into_prompt(self, hive_env, monkeypatch):
        """Piped stdin content appears in the session prompt."""
        captured = {}

        def mock_execvpe(file, args, env):
            captured["args"] = args
            raise SystemExit(0)

        monkeypatch.setattr("os.execvpe", mock_execvpe)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/claude")
        monkeypatch.setattr(
            "keephive.commands.session._read_stdin_if_piped",
            lambda: "ERROR: segfault at 0x00",
        )

        from keephive.commands.session import cmd_session

        with pytest.raises(SystemExit):
            cmd_session(["diagnose this"])

        prompt = captured["args"][-1]
        assert "ERROR: segfault at 0x00" in prompt
        assert "Piped input" in prompt

    def test_mode_passed_to_prompt(self, hive_env, monkeypatch):
        """Mode-specific prompt is included in the session."""
        captured_args = {}

        def mock_execvpe(file, args, env):
            captured_args["args"] = args
            raise SystemExit(0)

        monkeypatch.setattr("os.execvpe", mock_execvpe)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/claude")

        from keephive.commands.session import cmd_session

        with pytest.raises(SystemExit):
            cmd_session(["todo"])

        prompt = captured_args["args"][1]
        assert "TODO" in prompt
        assert "Session mode: todo" in prompt


class TestCliDispatch:
    def test_session_in_commands(self):
        from keephive.cli import COMMANDS

        assert "session" in COMMANDS
        assert "sesh" in COMMANDS
        assert "go" in COMMANDS

    def test_all_point_to_session_module(self):
        from keephive.cli import COMMANDS

        for alias in ("session", "sesh", "go"):
            module, func = COMMANDS[alias]
            assert module == "keephive.commands.session"
            assert func == "cmd_session"

    def test_help_mentions_session(self, hive_env, capsys):
        from keephive.cli import main

        main(["help"])
        out = capsys.readouterr().out
        assert "session" in out.lower()
        assert "go" in out
