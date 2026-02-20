"""Tests for hive l summarize — LLM and fast paths."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestLogSummarizeSkip:
    """Fast tests: HIVE_SKIP_LLM guard and missing log guard."""

    def test_skip_llm_prints_message(self, hive_env: Path, capsys):
        from keephive.commands.log import _log_summarize
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        append_to_daily("- [10:00:00] FACT: Python is great")
        _log_summarize()
        out = capsys.readouterr().out
        assert "HIVE_SKIP_LLM" in out or "skipping" in out.lower()

    def test_empty_log_exits_gracefully(self, hive_env: Path, capsys):
        from keephive.storage import ensure_daily

        ensure_daily()  # creates header-only file: "# Daily Log: DATE\n\n"
        from keephive.commands.log import _log_summarize

        _log_summarize()
        # Header-only log has no "- " entries — must print "no entries" and return
        out = capsys.readouterr().out
        assert "no entries" in out.lower() or "skipping" in out.lower()

    def test_summarize_dispatch_from_cmd_log(self, hive_env: Path, capsys):
        from keephive.commands.log import cmd_log

        cmd_log(["summarize"])
        out = capsys.readouterr().out
        # With HIVE_SKIP_LLM=1 this should hit the skip path, not crash
        assert out  # some output produced


@pytest.mark.llm
class TestLogSummarizeLLM:
    """Real LLM tests. Run with: uv run pytest -m llm -v -o 'addopts='"""

    def test_summarize_returns_bullets(self, llm_hive_env: Path, capsys):
        """hive l summarize produces 3-5 bullets from a real log via haiku."""
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        append_to_daily("- [09:00:00] FACT: SQLite FTS5 is built into Python stdlib")
        append_to_daily("- [09:05:00] DECISION: Use os.replace() for atomic writes")
        append_to_daily("- [09:10:00] CORRECTION: check_data() was wrong to require rules.md")
        append_to_daily("- [09:15:00] TODO: Write LLM tests for summarize feature")
        append_to_daily("- [09:20:00] INSIGHT: Strip-then-append is idempotent for verified tags")

        from keephive.commands.log import _log_summarize

        _log_summarize()
        out = capsys.readouterr().out

        assert "summary" in out.lower() or "•" in out, f"Expected summary output, got: {out!r}"
        bullet_count = out.count("• ")
        assert 2 <= bullet_count <= 6, f"Expected 3-5 bullets, got {bullet_count}: {out!r}"
        # Verify at least one specific term from the log appears
        assert any(
            term in out for term in ["FTS", "SQLite", "atomic", "rules", "idempotent", "os.replace"]
        ), f"Expected specific content from log in summary, got: {out!r}"
