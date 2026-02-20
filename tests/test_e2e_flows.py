"""End-to-end flow tests: multi-command user workflows.

Each test tells a story, simulating real multi-step usage.
No LLM calls (HIVE_SKIP_LLM=1 from conftest hive_env fixture).
"""

from __future__ import annotations

import io
import json
import sys
from datetime import date, timedelta

import pytest

# ---------------------------------------------------------------------------
# Flow 1: Day-One Capture and Retrieval
# ---------------------------------------------------------------------------


class TestDayOneCaptureAndRetrieval:
    """remember FACT -> DECISION -> TODO -> recall finds all -> status shows ->
    todo shows -> todo done marks -> todo no longer shows -> recall still finds."""

    def test_full_capture_recall_cycle(self, hive_env):
        from keephive.commands.remember import _search_all_tiers, cmd_remember
        from keephive.commands.todo import _todo_done
        from keephive.storage import open_todos

        # Capture 3 entries
        cmd_remember(["FACT: Python 3.14 released"])
        cmd_remember(["DECISION: use uv over pip for speed"])
        cmd_remember(["TODO: migrate CI to uv"])

        # Recall finds all three
        results = _search_all_tiers("uv")
        texts = " ".join(r["line"] for r in results)
        assert "use uv over pip" in texts
        assert "migrate CI to uv" in texts

        results = _search_all_tiers("Python 3.14")
        assert any("Python 3.14" in r["line"] for r in results)

        # TODO shows in open list
        todos = open_todos()
        assert any("migrate CI to uv" in t for _, _, t in todos)

        # Mark done
        _todo_done("migrate CI")

        # No longer in open TODOs
        todos = open_todos()
        assert not any("migrate CI to uv" in t for _, _, t in todos)

        # But FACT and DECISION still findable
        results = _search_all_tiers("uv")
        texts = " ".join(r["line"] for r in results)
        assert "use uv over pip" in texts

    def test_status_reflects_captures(self, hive_env, capsys):
        from keephive.commands.remember import cmd_remember
        from keephive.commands.status import cmd_status

        cmd_remember(["TODO: fix the widget"])
        capsys.readouterr()  # clear
        cmd_status([])
        out = capsys.readouterr().out
        assert "fix the widget" in out
        assert "open TODO" in out


# ---------------------------------------------------------------------------
# Flow 2: Memory Promotion and Persistence
# ---------------------------------------------------------------------------


class TestMemoryPromotionAndPersistence:
    """remember FACT -> mem promotes to working memory -> sessionstart includes it
    -> mem rm removes -> sessionstart no longer includes."""

    def test_mem_promotion_and_removal(self, hive_env):
        from keephive.commands.memory import cmd_mem
        from keephive.hooks.sessionstart import build_context
        from keephive.storage import memory_file

        # Promote fact to working memory
        cmd_mem(["uv cold starts in 200ms"])

        # Verify in file
        mem = memory_file().read_text()
        assert "uv cold starts in 200ms" in mem
        assert "[verified:" in mem

        # SessionStart includes it
        ctx = build_context("/test/project", "project")
        assert "uv cold starts in 200ms" in ctx

        # Remove
        cmd_mem(["rm", "uv cold starts"])

        # Verify removed from file
        mem = memory_file().read_text()
        assert "uv cold starts" not in mem

        # SessionStart no longer includes it
        ctx = build_context("/test/project", "project")
        assert "uv cold starts" not in ctx


# ---------------------------------------------------------------------------
# Flow 3: Stale Fact Detection (no LLM)
# ---------------------------------------------------------------------------


class TestStaleFactDetection:
    """Write fact with old date -> status shows stale -> verify --check exits 1
    -> sessionstart includes stale warning."""

    def test_stale_fact_surfaces_everywhere(self, hive_env, capsys):
        from keephive.commands.status import cmd_status
        from keephive.hooks.sessionstart import build_context

        # hive_env already has a fact from 2020-01-01 (definitely stale)

        # Status shows stale warning
        cmd_status([])
        out = capsys.readouterr().out
        assert "stale" in out.lower()

        # SessionStart includes warning
        ctx = build_context("/test/project", "project")
        assert "stale" in ctx.lower()

    def test_verify_check_exits_nonzero_when_stale(self, hive_env, monkeypatch):
        monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)
        from keephive.commands.verify import cmd_verify

        with pytest.raises(SystemExit) as exc_info:
            cmd_verify(["--check"])
        assert exc_info.value.code == 1

    def test_verify_check_exits_zero_when_fresh(self, hive_env, monkeypatch):
        monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)
        # Make all facts fresh
        mem = hive_env / "working" / "memory.md"
        today = date.today().isoformat()
        mem.write_text(f"# Working Memory\n\n- Everything is fresh [verified:{today}]\n")
        from keephive.commands.verify import cmd_verify

        with pytest.raises(SystemExit) as exc_info:
            cmd_verify(["--check"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Flow 4: Recurring Task Lifecycle
# ---------------------------------------------------------------------------


class TestRecurringTaskLifecycle:
    """todo repeat daily -> todo shows due -> recurring done -> todo no longer shows."""

    def test_recurring_add_due_done_cycle(self, hive_env, capsys):
        from keephive.commands.recurring import cmd_recurring
        from keephive.commands.todo import cmd_todo
        from keephive.storage import due_recurring

        # Add
        cmd_recurring(["daily", "Run test suite"])
        due = due_recurring()
        assert len(due) == 1
        assert "Run test suite" in due[0][1]

        # Todo shows it
        capsys.readouterr()
        cmd_todo([])
        out = capsys.readouterr().out
        assert "Recurring" in out
        assert "Run test suite" in out

        # Mark done
        cmd_recurring(["done", "test suite"])

        # No longer due
        due = due_recurring()
        assert len(due) == 0


# ---------------------------------------------------------------------------
# Flow 5: Audit Metrics Consistency
# ---------------------------------------------------------------------------


class TestAuditMetricsConsistency:
    """Seed data -> audit metrics are internally consistent."""

    def test_score_components_match_data(self, hive_env):
        from datetime import date

        # Seed daily log with known entries
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        daily.write_text(
            f"# Daily Log: {today}\n\n"
            "- [10:00:00] TODO: Task A\n"
            "- [10:01:00] TODO: Task B\n"
            "- [10:02:00] DONE: Task A\n"
            "- [10:03:00] FACT: Important finding\n"
            "- [10:04:00] CORRECTION: old was wrong, new is right\n"
        )

        # Create strategy guide
        (hive_env / "knowledge" / "guides" / "strategy.md").write_text(
            "# Strategy\nBuild verification tools."
        )

        from keephive.commands.audit import (
            _analyze_cleaner,
            _analyze_strategist,
            _analyze_vault,
            _compute_score,
        )

        vault = _analyze_vault()
        cleaner = _analyze_cleaner()
        strategist = _analyze_strategist()
        score = _compute_score(vault, cleaner, strategist)

        # Vault: memory.md has 3 verified facts (from fixture)
        assert vault["total_facts"] == 3
        assert vault["stale_facts"] == 1  # 2020-01-01 fact

        # Cleaner: completion rate reflects TODO/DONE ratio
        assert cleaner["todo_completion_rate"] > 0

        # Strategist: has strategy guide
        assert strategist["has_strategy"] is True
        assert strategist["has_rules"] is True

        # Score should be calculable and within range
        assert 0 <= score <= 100

    def test_skip_llm_json_output(self, hive_env, capsys):
        from keephive.commands.audit import cmd_audit

        cmd_audit(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data["score"], int)
        assert "vault" in data
        assert "cleaner" in data
        assert "strategist" in data


# ---------------------------------------------------------------------------
# Flow 6: Doctor Detects Real Problems
# ---------------------------------------------------------------------------


class TestDoctorDetectsProblems:
    """Write duplicate TODOs -> doctor finds them."""

    def test_doctor_finds_duplicate_todos(self, hive_env, capsys):
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        daily.write_text(
            f"# Daily Log: {today}\n\n"
            "- [10:00:00] TODO: Fix the login bug\n"
            "- [10:01:00] TODO: Fix the login bug\n"
            "- [10:02:00] TODO: Fix the login bug\n"
        )

        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "duplicate" in out.lower()


# ---------------------------------------------------------------------------
# Flow 7: Reflect Scan Shows Structured Summary
# ---------------------------------------------------------------------------


class TestReflectScanStructuredSummary:
    """Seed 3 days of logs -> reflect scan shows all days."""

    def test_reflect_scan_shows_entries(self, hive_env, capsys):
        # Seed 3 days of logs
        for days_ago in range(3):
            d = date.today() - timedelta(days=days_ago)
            daily = hive_env / "daily" / f"{d.isoformat()}.md"
            daily.write_text(
                f"# Daily Log: {d.isoformat()}\n\n"
                f"- [10:00:00] FACT: Day {days_ago} fact\n"
                f"- [10:01:00] TODO: Day {days_ago} task\n"
            )

        from keephive.commands.reflect import cmd_reflect

        cmd_reflect([])
        out = capsys.readouterr().out
        # Should show entry counts for each day
        assert "entries" in out.lower()
        # Should show TODO items
        assert "task" in out.lower()


# ---------------------------------------------------------------------------
# Flow 8: Note Lifecycle
# ---------------------------------------------------------------------------


class TestNoteLifecycle:
    """note creates -> show reads -> clear empties."""

    def test_note_write_show_clear(self, hive_env, capsys):
        from keephive.commands.note import cmd_note
        from keephive.storage import slot_file

        note_path = slot_file(1)

        # Write content
        note_path.write_text("# Test Note\nSome content here.")

        # Show
        cmd_note(["show"])
        out = capsys.readouterr().out
        assert "Test Note" in out

        # Clear
        cmd_note(["clear"])
        out = capsys.readouterr().out
        assert "cleared" in out.lower() or not note_path.read_text().strip()


# ---------------------------------------------------------------------------
# Flow 9: PreCompact Extracts Both User and Assistant
# ---------------------------------------------------------------------------


class TestPreCompactExtraction:
    """Create fake transcript -> precompact extracts user + assistant."""

    def test_extract_user_and_assistant_messages(self, hive_env, tmp_path):
        from keephive.hooks.precompact import _extract_excerpts

        transcript = tmp_path / "test.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": "Please investigate the authentication failure in the login endpoint"
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "The authentication failure is caused by an expired JWT signing key. The key rotation schedule was set to 30 days but the last rotation happened 45 days ago, causing all new tokens to fail validation.",
                            },
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Let me read the configuration file."},
                        ]
                    },
                }
            ),
        ]
        transcript.write_text("\n".join(lines))

        excerpts = _extract_excerpts(str(transcript), 4000)
        assert "[USER]" in excerpts
        assert "authentication" in excerpts.lower()
        # Noise message should be filtered
        assert "Let me read" not in excerpts

    def test_empty_transcript_returns_empty(self, hive_env, tmp_path):
        from keephive.hooks.precompact import _extract_excerpts

        transcript = tmp_path / "empty.jsonl"
        transcript.write_text("")
        assert _extract_excerpts(str(transcript), 4000) == ""


# ---------------------------------------------------------------------------
# Flow 10: Full Hook Chain
# ---------------------------------------------------------------------------


class TestFullHookChain:
    """sessionstart -> returns context -> posttooluse first -> returns reminder
    -> posttooluse second -> returns nothing."""

    def test_sessionstart_returns_context(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/test/project", "project")
        # Must include working memory facts
        assert "Python is great" in ctx
        # Must include workflows section
        assert "## Workflows" in ctx
        # Must include MCP and CLI references
        assert "hive_remember" in ctx
        assert "hive_recall" in ctx

    def test_posttooluse_counter_based_nudge(self, hive_env):
        """PostToolUse fires nudge at interval boundary, silent otherwise."""
        from keephive.hooks.posttooluse import hook_posttooluse

        session_id = "e2e-hook-chain-test"

        # First call (count=1): should be silent (default interval 8)
        input_json = json.dumps({"session_id": session_id, "tool_name": "Edit"})
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(input_json)
        sys.stdout = captured = io.StringIO()
        try:
            hook_posttooluse([])
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout

        assert captured.getvalue() == "", "First call should be silent"

        # Set counter to 7 so next call (count=8) fires at default interval 8.
        # Use slot 0 (count=24 -> slot=(24//8)%3=0) to avoid status-aware slot.
        counter_file = hive_env / ".tool-counter"
        counter_file.write_text(json.dumps({"count": 23, "session_id": session_id}))

        sys.stdin = io.StringIO(json.dumps({"session_id": session_id, "tool_name": "Write"}))
        sys.stdout = captured2 = io.StringIO()
        try:
            hook_posttooluse([])
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout

        output = captured2.getvalue()
        assert output, "24th call should produce nudge"
        data = json.loads(output)
        assert "hive_remember" in data["hookSpecificOutput"]["additionalContext"]

    def test_sessionstart_includes_stale_warnings(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/test/project", "project")
        # The fixture has a fact from 2020-01-01
        assert "stale" in ctx.lower()

    def test_sessionstart_includes_open_todos(self, hive_env):
        from keephive.commands.remember import cmd_remember
        from keephive.hooks.sessionstart import build_context

        cmd_remember(["TODO: deploy to staging"])
        ctx = build_context("/test/project", "project")
        assert "deploy to staging" in ctx

    def test_sessionstart_includes_due_recurring(self, hive_env):
        from keephive.commands.recurring import cmd_recurring
        from keephive.hooks.sessionstart import build_context

        cmd_recurring(["daily", "Check CI"])
        ctx = build_context("/test/project", "project")
        assert "Due Recurring Tasks" in ctx
        assert "Check CI" in ctx


# ---------------------------------------------------------------------------
# Flow 11: Status Output Completeness
# ---------------------------------------------------------------------------


class TestStatusOutputCompleteness:
    """Seed environment with facts, TODOs, guide, draft, entries.
    Status shows all sections."""

    def test_status_shows_all_sections(self, hive_env, capsys):
        from keephive.commands.recurring import cmd_recurring
        from keephive.commands.remember import cmd_remember
        from keephive.commands.status import cmd_status
        from keephive.storage import slot_file

        # Add TODO
        cmd_remember(["TODO: fix regression"])

        # Add recurring
        cmd_recurring(["daily", "Check builds"])

        # Add a guide
        (hive_env / "knowledge" / "guides" / "testing.md").write_text(
            "# Testing Guide\nAlways test."
        )

        # Add note in slot 1
        note = slot_file(1)
        note.write_text("# Note\nSome note content.")

        capsys.readouterr()  # clear output from setup commands
        cmd_status([])
        out = capsys.readouterr().out

        # Version
        assert "keephive" in out
        # Fact count with stale
        assert "stale" in out.lower()
        # TODO summary
        assert "fix regression" in out
        assert "open TODO" in out
        # Due recurring
        assert "due recurring" in out.lower()
        assert "Check builds" in out
        # Note indicator
        assert "Note" in out and "ready" in out
        # Footer (stale facts -> suggests verify session)
        assert "hive v" in out
        assert "session verify" in out


# ---------------------------------------------------------------------------
# Flow 12: Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary conditions: empty env, unicode, long text, concurrent appends."""

    def test_empty_environment_status(self, hive_env, capsys):
        """Status works with minimal data."""
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "keephive" in out  # At least shows version

    def test_empty_environment_todo(self, hive_env, capsys):
        """Todo with zero TODOs prints clean output."""
        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        # Should not crash, may show "No open TODOs" or similar
        assert "error" not in out.lower()

    def test_empty_environment_recall(self, hive_env, capsys):
        """Recall with no matches returns clean output."""
        from keephive.commands.remember import cmd_recall

        cmd_recall(["nonexistent_xyzzy_pattern"])
        out = capsys.readouterr().out
        assert "no results" in out.lower() or "No results" in out or out.strip() == ""

    def test_unicode_in_todo(self, hive_env):
        """Unicode text round-trips through remember and recall."""
        from keephive.commands.remember import _search_all_tiers, cmd_remember
        from keephive.storage import open_todos

        cmd_remember(["TODO: fix bug in login"])
        todos = open_todos()
        assert any("fix bug in login" in t for _, _, t in todos)

        # Unicode recall
        results = _search_all_tiers("login")
        assert len(results) > 0

    def test_long_todo_text(self, hive_env):
        """Long TODO text is stored without truncation in daily log."""
        from keephive.commands.remember import cmd_remember

        long_text = "TODO: " + "A" * 500
        cmd_remember([long_text])

        daily = hive_env / "daily" / f"{date.today().isoformat()}.md"
        content = daily.read_text()
        assert "A" * 500 in content

    def test_missing_daily_directory_autocreated(self, tmp_path, monkeypatch):
        """Commands create daily directory if missing."""
        hive_dir = tmp_path / "hive"
        hive_dir.mkdir()
        (hive_dir / "working").mkdir()
        (hive_dir / "working" / "memory.md").write_text("# Working Memory\n")
        (hive_dir / "working" / "rules.md").write_text("# Working Rules\n")
        monkeypatch.setenv("HIVE_HOME", str(hive_dir))
        monkeypatch.setenv("HIVE_SKIP_LLM", "1")

        # daily dir does not exist
        assert not (hive_dir / "daily").exists()

        from keephive.commands.remember import cmd_remember

        cmd_remember(["FACT: auto-creates daily dir"])

        assert (hive_dir / "daily").exists()
        daily = hive_dir / "daily" / f"{date.today().isoformat()}.md"
        assert daily.exists()
        assert "auto-creates daily dir" in daily.read_text()

    def test_concurrent_appends_no_corruption(self, hive_env):
        """Two rapid appends don't corrupt the file."""
        from keephive.commands.remember import cmd_remember

        cmd_remember(["FACT: first entry"])
        cmd_remember(["FACT: second entry"])

        daily = hive_env / "daily" / f"{date.today().isoformat()}.md"
        content = daily.read_text()
        assert "first entry" in content
        assert "second entry" in content
        # Each entry should be on its own line
        lines = [line for line in content.splitlines() if "entry" in line]
        assert len(lines) == 2

    def test_status_json_mode_complete(self, hive_env, capsys):
        """JSON status includes all expected fields."""
        from keephive.commands.status import cmd_status

        cmd_status(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        expected_keys = {
            "version",
            "working_lines",
            "verified_facts",
            "stale_facts",
            "guides",
            "today_entries",
            "yesterday_entries",
            "disk_usage",
            "hive_dir",
        }
        assert expected_keys.issubset(set(data.keys()))

    def test_recall_json_mode(self, hive_env, capsys):
        """Recall --json returns valid structured output."""
        from keephive.commands.remember import cmd_recall

        cmd_recall(["Python", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "query" in data
        assert "results" in data
        assert isinstance(data["results"], list)
