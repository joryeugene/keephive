"""Tests for LLM routing logic: multi-backend priority selection.

The new routing layer selects backends by priority:
  anthropic_cli (10) > anthropic_api (20) > gemini_api (25) > openai_api (30) > none (99)

CLAUDECODE env var no longer triggers special routing — it was removed when the
multi-backend layer was introduced. The CLI backend strips CLAUDECODE from the
subprocess env, so recursive CC invocation is not a concern.
"""

import pytest

from keephive.claude import ClaudePipeError, run_claude_pipe
from keephive.models import VerifyResponse


def _make_fake(called: list, label: str):
    """Return a fake call_structured that records which backend ran."""

    def _fn(*_a, **_kw):
        called.append(label)
        return VerifyResponse(verdicts=[], status="ok")

    return _fn


def test_routing_cli_backend_first(monkeypatch):
    """anthropic_cli (priority 10) is always tried first when available."""
    import keephive.llm.anthropic_cli as cli_mod

    called: list = []
    monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
    monkeypatch.setattr(cli_mod.backend, "call_structured", _make_fake(called, "cli"))

    run_claude_pipe("test", VerifyResponse)
    assert called == ["cli"]


def test_routing_api_fallback_when_cli_unavailable(monkeypatch):
    """When CLI unavailable, anthropic_api (priority 20) is used."""
    import keephive.llm.anthropic_api as api_mod
    import keephive.llm.anthropic_cli as cli_mod

    called: list = []
    monkeypatch.setattr(cli_mod.backend, "detect", lambda: (False, "cli unavailable"))
    monkeypatch.setattr(api_mod.backend, "detect", lambda: (True, "api mocked"))
    monkeypatch.setattr(api_mod.backend, "call_structured", _make_fake(called, "api"))

    run_claude_pipe("test", VerifyResponse)
    assert called == ["api"]


def test_routing_claudecode_does_not_affect_priority(monkeypatch):
    """CLAUDECODE env var no longer changes routing; CLI is still tried first."""
    import keephive.llm.anthropic_cli as cli_mod

    monkeypatch.setenv("CLAUDECODE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    called: list = []
    monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
    monkeypatch.setattr(cli_mod.backend, "call_structured", _make_fake(called, "cli"))

    run_claude_pipe("test", VerifyResponse)
    assert called == ["cli"]


def test_routing_no_available_backend_raises(monkeypatch):
    """When all real backends fail, none backend raises ClaudePipeError."""
    import keephive.llm.anthropic_api as api_mod
    import keephive.llm.anthropic_cli as cli_mod
    import keephive.llm.gemini_api as gem_mod
    import keephive.llm.openai_api as oai_mod

    monkeypatch.setattr(cli_mod.backend, "detect", lambda: (False, "unavailable"))
    monkeypatch.setattr(api_mod.backend, "detect", lambda: (False, "unavailable"))
    monkeypatch.setattr(gem_mod.backend, "detect", lambda: (False, "unavailable"))
    monkeypatch.setattr(oai_mod.backend, "detect", lambda: (False, "unavailable"))

    with pytest.raises(ClaudePipeError):
        run_claude_pipe("test", VerifyResponse)


def test_routing_tools_use_cli_backend(monkeypatch):
    """Tool requests are routed to CLI backend (supports_tools=True, priority 10)."""
    import keephive.llm.anthropic_cli as cli_mod

    called: list = []
    monkeypatch.setattr(cli_mod.backend, "detect", lambda: (True, "mocked"))
    monkeypatch.setattr(cli_mod.backend, "call_structured", _make_fake(called, "cli"))

    run_claude_pipe("test", VerifyResponse, tools=["Read"])
    assert called == ["cli"]
