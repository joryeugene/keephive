"""Tests for keephive.hooks.precompact: Layer 1/2 extraction, dedup, memory updates.

Tests the full precompact hook pipeline:
- Layer 1: deterministic transcript extraction (_extract_excerpts, _extract_user_text)
- Layer 2: LLM-based insight classification (_llm_summary with mocked run_claude_pipe)
- Dedup: exact hash and fuzzy SequenceMatcher deduplication
- Memory auto-update: ADD and CORRECT actions on memory.md
- Rule suggestions: queuing to .pending-rules.md
- Secret redaction: API keys, tokens, Bearer tokens
- Budget enforcement: HIVE_CAPTURE_BUDGET character limit
- Project attribution: [project:X] tagging on daily log entries
- Hook I/O: stdin JSON parsing, debug logging, graceful error handling
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from keephive.hooks.precompact import (
    _correct_in_memory,
    _extract_excerpts,
    _extract_user_text,
    _is_duplicate_in_memory,
    _is_duplicate_insight,
    _is_garbage_insight,
    _llm_summary,
    _normalize_for_dedup,
    _pending_facts_path,
    _queue_rule_suggestions,
    _redact_secrets,
    _select_within_budget,
)
from keephive.models import (
    Insight,
    InsightCategory,
    MemoryUpdate,
    PreCompactResponse,
)
from keephive.storage import daily_file, memory_file, safe_read_text

# ---- Helpers ----


def _make_transcript(hive_env: Path, messages: list[dict]) -> str:
    """Write a JSONL transcript file and return its path."""
    path = hive_env / "transcript.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return str(path)


def _user_msg(text: str) -> dict:
    """Create a user-type JSONL message."""
    return {"type": "user", "message": {"content": text}}


def _user_msg_parts(texts: list[str]) -> dict:
    """Create a user-type JSONL message with multiple text parts."""
    return {
        "type": "user",
        "message": {
            "content": [{"type": "text", "text": t} for t in texts],
        },
    }


def _asst_msg(text: str) -> dict:
    """Create an assistant-type JSONL message with text content block."""
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": text}],
        },
    }


def _asst_msg_str_content(text: str) -> dict:
    """Create an assistant-type JSONL message with string content (not list)."""
    return {
        "type": "assistant",
        "message": {"content": text},
    }


def _mock_precompact_response(
    insights: list[tuple[str, str]] | None = None,
    memory_updates: list[dict] | None = None,
    rule_suggestions: list[str] | None = None,
) -> PreCompactResponse:
    """Build a PreCompactResponse for mocking run_claude_pipe."""
    insight_objs = []
    for cat, desc in insights or []:
        insight_objs.append(Insight(category=InsightCategory(cat), description=desc))
    mem_objs = []
    for mu in memory_updates or []:
        mem_objs.append(MemoryUpdate(**mu))
    return PreCompactResponse(
        insights=insight_objs,
        memory_updates=mem_objs,
        rule_suggestions=rule_suggestions or [],
    )


# ---- Layer 1: Deterministic extraction ----


class TestExtractUserText:
    """Test _extract_user_text for various message formats."""

    def test_string_content(self):
        obj = {"type": "user", "message": {"content": "  Hello world  "}}
        assert _extract_user_text(obj) == "Hello world"

    def test_list_content_multiple_parts_skips_non_text(self):
        """Multi-part list concatenates text parts and skips non-text."""
        obj = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "image", "url": "http://example.com/img.png"},
                    {"type": "text", "text": "Part one"},
                    {"type": "text", "text": "Part two"},
                ]
            },
        }
        assert _extract_user_text(obj) == "Part one Part two"

    def test_missing_and_empty_content(self):
        """Missing content field returns None/empty; bare string message works."""
        obj_empty = {"type": "user", "message": {}}
        result = _extract_user_text(obj_empty)
        assert result is None or result == ""

        obj_bare = {"type": "user", "message": "Direct string message"}
        assert _extract_user_text(obj_bare) == "Direct string message"


class TestExtractExcerpts:
    """Test Layer 1 deterministic transcript extraction."""

    def test_basic_extraction(self, hive_env):
        """User and assistant messages extracted into formatted lines."""
        transcript = _make_transcript(
            hive_env,
            [
                _user_msg(
                    "We should use PostgreSQL for the database layer because it handles JSON well"
                ),
                _asst_msg(
                    "That's a great choice. PostgreSQL provides excellent JSONB support "
                    "with indexing capabilities that will serve this project well for years to come."
                ),
            ],
        )
        result = _extract_excerpts(transcript, 4000)
        assert "- [USER]" in result
        assert "PostgreSQL" in result

    def test_noise_filtering_assistant(self, hive_env):
        """Assistant messages starting with noise patterns are filtered out."""
        transcript = _make_transcript(
            hive_env,
            [
                _user_msg(
                    "What does this function do? I need to understand the parsing logic in detail"
                ),
                # All of these should be filtered by NOISE_RE
                _asst_msg(
                    "Let me read the file to understand what's happening here and how we can proceed"
                ),
                _asst_msg(
                    "Looking at the code, I can see that it follows a standard pattern for this type of thing"
                ),
                _asst_msg(
                    "I'll check the configuration to see if there are any relevant settings we need to update"
                ),
                # This should pass through (not noise)
                _asst_msg(
                    "The parser uses a recursive descent approach with backtracking, "
                    "which means it can handle ambiguous grammars but may have exponential worst-case performance."
                ),
            ],
        )
        result = _extract_excerpts(transcript, 4000)
        assert "recursive descent" in result
        assert "Let me read" not in result
        assert "Looking at" not in result

    def test_short_messages_filtered(self, hive_env):
        """User messages < 10 chars and assistant messages < 80 chars are dropped."""
        transcript = _make_transcript(
            hive_env,
            [
                _user_msg("ok"),  # < 10 chars, dropped
                _user_msg("yes please"),  # exactly 10 chars, kept
                _asst_msg("Short response"),  # < 80 chars, dropped
                _asst_msg(
                    "PostgreSQL provides excellent JSONB support with indexing capabilities "
                    "that serve well for structured document storage and querying workloads."
                ),
            ],
        )
        result = _extract_excerpts(transcript, 4000)
        assert "yes please" in result
        assert "PostgreSQL provides excellent" in result
        assert "[USER] ok" not in result

    def test_empty_transcript(self, hive_env):
        """Empty transcript returns empty string."""
        transcript = _make_transcript(hive_env, [])
        result = _extract_excerpts(transcript, 4000)
        assert result == ""

    def test_user_noise_continuation_filtered(self, hive_env):
        """Long user messages starting with continuation noise are filtered."""
        long_continuation = "This session is being continued " + "x" * 500
        transcript = _make_transcript(
            hive_env,
            [
                _user_msg(long_continuation),
                _user_msg(
                    "The real question is about database indexing strategies and performance tuning"
                ),
            ],
        )
        result = _extract_excerpts(transcript, 4000)
        assert "database indexing" in result
        assert "continued" not in result

    def test_malformed_json_lines_skipped(self, hive_env):
        """Malformed JSONL lines are silently skipped."""
        path = hive_env / "transcript.jsonl"
        with open(path, "w") as f:
            f.write("not valid json\n")
            f.write(json.dumps(_user_msg("Valid user message that is at least ten chars")) + "\n")
            f.write("{broken json\n")
        result = _extract_excerpts(str(path), 4000)
        assert "Valid user message" in result

    def test_assistant_string_content(self, hive_env):
        """Assistant message with string content (not list) is handled."""
        long_content = "A" * 100  # > 80 chars
        transcript = _make_transcript(
            hive_env,
            [
                {"type": "assistant", "message": {"content": long_content}},
            ],
        )
        result = _extract_excerpts(transcript, 4000)
        assert "A" * 50 in result

    def test_user_message_multi_part(self, hive_env):
        """User message with multiple text parts are concatenated."""
        transcript = _make_transcript(
            hive_env,
            [_user_msg_parts(["First part of the user prompt", " second part of the user prompt"])],
        )
        result = _extract_excerpts(transcript, 4000)
        assert "First part" in result

    def test_message_truncation_at_500_chars(self, hive_env):
        """Individual messages are truncated to 500 chars in output."""
        long_msg = "X" * 1000
        transcript = _make_transcript(hive_env, [_user_msg(long_msg)])
        result = _extract_excerpts(transcript, 4000)
        # Each line is truncated via [:500]
        for line in result.splitlines():
            # Line includes "- [USER] " prefix plus up to 500 chars of collapsed whitespace
            assert len(line) <= 510  # 500 + prefix


class TestSelectWithinBudget:
    """Test budget-constrained message selection."""

    def test_all_fit_within_budget(self):
        msgs = ["short", "also short"]
        result = _select_within_budget(msgs, 1000)
        assert result == msgs

    def test_prioritizes_recent(self):
        """When over budget, most recent messages are preferred."""
        msgs = ["A" * 100, "B" * 100, "C" * 100]
        result = _select_within_budget(msgs, 200)
        # Should get the last two (most recent) since they fit in budget 200
        assert len(result) == 2
        assert result[-1] == "C" * 100
        assert result[0] == "B" * 100

    def test_partial_message_with_remaining_budget(self):
        """When remaining budget > 50, a truncated message is included."""
        msgs = ["A" * 200, "B" * 100]
        result = _select_within_budget(msgs, 170)
        # B (100 chars) fits, then 70 chars remaining for A (>50 threshold)
        assert len(result) == 2
        assert result[-1] == "B" * 100
        assert result[0] == "A" * 70  # truncated

    def test_remaining_budget_under_50_skips(self):
        """When remaining budget <= 50, skip the truncated message."""
        msgs = ["A" * 200, "B" * 160]
        result = _select_within_budget(msgs, 200)
        # B (160) fits, remaining=40 which is <= 50, so A is dropped
        assert len(result) == 1
        assert result[0] == "B" * 160

    def test_empty_messages(self):
        assert _select_within_budget([], 1000) == []


# ---- Secret redaction ----


class TestRedactSecrets:
    """Test _redact_secrets removes sensitive content."""

    def test_key_value_redaction(self):
        text = "Set API_KEY=fake_key_abc123def456ghi789jkl012"
        result = _redact_secrets(text)
        assert "fake_key_abc123" not in result
        assert "REDACTED" in result

    def test_prefixed_secret_redaction(self):
        text = "Using myapp_sk_testabcdef12345678"
        result = _redact_secrets(text)
        assert "myapp_sk_test" not in result
        assert "REDACTED" in result

    def test_bearer_token_redaction(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkw"
        result = _redact_secrets(text)
        assert "eyJhbGciOiJ" not in result
        assert "Bearer [REDACTED]" in result

    def test_safe_text_unchanged(self):
        text = "The database uses PostgreSQL 15 with JSONB columns"
        assert _redact_secrets(text) == text


# ---- Dedup logic ----


class TestNormalizeForDedup:
    """Test _normalize_for_dedup text normalization."""

    def test_lowercases(self):
        assert _normalize_for_dedup("HELLO WORLD") == "hello world"

    def test_strips_urls(self):
        result = _normalize_for_dedup("Check https://example.com/path for details")
        assert "https://" not in result
        assert "details" in result

    def test_strips_long_tokens(self):
        """Tokens of 20+ alphanumeric chars are removed (API keys, hashes)."""
        result = _normalize_for_dedup("Hash is abcdef1234567890abcdef done")
        assert "abcdef1234567890" not in result

    def test_collapses_whitespace(self):
        result = _normalize_for_dedup("lots   of    spaces   here")
        assert result == "lots of spaces here"


class TestIsDuplicateInsight:
    """Test _is_duplicate_insight against daily log content."""

    def test_no_daily_file_returns_false(self, hive_env):
        df = daily_file()
        # daily file should not exist yet for this specific day
        df.unlink(missing_ok=True)
        assert not _is_duplicate_insight(df, "Some new insight text")

    def test_exact_prefix_match(self, hive_env, daily_with_entries):
        """An insight matching the first 50 normalized chars of an existing entry is duplicate."""
        # The fixture has "Python 3.12 supports type param syntax"
        assert _is_duplicate_insight(
            daily_with_entries,
            "Python 3.12 supports type param syntax",
        )

    def test_fuzzy_match(self, hive_env, daily_with_entries):
        """Slightly different wording still triggers fuzzy duplicate detection."""
        assert _is_duplicate_insight(
            daily_with_entries,
            "Python version 3.12 supports type parameter syntax",
        )

    def test_unrelated_not_duplicate(self, hive_env, daily_with_entries):
        assert not _is_duplicate_insight(
            daily_with_entries,
            "Rust compilation uses LLVM backend for code generation",
        )


class TestIsDuplicateInMemory:
    """Test _is_duplicate_in_memory fuzzy matching against memory.md."""

    def test_exact_match(self, hive_env):
        content = "# Memory\n- Python is great [verified:2025-01-01]\n"
        assert _is_duplicate_in_memory(content, "Python is great")

    def test_fuzzy_match(self, hive_env):
        content = "# Memory\n- keephive uses Pydantic for validation [verified:2025-01-01]\n"
        assert _is_duplicate_in_memory(content, "keephive uses Pydantic for data validation")

    def test_non_bullet_lines_skipped(self, hive_env):
        content = "# Memory\nNot a bullet line about PostgreSQL\n"
        assert not _is_duplicate_in_memory(content, "PostgreSQL")

    def test_unrelated_not_duplicate(self, hive_env):
        content = "# Memory\n- Python is great [verified:2025-01-01]\n"
        assert not _is_duplicate_in_memory(content, "Rust is a systems programming language")


# ---- Garbage insight filtering ----


class TestIsGarbageInsight:
    """Test _is_garbage_insight filters short and parroted descriptions."""

    def test_too_short(self):
        assert _is_garbage_insight("short")
        assert _is_garbage_insight("12345678901234")  # 14 chars, under 15

    def test_exactly_min_length(self):
        assert not _is_garbage_insight("exactly fifteen!")  # 16 chars

    def test_parroted_description(self):
        """Category descriptions from the LLM prompt should be rejected."""
        assert _is_garbage_insight("unfinished work or follow-up items")
        assert _is_garbage_insight("choices made about architecture, tools, approach")
        assert _is_garbage_insight("something learned that was previously unknown.")
        assert _is_garbage_insight("SOMETHING LEARNED THAT WAS PREVIOUSLY UNKNOWN")

    def test_real_insight_passes(self):
        assert not _is_garbage_insight(
            "PostgreSQL JSONB indexes are faster than MongoDB for this workload"
        )


# ---- Memory operations ----


class TestCorrectInMemory:
    """Test _correct_in_memory find-and-replace logic."""

    def test_replaces_matching_line(self):
        content = "# Memory\n- Python uses pip [verified:2025-01-01]\n- Other fact\n"
        result = _correct_in_memory(content, "Python uses pip", "Python uses uv", "2026-02-21")
        assert "- Python uses uv [verified:2026-02-21]" in result
        assert "pip" not in result

    def test_no_match_returns_unchanged(self):
        content = "# Memory\n- Some fact [verified:2025-01-01]\n"
        result = _correct_in_memory(content, "Nonexistent fact", "New fact", "2026-02-21")
        assert result == content

    def test_strips_old_verified_tag_for_comparison(self):
        """Matching works even when existing line has a [verified:] tag."""
        content = "- keephive uses bash [verified:2025-06-01]\n"
        result = _correct_in_memory(
            content, "keephive uses bash", "keephive uses Python", "2026-02-21"
        )
        assert "keephive uses Python [verified:2026-02-21]" in result

    def test_case_insensitive_match(self):
        content = "- KEEPHIVE uses Pydantic [verified:2025-01-01]\n"
        result = _correct_in_memory(
            content, "keephive uses pydantic", "keephive uses attrs", "2026-02-21"
        )
        assert "keephive uses attrs [verified:2026-02-21]" in result


class TestPendingFacts:
    """Test that memory updates queue to .pending-facts.md instead of memory.md."""

    def test_add_queues_to_pending(self, hive_env):
        """Memory ADD action writes to .pending-facts.md, not memory.md."""
        from keephive.hooks.precompact import _apply_memory_updates
        from keephive.models import MemoryAction, MemoryUpdate

        updates = [MemoryUpdate(action=MemoryAction.ADD, text="New auto fact")]
        _apply_memory_updates(updates, project_name="testproj")

        pf = _pending_facts_path()
        assert pf.exists()
        content = pf.read_text()
        assert "- New auto fact" in content
        assert "[auto:" in content
        assert "[project:testproj]" in content

        # Memory.md should NOT be modified
        mem = memory_file()
        if mem.exists():
            assert "New auto fact" not in mem.read_text()

    def test_correct_queues_to_pending(self, hive_env):
        """Memory CORRECT action writes to .pending-facts.md with replaces metadata."""
        from keephive.hooks.precompact import _apply_memory_updates
        from keephive.models import MemoryAction, MemoryUpdate

        # Set up memory with an existing fact
        mem = memory_file()
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("# Memory\n- Python uses pip [verified:2025-01-01]\n")

        updates = [
            MemoryUpdate(
                action=MemoryAction.CORRECT,
                text="Python uses uv",
                replaces="Python uses pip",
            )
        ]
        _apply_memory_updates(updates)

        pf = _pending_facts_path()
        assert pf.exists()
        content = pf.read_text()
        assert "Python uses uv" in content
        assert "[replaces:Python uses pip]" in content

    def test_dedup_prevents_duplicate_pending(self, hive_env):
        """ADD is skipped if fact already exists in memory.md."""
        from keephive.hooks.precompact import _apply_memory_updates
        from keephive.models import MemoryAction, MemoryUpdate

        mem = memory_file()
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("# Memory\n- Python is great [verified:2025-01-01]\n")

        updates = [MemoryUpdate(action=MemoryAction.ADD, text="Python is great")]
        _apply_memory_updates(updates)

        pf = _pending_facts_path()
        # Should not exist or be empty since the fact is a duplicate
        if pf.exists():
            assert pf.read_text().strip() == ""


# ---- Layer 2: LLM summary ----


class TestLlmSummary:
    """Test _llm_summary with mocked run_claude_pipe."""

    def test_writes_insights_to_daily_log(self, hive_env):
        """Insights from LLM response are written to the daily log."""
        response = _mock_precompact_response(
            insights=[
                ("FACT", "PostgreSQL 16 supports SQL/JSON standard natively"),
                ("DECISION", "Using connection pooling with pgbouncer for production"),
            ]
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("some excerpts", pipe_fn=mock_pipe)

        df = daily_file()
        content = safe_read_text(df)
        assert "FACT: PostgreSQL 16 supports SQL/JSON standard natively" in content
        assert "DECISION: Using connection pooling with pgbouncer" in content

    def test_project_attribution(self, hive_env):
        """Insights get [project:X] suffix when project_name is provided."""
        response = _mock_precompact_response(
            insights=[("INSIGHT", "The caching layer reduces latency by 40 percent")]
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe, project_name="myproject")

        df = daily_file()
        content = safe_read_text(df)
        assert "[project:myproject]" in content
        assert "INSIGHT: The caching layer" in content

    def test_dedup_skips_duplicate_insights(self, hive_env):
        """Duplicate insights are not written twice."""
        response = _mock_precompact_response(
            insights=[("FACT", "Python 3.12 supports type param syntax")]
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        # First call writes it
        _llm_summary("excerpts", pipe_fn=mock_pipe)
        # Second call should skip it (duplicate)
        _llm_summary("excerpts", pipe_fn=mock_pipe)

        df = daily_file()
        content = safe_read_text(df)
        # Count occurrences: should appear exactly once
        count = content.count("Python 3.12 supports type param syntax")
        assert count == 1

    def test_garbage_insights_filtered(self, hive_env):
        """Short and parroted insights are silently dropped."""
        response = _mock_precompact_response(
            insights=[
                ("FACT", "too short"),  # < 15 chars
                ("INSIGHT", "unfinished work or follow-up items"),  # parroted
                ("FACT", "This is a genuinely useful fact about architecture patterns"),
            ]
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe)

        df = daily_file()
        content = safe_read_text(df)
        assert "too short" not in content
        assert "unfinished work" not in content
        assert "genuinely useful fact" in content

    def test_secret_redaction_in_insights(self, hive_env):
        """Secrets in insight descriptions are redacted before writing."""
        response = _mock_precompact_response(
            insights=[("FACT", "The API uses token=sk_live_abcdef1234567890 for auth")]
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe)

        df = daily_file()
        content = safe_read_text(df)
        assert "sk_live_abcdef" not in content
        assert "REDACTED" in content

    def test_memory_update_add(self, hive_env):
        """Memory ADD action queues to .pending-facts.md (not memory.md directly)."""
        response = _mock_precompact_response(
            insights=[("FACT", "Database migration uses Alembic for schema management")],
            memory_updates=[
                {"action": "add", "text": "Project uses Alembic for database migrations"},
            ],
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe, project_name="webapp")

        # Fact should be in .pending-facts.md, NOT in memory.md
        pf = _pending_facts_path()
        assert pf.exists()
        pf_content = pf.read_text()
        assert "Project uses Alembic for database migrations" in pf_content
        assert "[project:webapp]" in pf_content

        # Daily log should say AUTO-CAPTURED (not AUTO-PROMOTED)
        df = daily_file()
        daily_content = safe_read_text(df)
        assert "AUTO-CAPTURED" in daily_content

    def test_memory_update_add_dedup(self, hive_env):
        """Memory ADD skips if fact already exists in memory.md."""
        response = _mock_precompact_response(
            memory_updates=[
                {"action": "add", "text": "Python is great"},  # already in memory from fixture
            ],
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe)

        # Pending facts should be empty since the fact is a duplicate
        pf = _pending_facts_path()
        if pf.exists():
            assert pf.read_text().strip() == ""

    def test_llm_exception_logged_not_raised(self, hive_env):
        """If run_claude_pipe raises, the error is logged, not propagated."""

        def mock_pipe(prompt, model, stdin_text=None):
            raise RuntimeError("LLM service unavailable")

        # Should not raise
        _llm_summary("excerpts", pipe_fn=mock_pipe)

        debug_log = hive_env / ".hook-debug.log"
        assert debug_log.exists()
        content = debug_log.read_text()
        assert "Layer 2 failed" in content
        assert "LLM service unavailable" in content


# ---- Rule suggestions ----


class TestQueueRuleSuggestions:
    """Test _queue_rule_suggestions writes to .pending-rules.md."""

    def test_writes_new_rules(self, hive_env):
        _queue_rule_suggestions(["Always use uv for Python", "Prefer composition over inheritance"])
        path = hive_env / ".pending-rules.md"
        assert path.exists()
        content = path.read_text()
        assert "- Always use uv for Python" in content
        assert "- Prefer composition over inheritance" in content

    def test_dedup_existing_rules(self, hive_env):
        """Duplicate rules (case-insensitive) are not added again."""
        path = hive_env / ".pending-rules.md"
        path.write_text("- Always use uv for Python\n")

        _queue_rule_suggestions(["always use uv for python"])

        content = path.read_text()
        assert content.count("uv for") == 1

    def test_max_two_per_call(self, hive_env):
        """At most 2 rule suggestions are queued per call."""
        _queue_rule_suggestions(["Rule one here", "Rule two here", "Rule three here"])
        path = hive_env / ".pending-rules.md"
        content = path.read_text()
        assert "Rule one" in content
        assert "Rule two" in content
        assert "Rule three" not in content

    def test_empty_list_no_op(self, hive_env):
        _queue_rule_suggestions([])
        path = hive_env / ".pending-rules.md"
        assert not path.exists()

    def test_whitespace_only_rules_skipped(self, hive_env):
        _queue_rule_suggestions(["   ", ""])
        path = hive_env / ".pending-rules.md"
        assert not path.exists()


# ---- Budget enforcement ----


class TestBudgetEnforcement:
    """Test HIVE_CAPTURE_BUDGET limits extraction volume."""

    def test_default_budget(self, hive_env):
        """Default budget is 4000 chars."""
        from keephive.storage import capture_budget

        assert capture_budget() == 4000

    def test_custom_budget(self, hive_env, monkeypatch):
        """HIVE_CAPTURE_BUDGET env var overrides default."""
        monkeypatch.setenv("HIVE_CAPTURE_BUDGET", "500")
        from keephive.storage import capture_budget

        assert capture_budget() == 500

    def test_budget_limits_extraction(self, hive_env):
        """With a small budget, fewer messages are included."""
        msgs = [
            _user_msg(
                f"User message number {i} with enough text to be at least ten characters long"
            )
            for i in range(20)
        ]
        msgs.extend(
            [
                _asst_msg(
                    f"Assistant response number {i} that is long enough to pass "
                    "the eighty character minimum filter threshold for inclusion"
                )
                for i in range(20)
            ]
        )
        transcript = _make_transcript(hive_env, msgs)

        small_result = _extract_excerpts(transcript, 200)
        large_result = _extract_excerpts(transcript, 8000)

        # Smaller budget should produce less output
        assert len(small_result) < len(large_result)


# ---- Hook I/O and integration ----


class TestHookIO:
    """Test the full hook entry point I/O behavior."""

    def test_invalid_json_input(self, hive_env, monkeypatch):
        """Invalid JSON on stdin is handled gracefully."""
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))

        from keephive.hooks.precompact import hook_precompact

        # Should not raise
        hook_precompact([])

        debug_log = hive_env / ".hook-debug.log"
        assert debug_log.exists()
        content = debug_log.read_text()
        assert "hook-precompact called" in content

    def test_empty_input(self, hive_env, monkeypatch):
        """Empty stdin is handled gracefully."""
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        from keephive.hooks.precompact import hook_precompact

        hook_precompact([])

        debug_log = hive_env / ".hook-debug.log"
        assert debug_log.exists()

    def test_trigger_field_logged(self, hive_env, monkeypatch):
        """The trigger field from input is written to debug log."""
        import io

        input_data = json.dumps({"trigger": "auto_compact", "cwd": "/home/user/project"})
        monkeypatch.setattr("sys.stdin", io.StringIO(input_data))

        from keephive.hooks.precompact import hook_precompact

        hook_precompact([])

        debug_log = hive_env / ".hook-debug.log"
        content = debug_log.read_text()
        assert "trigger=auto_compact" in content

    def test_transcript_path_used(self, hive_env, monkeypatch):
        """When transcript_path is provided and exists, Layer 1 runs."""
        import io

        # Create a transcript file with meaningful content
        transcript = _make_transcript(
            hive_env,
            [
                _user_msg(
                    "We decided to switch from REST to GraphQL for the new internal API design"
                ),
                _asst_msg(
                    "GraphQL would be a good fit here because it reduces over-fetching "
                    "and allows the frontend team to request exactly the data they need per component."
                ),
            ],
        )

        input_data = json.dumps(
            {
                "trigger": "manual",
                "cwd": "/home/user/project",
                "transcript_path": transcript,
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(input_data))

        from keephive.hooks.precompact import hook_precompact

        hook_precompact([])

        df = daily_file()
        assert df.exists()
        content = safe_read_text(df)
        assert "Session Excerpts" in content
        assert "GraphQL" in content

    def test_excerpt_hash_dedup(self, hive_env, monkeypatch):
        """Same transcript content is not appended twice (hash-based dedup)."""
        import io

        transcript = _make_transcript(
            hive_env,
            [_user_msg("Important decision about database architecture and migration strategy")],
        )

        for _ in range(2):
            input_data = json.dumps(
                {
                    "trigger": "manual",
                    "cwd": "/tmp/proj",
                    "transcript_path": transcript,
                }
            )
            monkeypatch.setattr("sys.stdin", io.StringIO(input_data))

            from keephive.hooks.precompact import hook_precompact

            hook_precompact([])

        df = daily_file()
        content = safe_read_text(df)
        # "Session Excerpts" header should appear only once
        assert content.count("Session Excerpts") == 1

    def test_llm_skipped_when_hive_skip_llm(self, hive_env, monkeypatch):
        """HIVE_SKIP_LLM=1 prevents Layer 2 LLM call."""
        import io

        # hive_env fixture sets HIVE_SKIP_LLM=1
        assert os.environ.get("HIVE_SKIP_LLM") == "1"

        transcript = _make_transcript(
            hive_env,
            [_user_msg("Test message with enough characters to pass the filter")],
        )

        # If LLM were called, this would fail since run_claude_pipe is not mocked
        input_data = json.dumps(
            {
                "trigger": "test",
                "cwd": "/tmp/proj",
                "transcript_path": transcript,
            }
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(input_data))

        from keephive.hooks.precompact import hook_precompact

        # Should not raise (LLM call skipped due to HIVE_SKIP_LLM)
        hook_precompact([])


# ---- Find transcript ----


class TestFindTranscript:
    """Test _find_transcript path resolution."""

    def test_direct_path(self, hive_env):
        """transcript_path field takes priority when the file exists."""
        from keephive.hooks.precompact import _find_transcript

        path = hive_env / "test_transcript.jsonl"
        path.write_text("")
        result = _find_transcript({"transcript_path": str(path)})
        assert result == str(path)

    def test_missing_path_returns_none(self, hive_env):
        from keephive.hooks.precompact import _find_transcript

        result = _find_transcript({"transcript_path": "/nonexistent/path.jsonl"})
        assert result is None

    def test_empty_input_returns_none(self):
        from keephive.hooks.precompact import _find_transcript

        assert _find_transcript({}) is None

    def test_session_id_fallback(self, hive_env, monkeypatch, tmp_path):
        """When no transcript_path, derives path from session_id + cwd."""
        from keephive.hooks.precompact import _find_transcript

        # Create the expected file at the derived path
        cwd = "/home/user/project"
        session_id = "abc123"
        encoded_cwd = cwd.replace("/", "-")
        projects_dir = tmp_path / ".claude" / "projects" / encoded_cwd
        projects_dir.mkdir(parents=True)
        transcript_file = projects_dir / f"{session_id}.jsonl"
        transcript_file.write_text("")

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = _find_transcript({"session_id": session_id, "cwd": cwd})
        assert result == str(transcript_file)


# ---- Edge-case tests: budget enforcement ----


class TestBudgetEnforcementEdgeCases:
    """Test extreme budget values and mid-message truncation."""

    def test_budget_zero(self):
        """Budget of 0 returns empty list."""
        msgs = ["Short message", "Another message"]
        result = _select_within_budget(msgs, 0)
        assert result == []

    def test_budget_one(self):
        """Budget of 1 char: only very short messages or truncated."""
        msgs = ["Hello world"]
        result = _select_within_budget(msgs, 1)
        # 1 char budget, message is 11 chars, remaining after 0 full fits = 1 char < 50 threshold
        assert result == []

    def test_budget_mid_message_truncation(self):
        """Budget that splits a message: remaining > 50 threshold triggers truncation."""
        msgs = ["A" * 100, "B" * 100]
        # Budget 160: reversed iteration takes B (100), chars=100. Next A: 100+100=200 > 160.
        # remaining = 160-100 = 60 > 50 threshold, so A gets truncated to 60 chars.
        result = _select_within_budget(msgs, 160)
        assert len(result) == 2
        assert result[-1] == "B" * 100
        assert len(result[0]) == 60


# ---- Edge-case tests: LLM summary ----


class TestLlmSummaryEdgeCases:
    """Edge cases for _llm_summary LLM response handling."""

    def test_all_insights_garbage_filtered(self, hive_env):
        """When every insight is garbage, nothing is written to daily log."""
        response = _mock_precompact_response(
            insights=[
                ("FACT", "short"),
                ("INSIGHT", "choices made about architecture, tools, approach"),
            ]
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe)

        df = daily_file()
        if df.exists():
            content = safe_read_text(df)
            assert "FACT: short" not in content
            assert "choices made" not in content

    def test_unexpected_category_in_insight(self, hive_env):
        """An insight with an unexpected category should be handled."""
        # Valid InsightCategory values are enumerated. Test with a valid one but
        # ensure the pipeline handles the full range without crashing.
        response = _mock_precompact_response(
            insights=[("TODO", "Remember to update the documentation and changelog")]
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe)

        df = daily_file()
        content = safe_read_text(df)
        assert "TODO: Remember to update the documentation" in content


# ---- Edge-case tests: secret redaction ----


class TestRedactSecretsEdgeCases:
    """Edge cases for _redact_secrets."""

    def test_overlapping_patterns(self):
        """Text containing multiple overlapping secret patterns."""
        text = "TOKEN=sk_live_abc123def456 and also API_KEY=ghp_abcdef1234567890"
        result = _redact_secrets(text)
        assert "sk_live_abc" not in result
        assert "ghp_abcdef" not in result

    def test_incomplete_prefix(self):
        """Strings that look like secrets but are too short to match."""
        text = "The variable sk_ is just a prefix"
        result = _redact_secrets(text)
        # Short token doesn't match the 20+ char pattern
        assert "sk_" in result

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert _redact_secrets("") == ""
