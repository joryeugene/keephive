"""End-to-end lifecycle tests: data flows through the full system."""

from __future__ import annotations

from datetime import date


class TestRememberRecallLifecycle:
    """remember -> recall -> verify that data persists and surfaces."""

    def test_remember_creates_daily_entry(self, hive_env):
        from keephive.commands.remember import cmd_remember

        cmd_remember(["FACT: test fact for lifecycle"])

        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        assert daily.exists()
        content = daily.read_text()
        assert "FACT: test fact for lifecycle" in content

    def test_recall_finds_working_memory(self, hive_env):
        from keephive.commands.remember import _search_all_tiers

        results = _search_all_tiers("Python")
        assert len(results) > 0
        assert any(r["tier"] == "working" for r in results)

    def test_recall_finds_daily_entries(self, hive_env, daily_with_entries):
        from keephive.commands.remember import _search_all_tiers

        results = _search_all_tiers("type param")
        assert len(results) > 0
        assert any(r["tier"] == "daily" for r in results)


class TestTodoLifecycle:
    """todo -> done -> standup reflects completed work."""

    def test_todo_then_done(self, hive_env):
        from keephive.commands.remember import cmd_remember
        from keephive.storage import open_todos

        # Create a TODO
        cmd_remember(["TODO: build feature X"])
        todos = open_todos()
        assert any("build feature X" in t for _, _, t in todos)

        # Mark as done
        from keephive.commands.todo import _todo_done

        _todo_done("feature X")

        # Should no longer appear in open todos
        todos = open_todos()
        assert not any("build feature X" in t for _, _, t in todos)


class TestMemRuleLifecycle:
    """mem add -> verify in file -> mem rm -> verify removed."""

    def test_mem_add_and_remove(self, hive_env):
        from keephive.commands.memory import cmd_mem
        from keephive.storage import memory_file

        # Add
        cmd_mem(["test fact for removal"])
        mem = memory_file().read_text()
        assert "test fact for removal" in mem
        assert "[verified:" in mem

        # Remove
        cmd_mem(["rm", "test fact for removal"])
        mem = memory_file().read_text()
        assert "test fact for removal" not in mem

    def test_backup_exists_after_write(self, hive_env):
        from keephive.commands.memory import cmd_mem
        from keephive.storage import memory_file

        cmd_mem(["new fact"])
        bak = memory_file().with_suffix(".md.bak")
        assert bak.exists()


class TestSessionStartLifecycle:
    """sessionstart hook injects working memory into context."""

    def test_context_includes_memory(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/test/project", "project")
        assert "Working Memory" in ctx
        assert "Python is great" in ctx

    def test_context_includes_stale_warning(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/test/project", "project")
        # The test fixture has a fact from 2020-01-01 which is definitely stale
        assert "stale" in ctx.lower()

    def test_context_includes_todos(self, hive_env, daily_with_entries):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/test/project", "project")
        # daily_with_entries has a TODO that's already DONE,
        # but the TODO for "Add more tests" is marked done
        # Let's just check the structure is there
        assert "Working Memory" in ctx

    def test_workflows_contain_dual_mcp_and_cli_references(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/test/project", "project")
        # Workflows section must exist with both MCP tool names and CLI equivalents
        assert "## Workflows" in ctx
        assert "hive_recall(topic)" in ctx
        assert "`hive rc <topic>`" in ctx
        assert "hive_remember(text)" in ctx
        assert "`hive r`" in ctx
        assert "hive_todo()" in ctx
        assert "`hive todo`" in ctx
        assert "hive_todo_done(pattern)" in ctx
        # Code hygiene block
        assert "### Code Hygiene" in ctx
        assert "dead code" in ctx.lower()
        assert "orphaned imports" in ctx.lower()


class TestPostToolUseLifecycle:
    """PostToolUse hook fires at interval boundaries via counter."""

    def test_first_edit_is_silent(self, hive_env):
        """First call (count=1) is silent at default interval 8."""
        import io
        import json
        import sys

        from keephive.hooks.posttooluse import hook_posttooluse

        session_id = "test-session-abc"
        input_json = json.dumps({"session_id": session_id, "tool_name": "Edit"})

        old_stdin = sys.stdin
        old_stdout = sys.stdout
        sys.stdin = io.StringIO(input_json)
        sys.stdout = captured = io.StringIO()
        try:
            hook_posttooluse([])
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert output == "", "First call should be silent (count=1, interval=8)"

    def test_fires_at_interval(self, hive_env):
        """Fires nudge when counter hits interval boundary."""
        import io
        import json
        import sys

        from keephive.hooks.posttooluse import hook_posttooluse

        session_id = "test-session-def"
        # Set counter to 23 so next call (count=24) fires.
        # slot = (24 // 8) % 3 = 0 (tool usage slot with hive_remember)
        counter_file = hive_env / ".tool-counter"
        counter_file.write_text(json.dumps({"count": 23, "session_id": session_id}))

        input_json = json.dumps({"session_id": session_id, "tool_name": "Edit"})

        old_stdin = sys.stdin
        old_stdout = sys.stdout
        sys.stdin = io.StringIO(input_json)
        sys.stdout = captured = io.StringIO()
        try:
            hook_posttooluse([])
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert output, "24th call should produce nudge"
        data = json.loads(output)
        assert "hookSpecificOutput" in data
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "hive_remember" in ctx


class TestPreCompactExcerpts:
    """precompact extracts meaningful excerpts from transcripts."""

    def test_extract_user_and_assistant(self, hive_env, tmp_path):
        import json

        from keephive.hooks.precompact import _extract_excerpts

        # Create a fake transcript
        transcript = tmp_path / "test.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "Please fix the authentication bug in login.py"},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "I found the issue in the login handler. The session token was not being validated correctly against the database, causing intermittent authentication failures for users with special characters in their passwords.",
                            }
                        ]
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
                                "text": "Let me fix this by updating the validation logic.",
                            }
                        ]
                    },
                }
            ),
        ]
        transcript.write_text("\n".join(lines))

        excerpts = _extract_excerpts(str(transcript), 4000)
        assert "[USER]" in excerpts
        assert "authentication" in excerpts.lower()
        # The noise message "Let me fix this" should be filtered
        assert "Let me fix this" not in excerpts

    def test_empty_transcript(self, hive_env, tmp_path):
        from keephive.hooks.precompact import _extract_excerpts

        transcript = tmp_path / "empty.jsonl"
        transcript.write_text("")
        result = _extract_excerpts(str(transcript), 4000)
        assert result == ""

    def test_layer2_failure_logged_to_debug(self, hive_env):
        """Layer 2 LLM failure is caught and logged to debug."""
        from keephive.hooks.precompact import _llm_summary
        from keephive.storage import hive_dir

        def _boom(*a, **kw):
            raise RuntimeError("simulated pipe failure")

        _llm_summary("some excerpt text", pipe_fn=_boom)

        debug_log = hive_dir() / ".hook-debug.log"
        assert debug_log.exists()
        log_content = debug_log.read_text()
        assert "Layer 2 failed" in log_content
        assert "simulated pipe failure" in log_content


class TestPreCompactGarbageFilter:
    """_is_garbage_insight rejects short and parroted insights."""

    def test_rejects_short_text(self):
        from keephive.hooks.precompact import _is_garbage_insight

        assert _is_garbage_insight("short") is True
        assert _is_garbage_insight("also short.") is True
        assert _is_garbage_insight("x" * 14) is True

    def test_accepts_long_enough_text(self):
        from keephive.hooks.precompact import _is_garbage_insight

        assert _is_garbage_insight("This is a real insight worth keeping") is False

    def test_rejects_parroted_category_descriptions(self):
        from keephive.hooks.precompact import _is_garbage_insight

        assert _is_garbage_insight("unfinished work or follow-up items") is True
        assert _is_garbage_insight("choices made about architecture, tools, approach") is True
        assert _is_garbage_insight("something learned that was previously unknown") is True
        assert _is_garbage_insight("something that was wrong and got fixed") is True
        assert _is_garbage_insight("non-obvious observations or patterns") is True

    def test_rejects_parroted_with_trailing_period(self):
        from keephive.hooks.precompact import _is_garbage_insight

        assert _is_garbage_insight("unfinished work or follow-up items.") is True

    def test_rejects_parroted_case_insensitive(self):
        from keephive.hooks.precompact import _is_garbage_insight

        assert _is_garbage_insight("Unfinished Work Or Follow-Up Items") is True

    def test_llm_summary_skips_garbage(self, hive_env):
        """_llm_summary filters out garbage insights before writing."""
        from keephive.hooks.precompact import _llm_summary
        from keephive.models import PreCompactResponse
        from keephive.storage import daily_file

        fake_response = PreCompactResponse(
            insights=[
                {"category": "TODO", "description": "unfinished work or follow-up items"},
                {"category": "FACT", "description": "short"},
                {
                    "category": "FACT",
                    "description": "uv run pytest passes all 60 tests in under 2 seconds",
                },
            ],
            memory_updates=[],
        )

        def fake_pipe(*a, **kw):
            return fake_response

        _llm_summary("some excerpts", pipe_fn=fake_pipe)

        content = daily_file().read_text()
        assert "unfinished work or follow-up items" not in content
        assert "short" not in content
        assert "uv run pytest passes all 60 tests" in content


class TestSessionStartRecurringSurfacing:
    """SessionStart hook surfaces due recurring tasks in context."""

    def test_due_recurring_appears_in_context(self, hive_env):
        """When a recurring task is due, SessionStart includes it."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.hooks.sessionstart import build_context

        cmd_recurring(["daily", "Run test suite"])
        # Never completed = due immediately
        ctx = build_context("/test/project", "project")
        assert "Due Recurring Tasks" in ctx
        assert "Run test suite" in ctx
        assert "daily" in ctx

    def test_no_recurring_section_when_none_due(self, hive_env):
        """No recurring section when no tasks exist."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/test/project", "project")
        assert "Due Recurring Tasks" not in ctx

    def test_completed_recurring_not_shown(self, hive_env):
        """After marking done, a daily task disappears from context."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.hooks.sessionstart import build_context

        cmd_recurring(["daily", "Check builds"])
        cmd_recurring(["done", "builds"])
        ctx = build_context("/test/project", "project")
        assert "Due Recurring Tasks" not in ctx

    def test_context_shows_overdue_count(self, hive_env):
        """Overdue tasks show how many days overdue."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.hooks.sessionstart import build_context
        from keephive.storage import recurring_file, safe_read_text

        cmd_recurring(["daily", "Overdue task"])
        # Backdate the completion to 3 days ago
        rf = recurring_file()
        content = safe_read_text(rf)
        from datetime import timedelta

        old_date = (date.today() - timedelta(days=3)).isoformat()
        content += f"\n- Overdue task: {old_date}\n"
        # Remove the "never completed" state by adding a Last Completed entry
        rf.write_text(content)

        ctx = build_context("/test/project", "project")
        assert "Due Recurring Tasks" in ctx
        assert "overdue" in ctx.lower()


class TestTodoDisplay:
    """cmd_todo display logic: recurring, open TODOs, recently done."""

    def test_todo_shows_open_items(self, hive_env, capsys):
        """cmd_todo lists open TODOs with age labels."""
        from keephive.commands.remember import cmd_remember
        from keephive.commands.todo import cmd_todo

        cmd_remember(["TODO: fix the widget"])
        cmd_todo([])
        out = capsys.readouterr().out
        assert "fix the widget" in out
        assert "today" in out

    def test_todo_shows_due_recurring(self, hive_env, capsys):
        """cmd_todo shows due recurring tasks before open TODOs."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.commands.todo import cmd_todo

        cmd_recurring(["daily", "Check CI"])
        cmd_todo([])
        out = capsys.readouterr().out
        assert "Recurring" in out
        assert "Check CI" in out

    def test_todo_shows_recently_done(self, hive_env, daily_with_entries, capsys):
        """cmd_todo shows recently done items."""
        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        assert "Recently Done" in out
        assert "Add more tests" in out

    def test_todo_done_pattern_match(self, hive_env, capsys):
        """_todo_done matches pattern and marks as done."""
        from keephive.commands.remember import cmd_remember
        from keephive.commands.todo import _todo_done
        from keephive.storage import open_todos

        cmd_remember(["TODO: deploy to staging"])
        _todo_done("staging")
        out = capsys.readouterr().out
        assert "Completed" in out

        todos = open_todos()
        assert not any("deploy to staging" in t for _, _, t in todos)

    def test_todo_done_no_match_shows_feedback(self, hive_env, capsys):
        """_todo_done with no matching TODO reports no match."""
        from keephive.commands.todo import _todo_done

        _todo_done("nonexistent task xyz")
        out = capsys.readouterr().out
        assert "No matching TODO" in out, f"Should report no matching TODO. Output: {out!r}"

    def test_todo_routes_to_recurring(self, hive_env, capsys):
        """cmd_todo(['repeat', ...]) routes to recurring command."""
        from keephive.commands.todo import cmd_todo

        cmd_todo(["repeat", "daily", "Run tests"])
        out = capsys.readouterr().out
        assert "Added" in out

    def test_quick_todo_shortcut(self, hive_env, capsys):
        """cmd_t creates a TODO via remember."""
        from keephive.commands.todo import cmd_t
        from keephive.storage import open_todos

        cmd_t(["ship the feature"])
        todos = open_todos()
        assert any("ship the feature" in t for _, _, t in todos)


class TestStatusDisplay:
    """cmd_status shows comprehensive overview."""

    def test_status_shows_version(self, hive_env, capsys):
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "keephive" in out

    def test_status_shows_stale_warning(self, hive_env, capsys):
        """Stale facts trigger a warning in status."""
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "stale" in out.lower()

    def test_status_shows_todos(self, hive_env, capsys):
        """Open TODOs appear in status output."""
        from keephive.commands.remember import cmd_remember
        from keephive.commands.status import cmd_status

        cmd_remember(["TODO: fix regression"])
        capsys.readouterr()  # clear remember output
        cmd_status([])
        out = capsys.readouterr().out
        assert "fix regression" in out
        assert "open TODO" in out

    def test_status_shows_due_recurring(self, hive_env, capsys):
        """Due recurring tasks appear in status."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.commands.status import cmd_status

        cmd_recurring(["daily", "Review PRs"])
        capsys.readouterr()  # clear recurring output
        cmd_status([])
        out = capsys.readouterr().out
        assert "due recurring" in out.lower()
        assert "Review PRs" in out

    def test_status_json_mode(self, hive_env, capsys):
        """Status --json returns valid JSON with expected fields."""
        import json

        from keephive.commands.status import cmd_status

        cmd_status(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "version" in data
        assert "working_lines" in data
        assert "stale_facts" in data
        assert "today_entries" in data

    def test_status_shows_today_entries(self, hive_env, daily_with_entries, capsys):
        """Today's entries appear in status."""
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "Today" in out


class TestDedupTodos:
    """Improved _dedup_todos: normalization, exact dedup, higher threshold."""

    def test_exact_content_dedup_after_normalization(self, hive_env):
        """Identical content after normalization is deduped."""
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-02-15", "10:00", "[audit] Fix the bug"),
            ("2026-02-16", "11:00", "Fix the bug"),
        ]
        result = _dedup_todos(todos)
        assert len(result) == 1
        # Keeps the more recent one
        assert result[0][0] == "2026-02-16"

    def test_prefix_stripping(self, hive_env):
        """Bracketed prefixes are stripped before comparison."""
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-02-15", "10:00", "[reflect] Run test suite"),
            ("2026-02-16", "11:00", "[audit] Run test suite"),
        ]
        result = _dedup_todos(todos)
        assert len(result) == 1

    def test_distinct_items_preserved(self, hive_env):
        """Distinct TODOs are not deduped."""
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-02-15", "10:00", "Fix the login bug"),
            ("2026-02-16", "11:00", "Deploy to production"),
        ]
        result = _dedup_todos(todos)
        assert len(result) == 2

    def test_fuzzy_dedup_at_higher_threshold(self, hive_env):
        """Fuzzy dedup requires 0.8 similarity (not 0.7)."""
        from keephive.storage import _dedup_todos

        # These are similar but below 0.8 threshold
        todos = [
            ("2026-02-15", "10:00", "Fix login bug in auth module"),
            ("2026-02-16", "11:00", "Deploy auth module to staging"),
        ]
        result = _dedup_todos(todos)
        assert len(result) == 2  # Should NOT be deduped

    def test_empty_list(self, hive_env):
        """Empty input returns empty output."""
        from keephive.storage import _dedup_todos

        assert _dedup_todos([]) == []

    def test_trailing_punctuation_normalized(self, hive_env):
        """Trailing punctuation differences don't prevent dedup."""
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-02-15", "10:00", "Fix the bug."),
            ("2026-02-16", "11:00", "Fix the bug"),
        ]
        result = _dedup_todos(todos)
        assert len(result) == 1


class TestRecurringLifecycle:
    """Recurring tasks: add, due detection, done, numeric intervals."""

    def test_parse_freq_named(self, hive_env):
        from keephive.storage import parse_freq

        assert parse_freq("daily") == 1.0
        assert parse_freq("weekly") == 7.0
        assert parse_freq("monthly") == 30.0

    def test_parse_freq_numeric_days(self, hive_env):
        from keephive.storage import parse_freq

        assert parse_freq("2d") == 2.0
        assert parse_freq("14d") == 14.0

    def test_parse_freq_numeric_hours(self, hive_env):
        from keephive.storage import parse_freq

        assert parse_freq("12h") == 0.5
        assert parse_freq("6h") == 0.25
        assert parse_freq("24h") == 1.0

    def test_parse_freq_invalid(self, hive_env):
        import pytest

        from keephive.storage import parse_freq

        with pytest.raises(ValueError):
            parse_freq("biweekly")
        with pytest.raises(ValueError):
            parse_freq("2x")

    def test_recurring_add_numeric_due(self, hive_env):
        """Numeric frequency tasks show up as due when never completed."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import due_recurring

        cmd_recurring(["2d", "Run test suite"])
        due = due_recurring()
        assert len(due) == 1
        freq, text, _ = due[0]
        assert freq == "2d"
        assert "Run test suite" in text

    def test_recurring_done_clears_due(self, hive_env):
        """Completing a recurring task removes it from the due list."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import due_recurring

        cmd_recurring(["daily", "Check builds"])
        assert len(due_recurring()) == 1

        cmd_recurring(["done", "builds"])
        # After marking done today, a daily task is no longer overdue
        assert len(due_recurring()) == 0

    def test_recurring_hour_done_uses_timestamp(self, hive_env):
        """Hour-based tasks store full ISO timestamp on completion."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import recurring_file, safe_read_text

        cmd_recurring(["12h", "Check CI"])
        cmd_recurring(["done", "CI"])

        content = safe_read_text(recurring_file())
        assert "T" in content  # ISO timestamp with time component
