"""Tests for keephive MCP server tools.

Direct function calls, no MCP protocol overhead.
Uses the hive_env fixture for isolated test data.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def mcp_env(hive_env: Path):
    """Extend hive_env with a seeded guide for knowledge tests."""
    guide = hive_env / "knowledge" / "guides" / "testing-guide.md"
    guide.write_text("---\ntags: [testing]\n---\n\n# Testing Guide\n\nAlways write tests first.\n")
    return hive_env


def test_remember_fact(mcp_env: Path):
    from keephive.mcp_server import hive_remember

    result = hive_remember("FACT: Python uses GIL")
    assert "[FACT]" in result
    assert "Remembered" in result


def test_remember_plain(mcp_env: Path):
    from keephive.mcp_server import hive_remember

    result = hive_remember("some random note")
    assert "Remembered" in result
    assert "entries today" in result
    assert "[" not in result.split("at")[0] or "Remembered" in result


def test_recall_found(mcp_env: Path):
    from keephive.mcp_server import hive_recall

    result = hive_recall("Python")
    assert "Found" in result
    assert "working" in result


def test_recall_not_found(mcp_env: Path):
    from keephive.mcp_server import hive_recall

    result = hive_recall("xyzzy_nonexistent_12345")
    assert "No results" in result


def test_status_basic(mcp_env: Path):
    from keephive.mcp_server import hive_status

    result = hive_status()
    assert "keephive" in result


def test_status_stale_warning(mcp_env: Path):
    from keephive.mcp_server import hive_status

    result = hive_status()
    # Memory has a fact verified:2020-01-01 which is stale
    assert "STALE" in result or "stale" in result


def test_todo_empty(mcp_env: Path):
    from keephive.mcp_server import hive_todo

    result = hive_todo()
    assert "No open TODOs" in result


def test_todo_with_items(mcp_env: Path, daily_with_entries: Path):
    from keephive.mcp_server import hive_remember, hive_todo

    hive_remember("TODO: Write more tests")
    result = hive_todo()
    assert "open TODO" in result
    assert "Write more tests" in result


def test_done_marks_complete(mcp_env: Path):
    from keephive.mcp_server import hive_remember, hive_todo, hive_todo_done

    hive_remember("TODO: Fix the bug")
    result = hive_todo_done("bug")
    assert "Completed" in result
    assert "Fix the bug" in result

    # Verify it's gone from the todo list
    todo_result = hive_todo()
    assert "Fix the bug" not in todo_result


def test_done_no_match(mcp_env: Path):
    from keephive.mcp_server import hive_todo_done

    result = hive_todo_done("nonexistent_xyz")
    assert "No open TODO or recurring task matching" in result


def test_knowledge_list(mcp_env: Path):
    from keephive.mcp_server import hive_knowledge

    result = hive_knowledge()
    assert "Knowledge guides:" in result
    assert "testing-guide" in result


def test_knowledge_view(mcp_env: Path):
    from keephive.mcp_server import hive_knowledge

    result = hive_knowledge("testing")
    assert "Always write tests first" in result


def test_log_today(mcp_env: Path):
    from keephive.mcp_server import hive_log, hive_remember

    hive_remember("FACT: Log test entry")
    result = hive_log()
    assert "Log test entry" in result


def test_lifecycle(mcp_env: Path):
    """Full lifecycle: remember -> todo -> todo_done -> verify clean."""
    from keephive.mcp_server import hive_remember, hive_todo, hive_todo_done

    # 1. Remember a TODO
    r1 = hive_remember("TODO: Refactor the parser")
    assert "Remembered" in r1

    # 2. Check it appears
    r2 = hive_todo()
    assert "Refactor the parser" in r2

    # 3. Mark it done
    r3 = hive_todo_done("parser")
    assert "Completed" in r3

    # 4. Verify it's gone
    r4 = hive_todo()
    assert "Refactor the parser" not in r4


# --- MCP Write Tools ---


def test_knowledge_write_creates_guide(mcp_env: Path):
    from keephive.mcp_server import hive_knowledge_write

    result = hive_knowledge_write("api-patterns", "# API Patterns\n\nUse REST.")
    assert "Created" in result or "Updated" in result
    guide = mcp_env / "knowledge" / "guides" / "api-patterns.md"
    assert guide.exists()
    assert "Use REST." in guide.read_text()


def test_knowledge_write_updates_existing(mcp_env: Path):
    from keephive.mcp_server import hive_knowledge_write

    hive_knowledge_write("update-me", "# Original\nV1")
    hive_knowledge_write("update-me", "# Updated\nV2")
    guide = mcp_env / "knowledge" / "guides" / "update-me.md"
    content = guide.read_text()
    assert "V2" in content
    assert "V1" not in content


def test_knowledge_write_empty_name(mcp_env: Path):
    from keephive.mcp_server import hive_knowledge_write

    result = hive_knowledge_write("", "content")
    assert "Invalid" in result


def test_prompt_write_creates(mcp_env: Path):
    from keephive.mcp_server import hive_prompt_write

    result = hive_prompt_write("code-review", "Review this code:\n{{code}}")
    assert "Created" in result or "Updated" in result
    prompt = mcp_env / "knowledge" / "prompts" / "code-review.md"
    assert prompt.exists()
    assert "{{code}}" in prompt.read_text()


def test_prompt_write_empty_name(mcp_env: Path):
    from keephive.mcp_server import hive_prompt_write

    result = hive_prompt_write("", "content")
    assert "Invalid" in result


def test_mem_list(mcp_env: Path):
    from keephive.mcp_server import hive_mem

    result = hive_mem("list")
    assert "Python is great" in result
    assert "keephive uses Pydantic" in result


def test_mem_add(mcp_env: Path):
    from keephive.mcp_server import hive_mem

    result = hive_mem("add", "Redis uses port 6379")
    assert "Added" in result
    mem = (mcp_env / "working" / "memory.md").read_text()
    assert "Redis uses port 6379" in mem
    assert "[verified:" in mem.split("Redis")[1]


def test_mem_rm(mcp_env: Path):
    from keephive.mcp_server import hive_mem

    result = hive_mem("rm", "Python is great")
    assert "Removed" in result
    mem = (mcp_env / "working" / "memory.md").read_text()
    assert "Python is great" not in mem


def test_mem_rm_no_match(mcp_env: Path):
    from keephive.mcp_server import hive_mem

    result = hive_mem("rm", "nonexistent_xyz_fact")
    assert "No line matching" in result


def test_mem_invalid_action(mcp_env: Path):
    from keephive.mcp_server import hive_mem

    result = hive_mem("delete", "something")
    assert "Invalid" in result or "Unknown" in result


def test_rule_list(mcp_env: Path):
    from keephive.mcp_server import hive_rule

    result = hive_rule("list")
    assert "When You Learn Something New" in result


def test_rule_add(mcp_env: Path):
    from keephive.mcp_server import hive_rule

    result = hive_rule("add", "Always run tests before committing")
    assert "Added" in result
    rules = (mcp_env / "working" / "rules.md").read_text()
    assert "Always run tests before committing" in rules


def test_rule_rm(mcp_env: Path):
    from keephive.mcp_server import hive_rule

    hive_rule("add", "Temporary rule to remove")
    result = hive_rule("rm", "Temporary rule")
    assert "Removed" in result
    rules = (mcp_env / "working" / "rules.md").read_text()
    assert "Temporary rule to remove" not in rules


def test_rule_invalid_action(mcp_env: Path):
    from keephive.mcp_server import hive_rule

    result = hive_rule("delete", "something")
    assert "Invalid" in result or "Unknown" in result


# --- hive_recurring extended ---


@pytest.fixture
def recurring_env(hive_env: Path):
    """Extend hive_env with a seeded recurring.md."""
    rf = hive_env / "working" / "recurring.md"
    rf.write_text(
        "# Recurring Tasks\n\n"
        "- [daily] Review test coverage\n"
        "- [weekly] Check stale facts\n"
        "\n## Last Completed\n\n"
    )
    return hive_env


def test_recurring_list_all(recurring_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("list")
    assert "Review test coverage" in result
    assert "Check stale facts" in result


def test_recurring_list_empty(mcp_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("list")
    assert "No recurring tasks" in result


def test_recurring_add(mcp_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("add", "daily", "Run the test suite")
    assert "Added" in result
    assert "Run the test suite" in result

    list_result = hive_recurring("list")
    assert "Run the test suite" in list_result


def test_recurring_add_invalid_freq(mcp_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("add", "biweekly", "Some task")
    assert "Invalid" in result


def test_recurring_add_missing_text(mcp_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("add", "daily", "")
    assert "Error" in result or "required" in result


def test_recurring_rm(recurring_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("rm", text="stale facts")
    assert "Removed" in result
    assert "stale facts" in result

    list_result = hive_recurring("list")
    assert "Check stale facts" not in list_result


def test_recurring_rm_no_match(recurring_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("rm", text="nonexistent_xyz_task")
    assert "No recurring task" in result


def test_recurring_done(recurring_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("done", text="test coverage")
    assert "Done" in result
    assert "test coverage" in result.lower()


def test_recurring_done_no_match(recurring_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("done", text="nonexistent_xyz_task")
    assert "No recurring task" in result


def test_recurring_unknown_action(mcp_env: Path):
    from keephive.mcp_server import hive_recurring

    result = hive_recurring("purge")
    assert "Unknown action" in result


# --- hive_prompt ---


@pytest.fixture
def prompt_env(hive_env: Path):
    """Extend hive_env with a seeded prompt template."""
    prompt = hive_env / "knowledge" / "prompts" / "code-review.md"
    prompt.write_text(
        "Review this code:\n\n{{code}}\n\nFocus on: correctness, clarity, edge cases."
    )
    return hive_env


def test_prompt_found(prompt_env: Path):
    from keephive.mcp_server import hive_prompt

    result = hive_prompt("code-review")
    assert "Review this code" in result
    assert "{{code}}" in result


def test_prompt_prefix_match(prompt_env: Path):
    from keephive.mcp_server import hive_prompt

    result = hive_prompt("code")
    assert "Review this code" in result


def test_prompt_not_found(mcp_env: Path):
    from keephive.mcp_server import hive_prompt

    result = hive_prompt("nonexistent_xyz_prompt")
    assert "not found" in result.lower()


# --- hive_ps ---


def test_ps_returns_string(mcp_env: Path):
    from keephive.mcp_server import hive_ps

    result = hive_ps()
    assert isinstance(result, str)
    assert "local hive map" in result


def test_ps_shows_project(mcp_env: Path):
    from keephive.mcp_server import hive_ps

    result = hive_ps()
    assert "This project:" in result


# --- hive_standup ---


def test_standup_deterministic(mcp_env: Path, daily_with_entries: Path):
    from keephive.mcp_server import hive_standup

    result = hive_standup(use_llm=False)
    assert isinstance(result, str)
    # Deterministic standup always produces the three Slack sections
    assert "*Yesterday:*" in result or "*Today:*" in result or "*Blockers:*" in result


def test_standup_skip_llm(mcp_env: Path):
    """HIVE_SKIP_LLM causes standup to use deterministic path."""
    from keephive.mcp_server import hive_standup

    # hive_env fixture sets HIVE_SKIP_LLM=1
    result = hive_standup(use_llm=True)
    assert isinstance(result, str)
    assert len(result) > 0
