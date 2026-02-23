"""Tests for LLM routing logic in claude.py (Terminal vs Claude Code)."""

import pytest
from keephive.claude import run_claude_pipe, ClaudePipeError
from keephive.models import VerifyResponse

def test_routing_terminal_no_key(monkeypatch):
    """Outside Claude Code, no key: must call subprocess."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    
    called = []
    def fake_sub(*args, **kwargs):
        called.append("subprocess")
        # Return a dummy model so parsing doesn't fail
        return VerifyResponse(verdicts=[], status="ok")
        
    monkeypatch.setattr("keephive.claude._run_via_subprocess", fake_sub)
    
    run_claude_pipe("test", VerifyResponse)
    assert called == ["subprocess"]

def test_routing_terminal_with_key(monkeypatch):
    """Outside Claude Code, WITH key: must STILL call subprocess (ignore key)."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    
    called = []
    def fake_sub(*args, **kwargs):
        called.append("subprocess")
        return VerifyResponse(verdicts=[], status="ok")
        
    monkeypatch.setattr("keephive.claude._run_via_subprocess", fake_sub)
    
    run_claude_pipe("test", VerifyResponse)
    assert called == ["subprocess"]

def test_routing_inside_cc_with_key(monkeypatch):
    """Inside Claude Code, with key: must call direct API."""
    monkeypatch.setenv("CLAUDECODE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    
    called = []
    def fake_api(*args, **kwargs):
        called.append("api")
        return VerifyResponse(verdicts=[], status="ok")
        
    monkeypatch.setattr("keephive.claude._run_via_api", fake_api)
    
    run_claude_pipe("test", VerifyResponse)
    assert called == ["api"]

def test_routing_inside_cc_no_key(monkeypatch):
    """Inside Claude Code, NO key: must raise ClaudePipeError with guidance."""
    monkeypatch.setenv("CLAUDECODE", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    
    with pytest.raises(ClaudePipeError, match="Set ANTHROPIC_API_KEY"):
        run_claude_pipe("test", VerifyResponse)

def test_routing_inside_cc_with_tools_fails(monkeypatch):
    """Inside Claude Code, even with key, tools must fail (require terminal)."""
    monkeypatch.setenv("CLAUDECODE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    
    # hive v uses tools, so it must fail inside CC
    with pytest.raises(ClaudePipeError, match="require a terminal"):
        run_claude_pipe("test", VerifyResponse, tools=["ls"])
