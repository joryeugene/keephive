"""Integration tests for multi-step state machines in keephive.

These tests exercise real function calls in sequence, verifying that
state flows correctly across operations. No mocking of internal functions;
only the LLM pipe is mocked where needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keephive.hooks.precompact import (
    _extract_excerpts,
    _is_duplicate_insight,
    _llm_summary,
)
from keephive.models import (
    Insight,
    InsightCategory,
    MemoryUpdate,
    PreCompactResponse,
)
from keephive.storage import (
    _dedup_todos,
    append_to_daily,
    collect_todos,
    daily_file,
    due_recurring,
    get_stale_facts,
    mark_recurring_done,
    memory_file,
    normalize_memory,
    open_todos,
    read_memory,
    recurring_file,
    safe_read_text,
)


def _make_transcript(hive_env: Path, messages: list[dict]) -> str:
    """Write a JSONL transcript file and return its path."""
    path = hive_env / "transcript.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return str(path)


# ---- TODO lifecycle ----


@pytest.mark.integration
class TestTodoLifecycle:
    """Full TODO lifecycle: add -> list -> done -> add same text -> no dedup interference."""

    def test_add_list_done_readd(self, hive_env, monkeypatch):
        """Add a TODO, verify it appears, mark done, verify gone. DONE persists across days."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")

        # Add a TODO via daily log
        append_to_daily("- [10:00:00] TODO: Refactor authentication module")

        # Verify it appears in open todos
        todos = open_todos()
        texts = [t[2] for t in todos]
        assert "Refactor authentication module" in texts

        # Mark done via daily log
        append_to_daily("- [11:00:00] DONE: Refactor authentication module")

        # Verify it no longer appears in open todos
        todos = open_todos()
        texts = [t[2] for t in todos]
        assert "Refactor authentication module" not in texts

        # Re-add the same text on a different day
        monkeypatch.setenv("HIVE_DATE", "2026-01-16")
        append_to_daily("- [10:00:00] TODO: Refactor authentication module")

        # DONE entries persist across the 30-day window, so this is still filtered
        todos = open_todos()
        texts = [t[2] for t in todos]
        assert "Refactor authentication module" not in texts

        # But a different task on the new day IS visible
        append_to_daily("- [10:30:00] TODO: Write migration scripts for database")
        todos = open_todos()
        texts = [t[2] for t in todos]
        assert "Write migration scripts for database" in texts

    def test_dedup_across_days(self, hive_env, monkeypatch):
        """Same TODO added on two different days gets deduped to the most recent."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")

        day1 = hive_env / "daily" / "2026-01-14.md"
        day1.write_text(
            "# Daily Log: 2026-01-14\n\n- [10:00:00] TODO: Update deployment configuration\n"
        )

        day2 = hive_env / "daily" / "2026-01-15.md"
        day2.write_text(
            "# Daily Log: 2026-01-15\n\n- [09:00:00] TODO: Update deployment configuration\n"
        )

        todos, _ = collect_todos()
        deduped = _dedup_todos(todos)
        matching = [t for t in deduped if "deployment configuration" in t[2].lower()]
        assert len(matching) == 1
        assert matching[0][0] == "2026-01-15"  # most recent wins

    def test_done_case_insensitive_match(self, hive_env, monkeypatch):
        """DONE matching against TODO is case-insensitive."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] TODO: Write Unit Tests\n"
            "- [11:00:00] DONE: write unit tests\n"
        )

        todos = open_todos()
        texts = [t[2] for t in todos]
        assert not any("unit tests" in t.lower() for t in texts)


# ---- Memory normalization survival ----


@pytest.mark.integration
class TestMemoryNormalizationSurvival:
    """After normalize_memory(), recall still finds facts, stale detection still works."""

    def test_normalize_preserves_stale_detection(self, hive_env, monkeypatch):
        """normalize_memory cleans up, but stale facts still detected correctly."""
        monkeypatch.setenv("HIVE_DATE", "2026-03-01")
        monkeypatch.delenv("HIVE_STALE_DAYS", raising=False)

        mf = memory_file()
        mf.write_text(
            "# Working Memory\n\n"
            "- - FACT: Double dash old fact [verified:2020-01-01]\n"
            "- FACT: Fresh fact [verified:2026-02-28]\n"
            "- FACT: duplicate line [verified:2026-01-15]\n"
            "- FACT: duplicate line [verified:2026-01-10]\n"
        )

        # Normalize fixes malformed prefix and deduplicates
        stats = normalize_memory(mf)
        assert stats["malformed_prefix"] == 1
        assert stats["deduped"] == 1

        # After normalization, stale detection still works
        stale = get_stale_facts()
        stale_texts = [s[1] for s in stale]
        assert any("Double dash old fact" in t for t in stale_texts)
        assert not any("Fresh fact" in t for t in stale_texts)

    def test_normalize_preserves_read_memory(self, hive_env):
        """normalize_memory doesn't destroy content readable by read_memory."""
        mf = memory_file()
        mf.write_text(
            "# Working Memory\n\n"
            "- FACT: important fact [verified:2026-01-15]\n"
            "- FACT: another fact [verified:2026-01-15] [verified:2026-02-01]\n"
        )

        normalize_memory(mf)
        mem = read_memory()
        assert "important fact" in mem
        assert "another fact" in mem
        # Double tag should be fixed
        assert mem.count("[verified:") == 2  # one per line


# ---- Recurring done across days ----


@pytest.mark.integration
class TestRecurringDoneAcrossDays:
    """Mark recurring done on day 1, check not-due on day 2, check due again on day N+1."""

    def test_daily_recurring_lifecycle(self, hive_env, monkeypatch):
        """Daily task: mark done today, not due same day, due next day (elapsed >= interval)."""
        rf = recurring_file()
        rf.write_text("# Recurring Tasks\n\n- [daily] Review pull requests\n\n## Last Completed\n")

        # Day 1: mark done
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        result = mark_recurring_done("pull request")
        assert result is not None
        assert result[0] == "Review pull requests"

        # Day 1: should not be due (just completed, elapsed=0 < interval=1)
        due = due_recurring()
        texts = [t[1] for t in due]
        assert "Review pull requests" not in texts

        # Day 2: now due (elapsed=1 >= interval=1, so overdue=0 which is >= 0)
        monkeypatch.setenv("HIVE_DATE", "2026-01-16")
        due = due_recurring()
        texts = [t[1] for t in due]
        assert "Review pull requests" in texts

    def test_weekly_recurring_lifecycle(self, hive_env, monkeypatch):
        """Weekly task done on day 1 is due again on day 7 (elapsed >= interval)."""
        rf = recurring_file()
        rf.write_text("# Recurring Tasks\n\n- [weekly] Team standup review\n\n## Last Completed\n")

        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        mark_recurring_done("standup review")

        # Day 6: not yet due (elapsed=6 < interval=7)
        monkeypatch.setenv("HIVE_DATE", "2026-01-21")
        due = due_recurring()
        texts = [t[1] for t in due]
        assert "Team standup review" not in texts

        # Day 7: due (elapsed=7 >= interval=7, overdue=0 which is >= 0)
        monkeypatch.setenv("HIVE_DATE", "2026-01-22")
        due = due_recurring()
        texts = [t[1] for t in due]
        assert "Team standup review" in texts


# ---- Precompact full pipeline ----


@pytest.mark.integration
class TestPrecompactFullPipeline:
    """Layer 1 extracts -> dedup against log -> Layer 2 writes -> memory updated.

    Uses real _extract_excerpts + _is_duplicate_insight with mock pipe for LLM only.
    """

    def test_extract_then_summarize_pipeline(self, hive_env, monkeypatch):
        """Full pipeline: transcript -> Layer 1 extraction -> Layer 2 write to daily + memory."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")

        # Create a transcript with meaningful content
        transcript = _make_transcript(
            hive_env,
            [
                {
                    "type": "user",
                    "message": {
                        "content": "We should migrate from SQLite to PostgreSQL for better concurrency"
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "PostgreSQL would be a significant upgrade here. Its MVCC "
                                    "implementation handles concurrent writes much better than "
                                    "SQLite's file-level locking, and you get JSONB support for "
                                    "semi-structured data with indexing capabilities."
                                ),
                            }
                        ]
                    },
                },
            ],
        )

        # Layer 1: real extraction
        excerpts = _extract_excerpts(transcript, 4000)
        assert "PostgreSQL" in excerpts
        assert "SQLite" in excerpts or "migrate" in excerpts

        # Layer 2: mock pipe, real write logic
        response = PreCompactResponse(
            insights=[
                Insight(
                    category=InsightCategory.DECISION,
                    description="Migrating from SQLite to PostgreSQL for better write concurrency",
                ),
                Insight(
                    category=InsightCategory.FACT,
                    description="PostgreSQL MVCC handles concurrent writes better than SQLite file locking",
                ),
            ],
            memory_updates=[
                MemoryUpdate(
                    action="add",
                    text="Project database is PostgreSQL (migrated from SQLite)",
                ),
            ],
            rule_suggestions=[],
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary(excerpts, pipe_fn=mock_pipe, project_name="webapp")

        # Verify daily log has insights with project attribution
        df = daily_file()
        content = safe_read_text(df)
        assert "DECISION: Migrating from SQLite" in content
        assert "FACT: PostgreSQL MVCC" in content
        assert "[project:webapp]" in content

        # Verify fact was queued to pending (not written directly to memory)
        from keephive.hooks.precompact import _pending_facts_path
        from keephive.storage import safe_read_text as _srt

        pf_content = _srt(_pending_facts_path())
        assert "Project database is PostgreSQL" in pf_content

    def test_dedup_prevents_double_write(self, hive_env, monkeypatch):
        """Running the same insight through Layer 2 twice only writes once."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")

        insight_text = "Redis sorted sets provide O(log N) range queries for leaderboards"

        response = PreCompactResponse(
            insights=[Insight(category=InsightCategory.FACT, description=insight_text)],
            memory_updates=[],
            rule_suggestions=[],
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        # Write once
        _llm_summary("excerpts", pipe_fn=mock_pipe)

        # Verify it was written
        df = daily_file()
        content = safe_read_text(df)
        assert insight_text in content

        # Write again (same insight)
        _llm_summary("excerpts", pipe_fn=mock_pipe)

        # Should still appear only once (dedup by _is_duplicate_insight using real SequenceMatcher)
        content = safe_read_text(df)
        assert content.count(insight_text) == 1

    def test_real_dedup_uses_sequence_matcher(self, hive_env, monkeypatch):
        """Dedup uses actual SequenceMatcher, not mocked _is_duplicate_insight."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")

        # Write an existing insight to daily log
        append_to_daily(
            "- [09:00:00] FACT: PostgreSQL JSONB indexes outperform MongoDB for structured queries"
        )

        # Now check that a fuzzy variant is detected as duplicate
        df = daily_file()
        assert _is_duplicate_insight(
            df, "PostgreSQL JSONB indexes outperform MongoDB for structured query workloads"
        )

        # But a genuinely different insight is not
        assert not _is_duplicate_insight(
            df, "Redis pub/sub is useful for real-time event streaming between microservices"
        )

    def test_memory_add_dedup_in_pipeline(self, hive_env, monkeypatch):
        """Memory ADD skips facts that already exist (fuzzy match against memory.md)."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")

        # Fixture memory already has "Python is great" and "keephive uses Pydantic"
        response = PreCompactResponse(
            insights=[],
            memory_updates=[
                MemoryUpdate(action="add", text="Python is great"),  # already exists
                MemoryUpdate(
                    action="add",
                    text="GraphQL reduces over-fetching compared to REST APIs",
                ),
            ],
            rule_suggestions=[],
        )

        def mock_pipe(prompt, model, stdin_text=None):
            return response

        _llm_summary("excerpts", pipe_fn=mock_pipe)

        mem = read_memory()
        # "Python is great" should not be duplicated in memory
        assert mem.count("Python is great") == 1

        # New fact should be queued in pending, not written directly to memory
        from keephive.hooks.precompact import _pending_facts_path
        from keephive.storage import safe_read_text as _srt

        pf_content = _srt(_pending_facts_path())
        assert "GraphQL reduces over-fetching" in pf_content
        # Duplicate should NOT appear in pending
        assert "Python is great" not in pf_content
