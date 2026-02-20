"""Tests for recall context lines and knowledge attribution."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("NO_COLOR", "1")


def _run(args: list[str], hive_home: str) -> subprocess.CompletedProcess:
    env = {
        "HIVE_SKIP_LLM": "1",
        "NO_COLOR": "1",
        "HIVE_HOME": hive_home,
        "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin:" + (Path.home() / ".local/bin").as_posix(),
    }
    return subprocess.run(
        [sys.executable, "-m", "keephive"] + args,
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# _get_context_lines
# ---------------------------------------------------------------------------

class TestGetContextLines:
    def test_returns_prev_and_next(self, tmp_path):
        f = tmp_path / "daily.md"
        f.write_text(
            "# Daily Log\n"
            "- [10:00:00] FACT: first entry\n"
            "- [10:01:00] FACT: target entry\n"
            "- [10:02:00] FACT: third entry\n"
        )
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(f, "- [10:01:00] FACT: target entry")
        assert prev == "- [10:00:00] FACT: first entry"
        assert nxt == "- [10:02:00] FACT: third entry"

    def test_skips_blank_lines_between_entries(self, tmp_path):
        f = tmp_path / "daily.md"
        f.write_text(
            "- [10:00:00] FACT: first\n"
            "\n"
            "- [10:01:00] FACT: target\n"
            "\n"
            "- [10:02:00] FACT: third\n"
        )
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(f, "- [10:01:00] FACT: target")
        assert prev == "- [10:00:00] FACT: first"
        assert nxt == "- [10:02:00] FACT: third"

    def test_skips_heading_lines(self, tmp_path):
        f = tmp_path / "daily.md"
        f.write_text(
            "# Daily Log\n"
            "- [10:01:00] FACT: target\n"
            "## Section\n"
            "- [10:02:00] FACT: after section\n"
        )
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(f, "- [10:01:00] FACT: target")
        assert prev is None  # heading is skipped, nothing useful before
        assert nxt == "- [10:02:00] FACT: after section"

    def test_skips_html_comment_lines(self, tmp_path):
        f = tmp_path / "daily.md"
        f.write_text(
            "<!-- generated -->\n"
            "- [10:01:00] FACT: target\n"
            "<!-- end -->\n"
            "- [10:02:00] FACT: after\n"
        )
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(f, "- [10:01:00] FACT: target")
        assert prev is None  # comment skipped
        assert nxt == "- [10:02:00] FACT: after"

    def test_returns_none_on_missing_file(self, tmp_path):
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(tmp_path / "nonexistent.md", "anything")
        assert prev is None
        assert nxt is None

    def test_returns_none_when_needle_not_found(self, tmp_path):
        f = tmp_path / "daily.md"
        f.write_text("- [10:00:00] FACT: something else entirely\n")
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(f, "needle not in file at all")
        assert prev is None
        assert nxt is None

    def test_first_line_has_no_prev(self, tmp_path):
        f = tmp_path / "daily.md"
        f.write_text(
            "- [10:00:00] FACT: first\n"
            "- [10:01:00] FACT: second\n"
        )
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(f, "- [10:00:00] FACT: first")
        assert prev is None
        assert nxt == "- [10:01:00] FACT: second"

    def test_last_line_has_no_next(self, tmp_path):
        f = tmp_path / "daily.md"
        f.write_text(
            "- [10:00:00] FACT: first\n"
            "- [10:01:00] FACT: last\n"
        )
        from keephive.commands.remember import _get_context_lines
        prev, nxt = _get_context_lines(f, "- [10:01:00] FACT: last")
        assert prev == "- [10:00:00] FACT: first"
        assert nxt is None


# ---------------------------------------------------------------------------
# _daily_path_for_result
# ---------------------------------------------------------------------------

class TestDailyPathForResult:
    def test_daily_tier_returns_existing_path(self, hive_env):
        today = date.today().isoformat()
        daily_file = hive_env / "daily" / f"{today}.md"
        daily_file.write_text("# Daily Log\n")

        from keephive.commands.remember import _daily_path_for_result
        result = {"tier": "daily", "date": today, "line": "test"}
        path = _daily_path_for_result(result)
        assert path == daily_file

    def test_archive_tier_returns_existing_path(self, hive_env):
        past = (date.today() - timedelta(days=60)).isoformat()
        archive_file = hive_env / "archive" / f"{past}.md"
        archive_file.write_text("# Archive\n")

        from keephive.commands.remember import _daily_path_for_result
        result = {"tier": "archive", "date": past, "line": "test"}
        path = _daily_path_for_result(result)
        assert path == archive_file

    def test_missing_date_returns_none(self, hive_env):
        from keephive.commands.remember import _daily_path_for_result
        assert _daily_path_for_result({"tier": "daily", "line": "test"}) is None

    def test_nonexistent_file_returns_none(self, hive_env):
        from keephive.commands.remember import _daily_path_for_result
        result = {"tier": "daily", "date": "2020-01-01", "line": "test"}
        assert _daily_path_for_result(result) is None

    def test_working_tier_returns_none(self, hive_env):
        from keephive.commands.remember import _daily_path_for_result
        result = {"tier": "working", "date": date.today().isoformat(), "line": "test"}
        assert _daily_path_for_result(result) is None

    def test_knowledge_tier_returns_none(self, hive_env):
        from keephive.commands.remember import _daily_path_for_result
        result = {"tier": "knowledge", "date": date.today().isoformat(), "line": "test"}
        assert _daily_path_for_result(result) is None


# ---------------------------------------------------------------------------
# Knowledge attribution: file field in search results
# ---------------------------------------------------------------------------

class TestKnowledgeAttribution:
    def test_exact_knowledge_hits_include_file(self, hive_env):
        guide = hive_env / "knowledge" / "guides" / "myguide.md"
        guide.write_text("# My Guide\n\nfts5xyzunique virtual table implementation\n")

        from keephive.commands.remember import _search_all_tiers
        results = _search_all_tiers("fts5xyzunique virtual table")
        knowledge_hits = [r for r in results if r["tier"] == "knowledge"]
        assert len(knowledge_hits) > 0, "Expected knowledge hits"
        for hit in knowledge_hits:
            assert "file" in hit, f"Knowledge hit missing 'file': {hit}"
            assert "myguide" in hit["file"]

    def test_knowledge_file_stem_matches_guide_name(self, hive_env):
        guide = hive_env / "knowledge" / "guides" / "keephive-guide.md"
        guide.write_text("# Keephive Guide\n\nreplicuniq content for testing\n")

        from keephive.commands.remember import _search_all_tiers
        results = _search_all_tiers("replicuniq content")
        knowledge_hits = [r for r in results if r["tier"] == "knowledge"]
        assert len(knowledge_hits) > 0
        stems = [Path(h["file"]).stem for h in knowledge_hits]
        assert any("keephive-guide" in s for s in stems)

    def test_knowledge_guide_name_in_recall_output(self, hive_env):
        """Guide stem appears in `hive rc` output for knowledge hits."""
        guide = hive_env / "knowledge" / "guides" / "specialguide.md"
        guide.write_text("# Special Guide\n\nxuniqterm987 special content here\n")

        r = _run(["rc", "xuniqterm987"], hive_home=str(hive_env))
        assert r.returncode == 0
        assert "specialguide" in r.stdout, (
            f"Guide name 'specialguide' should appear in output. Got:\n{r.stdout}"
        )

    def test_json_output_unchanged_structure(self, hive_env):
        """--json output still has correct structure; no display fields leak in."""
        guide = hive_env / "knowledge" / "guides" / "testguide.md"
        guide.write_text("# Test\n\njsoncheckterm999 value\n")

        r = _run(["rc", "jsoncheckterm999", "--json"], hive_home=str(hive_env))
        assert r.returncode == 0
        import json
        data = json.loads(r.stdout)
        assert "query" in data
        assert "results" in data
        assert "count" in data
        assert data["count"] > 0
        # file field comes through in JSON (it's part of the result struct)
        hits = [res for res in data["results"] if res["tier"] == "knowledge"]
        assert len(hits) > 0
        for hit in hits:
            assert "file" in hit
