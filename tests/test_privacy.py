"""Tests for privacy controls: kill switch (hive privacy on/off) and CLI-only mode.

Covers:
- Storage helpers (llm_paused_file, is_llm_paused, set_llm_paused)
- Storage helpers for .force-cli (force_cli_file, is_force_cli, set_force_cli)
- Gate in run_claude_pipe raises LLMPausedError when flag is set
- LLMPausedError is a subclass of ClaudePipeError (silent in hooks)
- cmd_privacy output for on/off/cli/status
- hive privacy off clears BOTH .llm-paused and .force-cli
- _validate() in LLM router rejects API backends when .force-cli is set
- cmd_status --json includes llm_paused and force_cli flags
- cmd_checkup includes privacy gate + force_cli in JSON output
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from keephive.commands.checkup import cmd_checkup
from keephive.commands.privacy import cmd_privacy
from keephive.commands.status import cmd_status
from keephive.llm.exceptions import ClaudePipeError, LLMPausedError
from keephive.storage import (
    force_cli_file,
    is_force_cli,
    is_llm_paused,
    llm_paused_file,
    set_force_cli,
    set_llm_paused,
)

# ── Storage helpers (kill switch) ─────────────────────────────────────


class TestPrivacyStorage:
    def test_is_llm_paused_false_by_default(self, hive_env):
        assert not is_llm_paused()

    def test_set_llm_paused_creates_file(self, hive_env):
        set_llm_paused(True)
        assert llm_paused_file().exists()

    def test_set_llm_paused_false_removes_file(self, hive_env):
        set_llm_paused(True)
        set_llm_paused(False)
        assert not llm_paused_file().exists()

    def test_set_llm_paused_false_is_noop_when_absent(self, hive_env):
        # Should not raise even when the file doesn't exist
        set_llm_paused(False)
        assert not llm_paused_file().exists()

    def test_is_llm_paused_true_when_file_present(self, hive_env):
        set_llm_paused(True)
        assert is_llm_paused()

    def test_toggle_roundtrip(self, hive_env):
        set_llm_paused(True)
        assert is_llm_paused()
        set_llm_paused(False)
        assert not is_llm_paused()
        set_llm_paused(True)
        assert is_llm_paused()


# ── Storage helpers (.force-cli) ──────────────────────────────────────


class TestForceCli:
    def test_is_force_cli_false_by_default(self, hive_env):
        assert not is_force_cli()

    def test_set_force_cli_creates_file(self, hive_env):
        set_force_cli(True)
        assert force_cli_file().exists()

    def test_force_cli_file_path(self, hive_env):
        """force_cli_file() must live inside hive_dir, named .force-cli."""
        assert force_cli_file().name == ".force-cli"
        assert force_cli_file().parent.name == "hive"

    def test_set_force_cli_false_removes_file(self, hive_env):
        set_force_cli(True)
        set_force_cli(False)
        assert not force_cli_file().exists()

    def test_set_force_cli_false_noop_when_absent(self, hive_env):
        # Should not raise when the file doesn't exist
        set_force_cli(False)
        assert not force_cli_file().exists()

    def test_toggle_roundtrip(self, hive_env):
        set_force_cli(True)
        assert is_force_cli()
        set_force_cli(False)
        assert not is_force_cli()
        set_force_cli(True)
        assert is_force_cli()


# ── Exception hierarchy ───────────────────────────────────────────────


class TestLLMPausedError:
    def test_is_subclass_of_claude_pipe_error(self):
        """Hooks catch ClaudePipeError — LLMPausedError must inherit it."""
        assert issubclass(LLMPausedError, ClaudePipeError)

    def test_can_be_raised_and_caught_as_claude_pipe_error(self):
        with pytest.raises(ClaudePipeError):
            raise LLMPausedError("LLM calls are paused.")

    def test_message_is_preserved(self):
        err = LLMPausedError("Run `hive privacy off` to resume.")
        assert "privacy off" in str(err)


# ── Gate in run_claude_pipe ───────────────────────────────────────────


class TestRunClaudePipeGate:
    def test_raises_llm_paused_error_when_flag_set(self, hive_env):
        """run_claude_pipe must raise before any subprocess is launched."""
        from pydantic import BaseModel

        from keephive.claude import run_claude_pipe

        class _M(BaseModel):
            x: str = ""

        set_llm_paused(True)
        with pytest.raises(LLMPausedError, match="hive privacy off"):
            run_claude_pipe("test", _M)

    def test_no_llm_paused_error_when_flag_absent(self, hive_env):
        """Gate must be a pass-through when .llm-paused is absent."""
        from pydantic import BaseModel

        from keephive.claude import run_claude_pipe

        class _M(BaseModel):
            x: str = ""

        set_llm_paused(False)
        # Mock call_structured so no real LLM call is attempted.
        # The gate passes → call_structured is invoked → returns our mock.
        with patch("keephive.claude.call_structured", return_value=_M()) as mock_call:
            result = run_claude_pipe("test", _M)
            mock_call.assert_called_once()
        assert isinstance(result, _M)

    def test_gate_checked_before_call_structured(self, hive_env):
        """call_structured must never be invoked when paused."""
        from pydantic import BaseModel

        from keephive.claude import run_claude_pipe

        class _M(BaseModel):
            x: str = ""

        set_llm_paused(True)
        with patch("keephive.claude.call_structured") as mock_call:
            with pytest.raises(LLMPausedError):
                run_claude_pipe("test", _M)
            mock_call.assert_not_called()


# ── CLI: hive privacy ─────────────────────────────────────────────────


class TestCmdPrivacy:
    def test_privacy_on_creates_flag(self, hive_env, capsys):
        cmd_privacy(["on"])
        assert is_llm_paused()

    def test_privacy_off_removes_flag(self, hive_env, capsys):
        set_llm_paused(True)
        cmd_privacy(["off"])
        assert not is_llm_paused()

    def test_privacy_status_shows_on(self, hive_env, capsys):
        set_llm_paused(True)
        cmd_privacy([])
        out = capsys.readouterr().out
        assert "ON" in out

    def test_privacy_status_shows_off(self, hive_env, capsys):
        set_llm_paused(False)
        cmd_privacy([])
        out = capsys.readouterr().out
        assert "OFF" in out

    def test_privacy_on_output_mentions_privacy_off(self, hive_env, capsys):
        cmd_privacy(["on"])
        out = capsys.readouterr().out
        assert "privacy off" in out

    def test_privacy_unknown_subcommand_prints_usage(self, hive_env, capsys):
        cmd_privacy(["bogus"])
        out = capsys.readouterr().out
        assert "Unknown" in out or "usage" in out.lower()


# ── CLI: hive privacy cli ─────────────────────────────────────────────


class TestCmdPrivacyCli:
    def test_privacy_cli_sets_force_cli_flag(self, hive_env, capsys):
        cmd_privacy(["cli"])
        assert is_force_cli()

    def test_privacy_cli_does_not_set_llm_paused(self, hive_env, capsys):
        """cli mode must not activate the full kill switch."""
        cmd_privacy(["cli"])
        assert not is_llm_paused()

    def test_privacy_off_clears_force_cli_flag(self, hive_env, capsys):
        """hive privacy off must clear .force-cli as well as .llm-paused."""
        set_force_cli(True)
        set_llm_paused(True)
        cmd_privacy(["off"])
        assert not is_force_cli()
        assert not is_llm_paused()

    def test_privacy_off_clears_force_cli_even_when_paused_not_set(self, hive_env, capsys):
        """off is a full reset — clears .force-cli even if .llm-paused was never set."""
        set_force_cli(True)
        cmd_privacy(["off"])
        assert not is_force_cli()

    def test_privacy_cli_output_mentions_cli_only(self, hive_env, capsys):
        cmd_privacy(["cli"])
        out = capsys.readouterr().out
        assert "CLI" in out or "cli" in out.lower()

    def test_privacy_cli_output_mentions_privacy_off(self, hive_env, capsys):
        """The cli mode banner must tell the user how to disable it."""
        cmd_privacy(["cli"])
        out = capsys.readouterr().out
        assert "privacy off" in out

    def test_privacy_cli_alias_works(self, hive_env, capsys):
        """cli-only is a valid alias for cli."""
        cmd_privacy(["cli-only"])
        assert is_force_cli()


# ── LLM router: _validate() enforces force_cli ────────────────────────


class TestForceCliRouting:
    def test_api_backend_rejected_when_force_cli(self, hive_env):
        """When .force-cli is set, anthropic_api backend must be skipped."""
        from keephive.llm import _REGISTRY, _resolve_backend

        # Ensure anthropic_api is in registry
        assert "anthropic_api" in _REGISTRY, "anthropic_api backend not registered"

        set_force_cli(True)
        # Patch anthropic_api to appear available and anthropic_cli to appear unavailable.
        # The resolver must skip api and fall through to none (not use api).
        with (
            patch.object(_REGISTRY["anthropic_api"], "detect", return_value=(True, "key present")),
            patch.object(
                _REGISTRY["anthropic_cli"], "detect", return_value=(False, "claude not found")
            ),
        ):
            backend, meta = _resolve_backend()
            assert backend.name != "anthropic_api", (
                "_validate() must reject anthropic_api when .force-cli is set"
            )

    def test_cli_backend_accepted_when_force_cli(self, hive_env):
        """When .force-cli is set, anthropic_cli is still accepted."""
        from keephive.llm import _REGISTRY, _resolve_backend

        set_force_cli(True)
        with patch.object(
            _REGISTRY["anthropic_cli"], "detect", return_value=(True, "claude found")
        ):
            backend, meta = _resolve_backend()
            assert backend.name == "anthropic_cli"

    def test_force_cli_off_allows_api_backend(self, hive_env):
        """Without .force-cli, anthropic_api should be available as usual."""
        from keephive.llm import _REGISTRY, _resolve_backend

        set_force_cli(False)
        with (
            patch.object(_REGISTRY["anthropic_api"], "detect", return_value=(True, "key present")),
            patch.object(
                _REGISTRY["anthropic_cli"], "detect", return_value=(False, "claude not found")
            ),
        ):
            backend, meta = _resolve_backend()
            assert backend.name == "anthropic_api"


# ── CLI: hive status --json ───────────────────────────────────────────


class TestStatusJsonLlmPaused:
    def test_json_includes_llm_paused_false(self, hive_env, capsys):
        set_llm_paused(False)
        cmd_status(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["llm_paused"] is False

    def test_json_includes_llm_paused_true(self, hive_env, capsys):
        set_llm_paused(True)
        cmd_status(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["llm_paused"] is True


class TestStatusJsonForceCli:
    def test_json_includes_force_cli_false(self, hive_env, capsys):
        set_force_cli(False)
        cmd_status(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["force_cli"] is False

    def test_json_includes_force_cli_true(self, hive_env, capsys):
        set_force_cli(True)
        cmd_status(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["force_cli"] is True


# ── CLI: hive checkup --json ──────────────────────────────────────────


class TestCheckupJsonPrivacyPaused:
    def test_json_includes_privacy_paused_false(self, hive_env, capsys):
        set_llm_paused(False)
        cmd_checkup(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["privacy_paused"] is False

    def test_json_includes_privacy_paused_true(self, hive_env, capsys):
        set_llm_paused(True)
        cmd_checkup(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["privacy_paused"] is True

    def test_stage0_appears_in_report(self, hive_env, capsys):
        cmd_checkup([])
        out = capsys.readouterr().out
        assert "Stage 0" in out or "Privacy Gate" in out


class TestCheckupJsonForceCli:
    def test_json_includes_force_cli_false(self, hive_env, capsys):
        set_force_cli(False)
        cmd_checkup(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["force_cli"] is False

    def test_json_includes_force_cli_true(self, hive_env, capsys):
        set_force_cli(True)
        cmd_checkup(["--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["force_cli"] is True


# ── Routing log helpers ───────────────────────────────────────────────


class TestReadRoutingLog:
    def test_empty_when_file_absent(self, hive_env):
        from keephive.storage import read_routing_log

        result = read_routing_log()
        assert result == []

    def test_returns_lines_when_file_present(self, hive_env):
        from keephive.storage import read_routing_log, routing_log_file

        path = routing_log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[2026-02-27 12:00:00] blocked: anthropic_api — CLI-only policy\n"
            "[2026-02-27 12:01:00] used: anthropic_cli — auto\n",
            encoding="utf-8",
        )
        lines = read_routing_log()
        assert len(lines) == 2
        assert "blocked: anthropic_api" in lines[0]
        assert "used: anthropic_cli" in lines[1]

    def test_n_parameter_limits_returned_lines(self, hive_env):
        from keephive.storage import read_routing_log, routing_log_file

        path = routing_log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(f"line {i}" for i in range(50)) + "\n", encoding="utf-8")
        lines = read_routing_log(5)
        assert len(lines) == 5
        assert lines[-1] == "line 49"  # last line returned, newest last
