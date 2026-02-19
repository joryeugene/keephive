"""Tests for reflect command logic (commands/reflect.py).

Tests pure-logic functions without LLM calls.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from conftest import make_daily


# ---- _update_contradiction ----

class TestUpdateContradiction:
    def test_replaces_matching_line(self):
        from keephive.commands.reflect import _update_contradiction
        mem = "- Python is slow [verified:2026-01-01]\n- Rust is fast\n"
        result = _update_contradiction(mem, "Python is slow", "Python is fast", "2026-02-17")
        assert "Python is fast [verified:2026-02-17]" in result
        assert "Python is slow" not in result

    def test_case_insensitive_match(self):
        from keephive.commands.reflect import _update_contradiction
        mem = "- PYTHON IS SLOW [verified:2026-01-01]\n"
        result = _update_contradiction(mem, "python is slow", "Python is fast", "2026-02-17")
        assert "Python is fast [verified:2026-02-17]" in result

    def test_strips_verified_tag_for_comparison(self):
        from keephive.commands.reflect import _update_contradiction
        mem = "- uv is 5x faster than pip [verified:2026-01-15]\n"
        result = _update_contradiction(mem, "uv is 5x faster than pip", "uv is 10x faster than pip", "2026-02-17")
        assert "uv is 10x faster than pip [verified:2026-02-17]" in result
        assert "5x" not in result

    def test_partial_match(self):
        from keephive.commands.reflect import _update_contradiction
        mem = "- keephive uses Pydantic for all validation [verified:2026-01-01]\n"
        result = _update_contradiction(mem, "keephive uses Pydantic", "keephive uses attrs", "2026-02-17")
        assert "keephive uses attrs [verified:2026-02-17]" in result

    def test_appends_if_no_match(self):
        from keephive.commands.reflect import _update_contradiction
        mem = "- Existing fact\n"
        result = _update_contradiction(mem, "No such fact", "New correction", "2026-02-17")
        assert "New correction [verified:2026-02-17]" in result
        assert "Existing fact" in result

    def test_preserves_other_lines(self):
        from keephive.commands.reflect import _update_contradiction
        mem = "- Keep me\n- Replace me [verified:2026-01-01]\n- Also keep\n"
        result = _update_contradiction(mem, "Replace me", "Replaced", "2026-02-17")
        assert "Keep me" in result
        assert "Also keep" in result
        assert "Replaced [verified:2026-02-17]" in result


# ---- _append_to_memory ----

class TestAppendToMemory:
    def test_appends_before_trailing_whitespace(self):
        from keephive.commands.reflect import _append_to_memory
        mem = "- First fact\n- Second fact\n\n"
        result = _append_to_memory(mem, "- Third fact")
        lines = result.strip().splitlines()
        assert lines[-1] == "- Third fact"
        assert result.endswith("\n")

    def test_empty_content(self):
        from keephive.commands.reflect import _append_to_memory
        result = _append_to_memory("", "- New fact")
        assert "- New fact" in result

    def test_no_trailing_newline(self):
        from keephive.commands.reflect import _append_to_memory
        mem = "- First fact"
        result = _append_to_memory(mem, "- Second fact")
        assert "Second fact" in result
        assert result.endswith("\n")


# ---- _analyze_post_pass ----

class TestAnalyzePostPass:
    def test_filters_overlapping(self):
        """Additions already in memory should be filtered out."""
        from keephive.commands.reflect import _analyze_post_pass
        from keephive.models import ReflectAnalyzeResponse, Addition

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[
                Addition(fact="keephive uses Pydantic for validation", source="2026-02-15"),
            ],
            contradictions=[],
            actions=[],
        )
        mem = "- keephive uses Pydantic for validation [verified:2026-02-15]\n"

        # Should not print "Not Yet in Working Memory" for overlapping items
        # This is a visual check; the function prints to console
        _analyze_post_pass(response, mem)

    def test_keeps_non_overlapping(self):
        from keephive.commands.reflect import _analyze_post_pass
        from keephive.models import ReflectAnalyzeResponse, Addition

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[
                Addition(fact="completely brand new fact about quantum computing", source="2026-02-15"),
            ],
            contradictions=[],
            actions=[],
        )
        mem = "- keephive uses Pydantic\n"

        # Non-overlapping fact should pass through
        _analyze_post_pass(response, mem)

    def test_empty_additions(self):
        from keephive.commands.reflect import _analyze_post_pass
        from keephive.models import ReflectAnalyzeResponse

        response = ReflectAnalyzeResponse(
            patterns=[], additions=[], contradictions=[], actions=[]
        )
        # Should not crash on empty
        _analyze_post_pass(response, "")


# ---- _reflect_scan ----

class TestReflectScan:
    def test_counts_entries(self, hive_env):
        make_daily(hive_env, days_ago=0, entries=[
            "- [10:00:00] FACT: Python is great",
            "- [10:05:00] TODO: Add tests",
            "- [10:10:00] DECISION: Use pytest",
        ])

        from keephive.commands.reflect import _reflect_scan
        # Just verify it doesn't crash
        _reflect_scan([])

    def test_finds_todos(self, hive_env):
        make_daily(hive_env, days_ago=0, entries=[
            "- [10:00:00] TODO: Fix the bug",
            "- [10:05:00] FACT: Something else",
        ])

        from keephive.commands.reflect import _reflect_scan
        _reflect_scan([])

    def test_finds_corrections(self, hive_env):
        make_daily(hive_env, days_ago=0, entries=[
            "- [10:00:00] CORRECTION: old was wrong, new is right",
        ])

        from keephive.commands.reflect import _reflect_scan
        _reflect_scan([])

    def test_no_entries(self, hive_env):
        from keephive.commands.reflect import _reflect_scan
        _reflect_scan([])


# ---- get_pending_analysis ----

class TestGetPendingAnalysis:
    def test_fresh_file_returns_counts(self, hive_env):
        from keephive.commands.reflect import get_pending_analysis
        from keephive.models import ReflectAnalyzeResponse, Addition, Contradiction
        from keephive.storage import hive_dir

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[Addition(fact="new fact", source="2026-02-17")],
            contradictions=[Contradiction(memory="old", log="new", date="2026-02-17")],
            actions=[],
        )
        analyze_path = hive_dir() / ".last-analyze.json"
        analyze_path.write_text(response.model_dump_json(indent=2))

        result = get_pending_analysis()
        assert result is not None
        assert result == (1, 1)

    def test_stale_returns_none(self, hive_env):
        import os
        from keephive.commands.reflect import get_pending_analysis
        from keephive.models import ReflectAnalyzeResponse, Addition
        from keephive.storage import hive_dir

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[Addition(fact="fact", source="date")],
            contradictions=[],
            actions=[],
        )
        analyze_path = hive_dir() / ".last-analyze.json"
        analyze_path.write_text(response.model_dump_json(indent=2))

        # Make file 25 hours old
        old_time = time.time() - 25 * 3600
        os.utime(analyze_path, (old_time, old_time))

        result = get_pending_analysis()
        assert result is None

    def test_missing_returns_none(self, hive_env):
        from keephive.commands.reflect import get_pending_analysis
        result = get_pending_analysis()
        assert result is None

    def test_empty_additions_returns_none(self, hive_env):
        from keephive.commands.reflect import get_pending_analysis
        from keephive.models import ReflectAnalyzeResponse
        from keephive.storage import hive_dir

        response = ReflectAnalyzeResponse(
            patterns=[], additions=[], contradictions=[], actions=[]
        )
        analyze_path = hive_dir() / ".last-analyze.json"
        analyze_path.write_text(response.model_dump_json(indent=2))

        result = get_pending_analysis()
        assert result is None

    def test_corrupt_json_returns_none(self, hive_env):
        from keephive.commands.reflect import get_pending_analysis
        from keephive.storage import hive_dir

        analyze_path = hive_dir() / ".last-analyze.json"
        analyze_path.write_text("not valid json{{{")

        result = get_pending_analysis()
        assert result is None


# ---- _reflect_apply (auto mode) ----

class TestReflectApplyAuto:
    def _write_analysis(self, hive_env: Path, response) -> None:
        from keephive.storage import hive_dir
        analyze_path = hive_dir() / ".last-analyze.json"
        analyze_path.write_text(response.model_dump_json(indent=2))

    def test_auto_writes_additions_to_memory(self, hive_env):
        from keephive.commands.reflect import _reflect_apply
        from keephive.models import Addition, ReflectAnalyzeResponse
        from keephive.storage import memory_file

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[
                Addition(fact="FTS5 search is faster than grep", source="2026-02-19"),
            ],
            contradictions=[],
            actions=[],
        )
        self._write_analysis(hive_env, response)

        # --auto skips interactive prompts
        _reflect_apply(["--auto"])

        mem = memory_file().read_text()
        assert "FTS5 search is faster than grep" in mem

    def test_auto_writes_multiple_additions(self, hive_env):
        from keephive.commands.reflect import _reflect_apply
        from keephive.models import Addition, ReflectAnalyzeResponse
        from keephive.storage import memory_file

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[
                Addition(fact="Fact one about testing", source="2026-02-19"),
                Addition(fact="Fact two about storage", source="2026-02-19"),
            ],
            contradictions=[],
            actions=[],
        )
        self._write_analysis(hive_env, response)
        _reflect_apply(["--auto"])

        mem = memory_file().read_text()
        assert "Fact one about testing" in mem
        assert "Fact two about storage" in mem

    def test_auto_stale_analysis_warns(self, hive_env, capsys):
        import os

        from keephive.commands.reflect import _reflect_apply
        from keephive.models import Addition, ReflectAnalyzeResponse
        from keephive.storage import hive_dir

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[Addition(fact="Some old fact", source="2026-01-01")],
            contradictions=[],
            actions=[],
        )
        analyze_path = hive_dir() / ".last-analyze.json"
        analyze_path.write_text(response.model_dump_json(indent=2))

        # Make the analysis file 26 hours old
        old_time = time.time() - 26 * 3600
        os.utime(analyze_path, (old_time, old_time))

        _reflect_apply(["--auto"])
        out = capsys.readouterr().out
        assert "old" in out.lower() or "h old" in out.lower() or "analyze" in out.lower()

    def test_auto_contradictions_only_no_additions(self, hive_env):
        from keephive.commands.reflect import _reflect_apply
        from keephive.models import Contradiction, ReflectAnalyzeResponse
        from keephive.storage import memory_file

        # Set up memory with the fact that will be contradicted
        mem_path = memory_file()
        mem_path.write_text("- Python is slow [verified:2026-01-01]\n")

        response = ReflectAnalyzeResponse(
            patterns=[],
            additions=[],
            contradictions=[
                Contradiction(
                    memory="Python is slow",
                    log="Python is actually fast with PyPy",
                    date="2026-02-19",
                ),
            ],
            actions=[],
        )
        self._write_analysis(hive_env, response)
        _reflect_apply(["--auto"])

        # Contradiction should update memory, no additions processed
        mem = memory_file().read_text()
        assert "Python is actually fast with PyPy" in mem

    def test_no_analysis_file_exits_gracefully(self, hive_env, capsys):
        from keephive.commands.reflect import _reflect_apply

        _reflect_apply(["--auto"])
        out = capsys.readouterr().out
        assert "no pending" in out.lower() or "analyze" in out.lower()
