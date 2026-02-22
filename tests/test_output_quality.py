"""Output quality tests: validate that commands produce useful, well-formatted output.

These go beyond 'doesn't crash' to verify that output contains the right
structure, formatting, and information density.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

# -- Status output tests --


class TestStatusOutput:
    def test_truncation_notice_when_many_todos(self, hive_env, capsys):
        """When >3 TODOs exist, status shows truncation notice."""
        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        # Use very distinct task names to prevent dedup (>70% similarity threshold)
        tasks = [
            "Fix authentication bug in login flow",
            "Write integration tests for API endpoints",
            "Refactor database connection pooling",
            "Update deployment documentation",
            "Add monitoring dashboard alerts",
            "Implement rate limiting middleware",
        ]
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            + "".join(f"- [10:0{i}:00] TODO: {tasks[i]}\n" for i in range(6))
        )
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "... and" in out
        assert "more" in out
        assert "hive todo" in out

    def test_no_truncation_with_few_todos(self, hive_env, capsys):
        """When <=3 TODOs exist, no truncation notice."""
        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] TODO: Task one\n"
            "- [10:01:00] TODO: Task two\n"
        )
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "... and" not in out

    def test_stale_warning_shows_count_and_action(self, hive_env, capsys):
        """Stale warning includes count and 'hive v' action."""
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "stale" in out.lower()
        assert "hive v" in out

    def test_status_shows_facts_breakdown(self, hive_env, capsys):
        """Status shows ok vs stale fact counts."""
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        # Should show "3 facts (2 ok, 1 stale)" or similar
        assert "facts" in out
        assert "ok" in out


class TestTodoOutput:
    def test_age_labels_today_vs_days(self, hive_env, capsys):
        """TODOs from today show 'today', older show 'Nd'."""
        today_str = date.today().isoformat()
        yesterday_str = (date.today() - timedelta(days=3)).isoformat()
        daily_today = hive_env / "daily" / f"{today_str}.md"
        daily_today.write_text(f"# Daily Log: {today_str}\n\n- [10:00:00] TODO: Fresh task\n")
        daily_old = hive_env / "daily" / f"{yesterday_str}.md"
        daily_old.write_text(f"# Daily Log: {yesterday_str}\n\n- [08:00:00] TODO: Old task\n")
        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        assert "today" in out
        assert "3d" in out

    def test_done_item_disappears_from_open(self, hive_env, capsys):
        """Completed TODO does not show in Open TODOs section."""
        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] TODO: Write docs\n"
            "- [10:05:00] DONE: Write docs\n"
            "- [10:10:00] TODO: Fix bugs\n"
        )
        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        assert "Fix bugs" in out
        # "Write docs" should only appear in Recently Done, not Open TODOs
        open_section = out.split("Recently Done")[0] if "Recently Done" in out else out
        assert "Write docs" not in open_section


class TestStandupOutput:
    def test_standup_has_structured_sections(self, hive_env, daily_with_entries, capsys):
        """Standup produces Yesterday and/or Today sections."""
        from keephive.commands.standup import cmd_standup

        cmd_standup([])
        out = capsys.readouterr().out
        assert "Standup" in out
        # daily_with_entries has a DONE, so Yesterday section should exist
        assert "Yesterday:" in out

    def test_standup_includes_blockers_section(self, hive_env, daily_with_entries, capsys):
        """Standup always includes Blockers section."""
        from keephive.commands.standup import cmd_standup

        cmd_standup([])
        out = capsys.readouterr().out
        assert "Blockers:" in out

    def test_empty_standup_gives_guidance(self, hive_env, capsys):
        """Empty standup shows how to start tracking work."""
        from keephive.commands.standup import cmd_standup

        cmd_standup([])
        out = capsys.readouterr().out
        assert "hive t" in out or "hive r" in out


class TestRecurringLifecycle:
    def test_daily_task_due_after_one_day(self, hive_env):
        """Daily task completed yesterday is due today."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import due_recurring, recurring_file

        cmd_recurring(["daily", "Check status"])
        # Simulate completion yesterday
        rf = recurring_file()
        content = rf.read_text()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        content += f"- Check status: {yesterday}\n"
        rf.write_text(content)

        due = due_recurring()
        texts = [text for _, text, _ in due]
        assert "Check status" in texts

    def test_weekly_not_due_for_seven_days(self, hive_env):
        """Weekly task completed today is not due."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import due_recurring

        cmd_recurring(["weekly", "Review facts"])
        cmd_recurring(["done", "Review"])

        due = due_recurring()
        texts = [text.lower() for _, text, _ in due]
        assert "review facts" not in texts

    def test_overdue_count_is_accurate(self, hive_env):
        """Task 3 days past daily deadline shows +2d overdue."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import due_recurring, recurring_file

        cmd_recurring(["daily", "Run tests"])
        # Simulate completion 3 days ago
        rf = recurring_file()
        content = rf.read_text()
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        content += f"- Run tests: {three_days_ago}\n"
        rf.write_text(content)

        due = due_recurring()
        for freq, text, overdue in due:
            if text == "Run tests":
                assert overdue == 2  # 3 days elapsed - 1 day interval = 2 overdue
                break
        else:
            pytest.fail("Run tests not found in due list")

    def test_done_clears_due_status(self, hive_env):
        """Completing a daily task makes it no longer due."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import due_recurring, recurring_file

        cmd_recurring(["daily", "Check status"])
        # Simulate completion yesterday so it's due today
        rf = recurring_file()
        content = rf.read_text()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        content += f"- Check status: {yesterday}\n"
        rf.write_text(content)

        # Verify it IS due before completing
        due = due_recurring()
        assert any(text == "Check status" for _, text, _ in due)

        # Complete it
        cmd_recurring(["done", "Check"])

        # Verify no longer due
        due = due_recurring()
        assert not any(text == "Check status" for _, text, _ in due)

        # Verify Last Completed entry has today's date
        content = rf.read_text()
        today_str = date.today().isoformat()
        assert f"Check status: {today_str}" in content

    def test_done_updates_existing_entry(self, hive_env):
        """Second completion updates the date, not adds a duplicate entry."""
        from keephive.commands.recurring import cmd_recurring
        from keephive.storage import recurring_file

        cmd_recurring(["daily", "Run test suite"])

        # First completion
        cmd_recurring(["done", "Run test"])
        content = recurring_file().read_text()
        today_str = date.today().isoformat()
        # Count occurrences of the completion entry
        matches = [line for line in content.splitlines() if line.startswith("- Run test suite:")]
        assert len(matches) == 1
        assert today_str in matches[0]

        # Second completion (should update, not duplicate)
        cmd_recurring(["done", "Run test"])
        content = recurring_file().read_text()
        matches = [line for line in content.splitlines() if line.startswith("- Run test suite:")]
        assert len(matches) == 1, f"Expected 1 entry, got {len(matches)}: {matches}"

    def test_done_pattern_matching(self, hive_env, capsys):
        """Partial pattern matches the correct recurring task."""
        from keephive.commands.recurring import cmd_recurring

        cmd_recurring(["daily", "Run full test suite"])
        cmd_recurring(["weekly", "Review stale facts"])

        # "test" should match "Run full test suite"
        cmd_recurring(["done", "test"])
        out = capsys.readouterr().out
        assert "Run full test suite" in out

        # "stale" should match "Review stale facts"
        cmd_recurring(["done", "stale"])
        out = capsys.readouterr().out
        assert "Review stale facts" in out


class TestVerifyVerdicts:
    def test_stale_correction_preserves_markdown(self, hive_env):
        """STALE correction keeps valid markdown list format."""
        from keephive.commands.verify import apply_verdicts
        from keephive.models import FactVerdict, Verdict, VerifyResponse

        mem_path = hive_env / "working" / "memory.md"
        today_str = date.today().isoformat()

        stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]

        response = VerifyResponse(
            verdicts=[
                FactVerdict(
                    index=1,
                    verdict=Verdict.STALE,
                    reason="Outdated",
                    correction="Python 3.14 is current",  # No leading "- "
                )
            ]
        )

        apply_verdicts(response, stale_facts, mem_path, today_str)
        mem = mem_path.read_text()
        # Should have added the "- " prefix
        assert "- Python 3.14 is current [verified:" in mem

    def test_multiple_verdicts_all_applied(self, hive_env):
        """All verdicts in a batch get applied to memory.md."""
        from keephive.commands.verify import apply_verdicts
        from keephive.models import FactVerdict, Verdict, VerifyResponse

        mem_path = hive_env / "working" / "memory.md"
        mem_path.write_text(
            "# Working Memory\n\n"
            "- Fact A [verified:2020-01-01]\n"
            "- Fact B [verified:2020-01-02]\n"
            "- Fresh fact [verified:2026-02-15]\n"
        )
        today_str = date.today().isoformat()

        stale_facts = [
            (3, "Fact A", "- Fact A [verified:2020-01-01]\n"),
            (4, "Fact B", "- Fact B [verified:2020-01-02]\n"),
        ]

        response = VerifyResponse(
            verdicts=[
                FactVerdict(index=1, verdict=Verdict.VALID, reason="OK"),
                FactVerdict(index=2, verdict=Verdict.UNCERTAIN, reason="Maybe"),
            ]
        )

        updated, refreshed = apply_verdicts(response, stale_facts, mem_path, today_str)

        assert updated == 1
        assert refreshed == 1
        mem = mem_path.read_text()
        # Both stale facts should now have today's date
        lines_with_today = [line for line in mem.splitlines() if f"[verified:{today_str}]" in line]
        assert len(lines_with_today) == 2


def _extract_todo_texts(output: str) -> list[str]:
    """Extract TODO text values from command output, preserving display order."""
    texts = []
    for line in output.splitlines():
        m = re.search(r"\[(?:today|\d+d)(?:\s+\d{2}:\d{2}(?::\d{2})?)?\]\s*(.+)", line)
        if m:
            # Strip trailing Rich Panel border chars (│) and whitespace
            texts.append(m.group(1).strip().rstrip("│").strip())
    return texts


def _extract_entry_timestamps(output: str) -> list[str]:
    """Extract HH:MM:SS timestamps from log entry lines, preserving display order."""
    timestamps = []
    for line in output.splitlines():
        m = re.search(r"\[(\d{2}:\d{2}:\d{2})\]", line)
        if m:
            timestamps.append(m.group(1))
    return timestamps


class TestNewestFirstOrdering:
    """All temporal lists must display newest items first."""

    def _make_multi_day_todos(self, hive_env):
        """Create TODOs across 3 different days with distinct names."""
        today_str = date.today().isoformat()
        one_day = (date.today() - timedelta(days=1)).isoformat()
        three_days = (date.today() - timedelta(days=3)).isoformat()

        (hive_env / "daily" / f"{three_days}.md").write_text(
            f"# Daily Log: {three_days}\n\n- [08:00:00] TODO: Deploy monitoring stack\n"
        )
        (hive_env / "daily" / f"{one_day}.md").write_text(
            f"# Daily Log: {one_day}\n\n- [14:00:00] TODO: Refactor auth module\n"
        )
        (hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n- [10:00:00] TODO: Fix pagination bug\n"
        )
        return ["Fix pagination bug", "Refactor auth module", "Deploy monitoring stack"]

    def test_todo_cmd_newest_first(self, hive_env, capsys):
        """hive todo displays newest TODO at the top."""
        expected_order = self._make_multi_day_todos(hive_env)

        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out

        displayed = _extract_todo_texts(out)
        assert displayed == expected_order, (
            f"Expected newest-first: {expected_order}, got: {displayed}"
        )

    def test_status_cmd_newest_first(self, hive_env, capsys):
        """hive s displays newest TODO at the top."""
        expected_order = self._make_multi_day_todos(hive_env)

        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out

        displayed = _extract_todo_texts(out)
        assert displayed == expected_order, (
            f"Expected newest-first: {expected_order}, got: {displayed}"
        )

    def test_mcp_todo_newest_first(self, hive_env):
        """MCP hive_todo displays newest TODO at the top."""
        expected_order = self._make_multi_day_todos(hive_env)

        from keephive.mcp_server import hive_todo

        result = hive_todo()

        displayed = _extract_todo_texts(result)
        assert displayed == expected_order, (
            f"Expected newest-first: {expected_order}, got: {displayed}"
        )

    def test_mcp_status_newest_first(self, hive_env):
        """MCP hive_status displays newest TODO at the top."""
        expected_order = self._make_multi_day_todos(hive_env)

        from keephive.mcp_server import hive_status

        result = hive_status()

        displayed = _extract_todo_texts(result)
        assert displayed == expected_order, (
            f"Expected newest-first: {expected_order}, got: {displayed}"
        )

    def test_status_entries_newest_first(self, hive_env, capsys):
        """hive s displays today's log entries newest-first."""
        today_str = date.today().isoformat()
        (hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [09:00:00] FACT: Early morning insight\n"
            "- [12:00:00] DECISION: Midday choice\n"
            "- [17:00:00] INSIGHT: Late afternoon pattern\n"
        )

        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out

        timestamps = _extract_entry_timestamps(out)
        # Filter to just the entry timestamps (not TODO timestamps)
        entry_ts = [t for t in timestamps if t in ("09:00:00", "12:00:00", "17:00:00")]
        assert entry_ts == ["17:00:00", "12:00:00", "09:00:00"], (
            f"Expected newest-first entries, got: {entry_ts}"
        )

    def test_recently_done_newest_first(self, hive_env, capsys):
        """hive todo Recently Done section shows newest completions first."""
        today_str = date.today().isoformat()
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()

        (hive_env / "daily" / f"{yesterday_str}.md").write_text(
            f"# Daily Log: {yesterday_str}\n\n"
            "- [10:00:00] TODO: Write unit tests\n"
            "- [15:00:00] DONE: Write unit tests\n"
        )
        (hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [09:00:00] TODO: Deploy staging\n"
            "- [11:00:00] DONE: Deploy staging\n"
        )

        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out

        if "Recently Done" in out:
            done_section = out.split("Recently Done")[1]
            done_pos_staging = done_section.find("Deploy staging")
            done_pos_tests = done_section.find("Write unit tests")
            assert done_pos_staging < done_pos_tests, (
                "Today's completion should appear before yesterday's"
            )


class TestConsistency:
    """All surfaces showing the same data must agree on content and order."""

    def test_todo_order_matches_across_surfaces(self, hive_env, capsys):
        """CLI todo, CLI status, and MCP todo show same order."""
        today_str = date.today().isoformat()
        three_days = (date.today() - timedelta(days=3)).isoformat()
        (hive_env / "daily" / f"{three_days}.md").write_text(
            f"# Daily Log: {three_days}\n\n- [08:00:00] TODO: Oldest task from days ago\n"
        )
        (hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n- [10:00:00] TODO: Recent task from today\n"
        )

        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        todo_texts = _extract_todo_texts(capsys.readouterr().out)

        from keephive.commands.status import cmd_status

        cmd_status([])
        status_texts = _extract_todo_texts(capsys.readouterr().out)

        from keephive.mcp_server import hive_todo

        mcp_texts = _extract_todo_texts(hive_todo())

        assert todo_texts == status_texts == mcp_texts, (
            f"Ordering mismatch:\n"
            f"  todo:   {todo_texts}\n"
            f"  status: {status_texts}\n"
            f"  mcp:    {mcp_texts}"
        )

    def test_mcp_status_matches_cli_count(self, hive_env):
        """MCP hive_status shows same TODO count as CLI status."""
        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] TODO: Task alpha\n"
            "- [10:01:00] TODO: Task beta\n"
        )
        from keephive.mcp_server import hive_todo

        mcp_result = hive_todo()
        assert "2 open TODO" in mcp_result

        from keephive.storage import open_todos

        todos = open_todos()
        assert len(todos) == 2


class TestAgeLabels:
    """Age label formatting must be consistent everywhere."""

    def test_1d_age_label_in_todo(self, hive_env, capsys):
        """TODO from yesterday shows '1d', not '1d' via different logic paths."""
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        (hive_env / "daily" / f"{yesterday_str}.md").write_text(
            f"# Daily Log: {yesterday_str}\n\n- [10:00:00] TODO: Yesterday task\n"
        )

        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        assert "[1d" in out, f"Expected '[1d' age label, got:\n{out}"

    def test_1d_age_label_in_status(self, hive_env, capsys):
        """Status shows '1d' for yesterday's TODO."""
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        (hive_env / "daily" / f"{yesterday_str}.md").write_text(
            f"# Daily Log: {yesterday_str}\n\n- [10:00:00] TODO: Yesterday task\n"
        )

        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "[1d" in out, f"Expected '[1d' age label, got:\n{out}"

    def test_1d_age_label_in_mcp_todo(self, hive_env):
        """MCP hive_todo shows '1d' for yesterday's TODO."""
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        (hive_env / "daily" / f"{yesterday_str}.md").write_text(
            f"# Daily Log: {yesterday_str}\n\n- [10:00:00] TODO: Yesterday task\n"
        )

        from keephive.mcp_server import hive_todo

        result = hive_todo()
        assert "[1d" in result, f"Expected '[1d' age label, got:\n{result}"

    def test_1d_age_label_in_recently_done(self, hive_env, capsys):
        """Recently Done section shows '1d' for yesterday's completions."""
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        (hive_env / "daily" / f"{yesterday_str}.md").write_text(
            f"# Daily Log: {yesterday_str}\n\n"
            "- [10:00:00] TODO: Completed task\n"
            "- [11:00:00] DONE: Completed task\n"
        )

        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        if "Recently Done" in out:
            done_section = out.split("Recently Done")[1]
            assert "[1d]" in done_section, f"Expected '[1d]' in Recently Done, got:\n{done_section}"

    def test_age_labels_cover_all_branches(self, hive_env, capsys):
        """Verify today/1d/Nd labels all render correctly in one output."""
        today_str = date.today().isoformat()
        one_day = (date.today() - timedelta(days=1)).isoformat()
        five_days = (date.today() - timedelta(days=5)).isoformat()

        (hive_env / "daily" / f"{five_days}.md").write_text(
            f"# Daily Log: {five_days}\n\n- [08:00:00] TODO: Ancient relic task\n"
        )
        (hive_env / "daily" / f"{one_day}.md").write_text(
            f"# Daily Log: {one_day}\n\n- [12:00:00] TODO: Middling priority task\n"
        )
        (hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n- [16:00:00] TODO: Fresh urgent task\n"
        )

        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out

        assert "[today" in out, "Missing 'today' age label"
        assert "[1d" in out, "Missing '1d' age label"
        assert "[5d" in out, "Missing '5d' age label"


class TestLogDateParsing:
    """Tests for hive l <date> navigation."""

    def test_log_today_default(self, hive_env, daily_with_entries, capsys):
        """hive l with no args shows today's log."""
        from keephive.commands.log import cmd_log

        cmd_log([])
        out = capsys.readouterr().out
        assert "Daily Log" in out

    def test_log_yesterday(self, hive_env, capsys):
        """hive l yesterday shows yesterday's log or nearby."""
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        (hive_env / "daily" / f"{yesterday_str}.md").write_text(
            f"# Daily Log: {yesterday_str}\n\n- [10:00:00] FACT: Yesterday's entry\n"
        )
        from keephive.commands.log import cmd_log

        cmd_log(["yesterday"])
        out = capsys.readouterr().out
        assert "Yesterday's entry" in out

    def test_log_days_ago(self, hive_env, capsys):
        """hive l 3 shows log from 3 days ago."""
        three_days = (date.today() - timedelta(days=3)).isoformat()
        (hive_env / "daily" / f"{three_days}.md").write_text(
            f"# Daily Log: {three_days}\n\n- [09:00:00] FACT: Three day old entry\n"
        )
        from keephive.commands.log import cmd_log

        cmd_log(["3"])
        out = capsys.readouterr().out
        assert "Three day old entry" in out

    def test_log_iso_date(self, hive_env, capsys):
        """hive l 2026-02-15 shows that specific date's log."""
        (hive_env / "daily" / "2026-02-15.md").write_text(
            "# Daily Log: 2026-02-15\n\n- [10:00:00] FACT: Specific date entry\n"
        )
        from keephive.commands.log import cmd_log

        cmd_log(["2026-02-15"])
        out = capsys.readouterr().out
        assert "Specific date entry" in out

    def test_log_missing_shows_nearby(self, hive_env, capsys):
        """hive l for missing date shows nearby logs."""
        today_str = date.today().isoformat()
        (hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n- [10:00:00] FACT: Entry\n"
        )
        from keephive.commands.log import cmd_log

        cmd_log(["99"])  # 99 days ago, no log
        out = capsys.readouterr().out
        assert "No log for" in out
        assert "Nearby" in out
        assert today_str in out

    def test_parse_date_arg_empty(self):
        """Empty arg returns today."""
        from keephive.commands.log import _parse_date_arg

        assert _parse_date_arg("") == date.today().isoformat()

    def test_parse_date_arg_yesterday(self):
        """'yesterday' returns yesterday's ISO date."""
        from keephive.commands.log import _parse_date_arg

        expected = (date.today() - timedelta(days=1)).isoformat()
        assert _parse_date_arg("yesterday") == expected

    def test_parse_date_arg_digit(self):
        """Digit arg returns N days ago."""
        from keephive.commands.log import _parse_date_arg

        expected = (date.today() - timedelta(days=5)).isoformat()
        assert _parse_date_arg("5") == expected

    def test_parse_date_arg_iso(self):
        """ISO date passes through unchanged."""
        from keephive.commands.log import _parse_date_arg

        assert _parse_date_arg("2026-01-15") == "2026-01-15"


class TestMcpLogDateParsing:
    """Tests for MCP hive_log() date support."""

    def test_mcp_log_yesterday(self, hive_env):
        """MCP hive_log('yesterday') returns yesterday's log."""
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        (hive_env / "daily" / f"{yesterday_str}.md").write_text(
            f"# Daily Log: {yesterday_str}\n\n- [10:00:00] FACT: Yesterday via MCP\n"
        )
        from keephive.mcp_server import hive_log

        result = hive_log("yesterday")
        assert "Yesterday via MCP" in result

    def test_mcp_log_days_ago(self, hive_env):
        """MCP hive_log('2') returns log from 2 days ago."""
        two_days = (date.today() - timedelta(days=2)).isoformat()
        (hive_env / "daily" / f"{two_days}.md").write_text(
            f"# Daily Log: {two_days}\n\n- [10:00:00] FACT: Two days ago via MCP\n"
        )
        from keephive.mcp_server import hive_log

        result = hive_log("2")
        assert "Two days ago via MCP" in result

    def test_mcp_log_missing_shows_nearby(self, hive_env):
        """MCP hive_log for missing date shows nearby logs."""
        today_str = date.today().isoformat()
        (hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n- [10:00:00] FACT: Entry\n"
        )
        from keephive.mcp_server import hive_log

        result = hive_log("99")
        assert "No log for" in result
        assert "Nearby" in result


class TestReflectApply:
    """Tests for hive rf apply."""

    def _write_analysis(self, hive_env, additions=None, contradictions=None):
        """Write a fake .last-analyze.json."""
        import json

        data = {
            "patterns": [],
            "additions": additions or [],
            "contradictions": contradictions or [],
            "actions": [],
        }
        (hive_env / ".last-analyze.json").write_text(json.dumps(data, indent=2))

    def test_apply_no_analysis(self, hive_env, capsys):
        """rf apply with no prior analysis shows guidance."""
        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["apply"])
        out = capsys.readouterr().out
        assert "No pending analysis" in out
        assert "hive rf analyze" in out

    def test_apply_empty_analysis(self, hive_env, capsys):
        """rf apply with empty analysis shows nothing to review."""
        self._write_analysis(hive_env)
        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["apply"])
        out = capsys.readouterr().out
        assert "no additions or contradictions" in out.lower()

    def test_apply_addition_yes(self, hive_env, capsys, monkeypatch):
        """Approving an addition writes it to memory.md."""
        self._write_analysis(
            hive_env,
            additions=[
                {"fact": "uv is the preferred package manager", "source": "2026-02-17"},
            ],
        )
        # Simulate user typing "y"
        monkeypatch.setattr("builtins.input", lambda prompt: "y")

        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["apply"])
        out = capsys.readouterr().out

        assert "Added to memory.md" in out
        mem = (hive_env / "working" / "memory.md").read_text()
        assert "uv is the preferred package manager" in mem
        assert "[verified:" in mem

    def test_apply_addition_skip(self, hive_env, capsys, monkeypatch):
        """Skipping an addition does not modify memory.md."""
        self._write_analysis(
            hive_env,
            additions=[
                {"fact": "Should not appear", "source": "2026-02-17"},
            ],
        )
        monkeypatch.setattr("builtins.input", lambda prompt: "n")

        mem_before = (hive_env / "working" / "memory.md").read_text()
        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["apply"])
        out = capsys.readouterr().out

        assert "Skipped" in out
        mem_after = (hive_env / "working" / "memory.md").read_text()
        assert mem_before == mem_after

    def test_apply_contradiction_update(self, hive_env, capsys, monkeypatch):
        """Updating a contradiction replaces the old fact in memory.md."""
        # Memory has "Python is great" (from conftest)
        self._write_analysis(
            hive_env,
            contradictions=[
                {
                    "memory": "Python is great",
                    "log": "Python 3.13 is the latest stable release",
                    "date": "2026-02-17",
                },
            ],
        )
        monkeypatch.setattr("builtins.input", lambda prompt: "u")

        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["apply"])
        out = capsys.readouterr().out

        assert "Updated in memory.md" in out
        mem = (hive_env / "working" / "memory.md").read_text()
        assert "Python 3.13 is the latest stable release" in mem

    def test_apply_summary_counts(self, hive_env, capsys, monkeypatch):
        """Apply shows correct counts at the end."""
        self._write_analysis(
            hive_env,
            additions=[
                {"fact": "fact one", "source": "2026-02-17"},
                {"fact": "fact two", "source": "2026-02-17"},
            ],
            contradictions=[
                {"memory": "old", "log": "new", "date": "2026-02-17"},
            ],
        )
        # Approve first addition, skip second, skip contradiction
        inputs = iter(["y", "n", "s"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["apply"])
        out = capsys.readouterr().out

        assert "1 added" in out
        assert "0 updated" in out
        assert "2 skipped" in out


class TestStatusReflectNudge:
    """Tests for reflect analysis nudge in status output."""

    def test_nudge_when_analysis_exists(self, hive_env, capsys):
        """Status shows nudge when fresh .last-analyze.json exists."""
        import json

        data = {
            "patterns": [],
            "additions": [{"fact": "test", "source": "today"}],
            "contradictions": [{"memory": "old", "log": "new", "date": "today"}],
            "actions": [],
        }
        (hive_env / ".last-analyze.json").write_text(json.dumps(data))

        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "reflect" in out.lower()
        assert "addition" in out or "contradiction" in out

    def test_no_nudge_when_no_analysis(self, hive_env, capsys):
        """Status shows no nudge when no analysis exists."""
        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "reflect:" not in out.lower()

    def test_no_nudge_when_analysis_old(self, hive_env, capsys):
        """Status shows no nudge when analysis is older than 24h."""
        import json
        import os
        import time

        data = {
            "patterns": [],
            "additions": [{"fact": "test", "source": "today"}],
            "contradictions": [],
            "actions": [],
        }
        path = hive_env / ".last-analyze.json"
        path.write_text(json.dumps(data))
        # Set mtime to 25 hours ago
        old_time = time.time() - 25 * 3600
        os.utime(path, (old_time, old_time))

        from keephive.commands.status import cmd_status

        cmd_status([])
        out = capsys.readouterr().out
        assert "hive rf apply" not in out


class TestRecallDeepFlag:
    """Tests for recall --deep flag."""

    def test_deep_without_llm_returns_normal(self, hive_env, capsys):
        """--deep with HIVE_SKIP_LLM=1 returns normal results (no LLM expansion)."""
        from keephive.commands.remember import cmd_recall

        cmd_recall(["Python", "--deep"])
        out = capsys.readouterr().out
        assert "Python" in out
        # Should not show "Expanding" status since LLM is skipped
        assert "Expanding" not in out

    def test_recall_without_deep_is_instant(self, hive_env, capsys):
        """Normal recall does not trigger LLM expansion."""
        from keephive.commands.remember import cmd_recall

        cmd_recall(["Python"])
        out = capsys.readouterr().out
        assert "result" in out.lower()


class TestSessionStartOutput:
    def test_stale_warning_injected(self, hive_env):
        """SessionStart context includes stale fact warning."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "unverified 30+ days" in ctx.lower()
        assert "hive v" in ctx

    def test_todos_injected_with_age(self, hive_env):
        """SessionStart context includes TODO items with age labels."""
        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(f"# Daily Log: {today_str}\n\n- [10:00:00] TODO: Test task\n")
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "TODO" in ctx
        assert "today" in ctx

    def test_workflows_section_not_statically_injected(self, hive_env):
        """Workflows section removed from static injection to cut token bloat.
        It lives in the keephive-guide and is injected only when that guide matches."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "## Workflows" not in ctx
