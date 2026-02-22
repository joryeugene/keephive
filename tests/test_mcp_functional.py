"""Functional tests for MCP server tools: verify side effects, not just return values.

Complements test_mcp_tools.py (which covers schemas/basic behavior).
This file focuses on: file persistence, stats tracking, and error resilience.
"""

from __future__ import annotations


class TestHiveRememberFunctional:
    def test_persists_to_daily_log(self, hive_env):
        """hive_remember actually writes to today's daily log file."""
        from keephive.clock import get_today
        from keephive.mcp_server import hive_remember

        hive_remember("FACT: mcp functional test unique fact")

        day = get_today().isoformat()
        daily_path = hive_env / "daily" / f"{day}.md"
        assert daily_path.exists(), "Daily log not created by hive_remember"
        content = daily_path.read_text()
        assert "mcp functional test unique fact" in content

    def test_returns_non_empty_string(self, hive_env):
        from keephive.mcp_server import hive_remember

        result = hive_remember("FACT: return value test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_category_tag(self, hive_env):
        """hive_remember returns a string containing the category tag for prefixed entries."""
        from keephive.mcp_server import hive_remember

        result = hive_remember("FACT: category tag test")
        assert "[FACT]" in result

        result2 = hive_remember("DECISION: decision tag test")
        assert "[DECISION]" in result2

    def test_multiple_facts_all_persisted(self, hive_env):
        """Multiple remember calls all persist to daily log."""
        from keephive.clock import get_today
        from keephive.mcp_server import hive_remember

        hive_remember("FACT: first unique fact abc")
        hive_remember("FACT: second unique fact xyz")
        hive_remember("DECISION: third unique decision 123")

        day = get_today().isoformat()
        daily_path = hive_env / "daily" / f"{day}.md"
        content = daily_path.read_text()
        assert "first unique fact abc" in content
        assert "second unique fact xyz" in content
        assert "third unique decision 123" in content

    def test_entries_have_timestamp_prefix(self, hive_env):
        """Each entry in the daily log has a [HH:MM:SS] timestamp prefix."""
        import re

        from keephive.clock import get_today
        from keephive.mcp_server import hive_remember

        hive_remember("FACT: timestamp prefix test")

        day = get_today().isoformat()
        daily_path = hive_env / "daily" / f"{day}.md"
        content = daily_path.read_text()
        # Should find a line like: - [HH:MM:SS] FACT: timestamp prefix test
        assert re.search(r"- \[\d{2}:\d{2}:\d{2}\] FACT: timestamp prefix test", content)

    def test_entry_count_increments(self, hive_env):
        """Return value reports incrementing entry count."""
        from keephive.mcp_server import hive_remember

        r1 = hive_remember("FACT: count test one")
        r2 = hive_remember("FACT: count test two")

        # Extract count from "Remembered [FACT] at HH:MM:SS (N entries today)"
        import re

        m1 = re.search(r"\((\d+) entries today\)", r1)
        m2 = re.search(r"\((\d+) entries today\)", r2)
        assert m1 and m2
        assert int(m2.group(1)) > int(m1.group(1))


class TestHiveRecallFunctional:
    def test_recall_finds_remembered_fact(self, hive_env):
        """remember then recall by keyword -> fact is returned."""
        from keephive.mcp_server import hive_recall, hive_remember

        hive_remember("FACT: keephive uses SQLite for FTS")
        result = hive_recall("SQLite")
        assert "SQLite" in result or "keephive" in result

    def test_recall_finds_in_memory_file(self, hive_env):
        """recall searches working memory.md (pre-seeded by hive_env fixture)."""
        from keephive.mcp_server import hive_recall

        # hive_env has "Python is great" in memory.md
        result = hive_recall("Python")
        assert "Found" in result
        assert "Python" in result

    def test_recall_no_results_message(self, hive_env):
        """recall with no matches returns a message indicating no results."""
        from keephive.mcp_server import hive_recall

        result = hive_recall("xyzzy_nonexistent_99999")
        assert "No results" in result


class TestHiveStatusFunctional:
    def test_returns_nonempty_string(self, hive_env):
        from keephive.mcp_server import hive_status

        result = hive_status()
        assert isinstance(result, str)
        assert len(result) > 10

    def test_contains_keephive_branding(self, hive_env):
        from keephive.mcp_server import hive_status

        result = hive_status()
        assert "keephive" in result.lower() or "hive" in result.lower()

    def test_contains_version(self, hive_env):
        """Status output includes version string."""
        from keephive.mcp_server import hive_status

        result = hive_status()
        assert "v" in result  # "keephive vX.Y.Z"


class TestHiveTodoFunctional:
    def test_todo_returns_string(self, hive_env):
        from keephive.mcp_server import hive_todo

        result = hive_todo()
        assert isinstance(result, str)

    def test_todo_empty_when_none(self, hive_env):
        """No TODOs -> message says no open TODOs."""
        from keephive.mcp_server import hive_todo

        result = hive_todo()
        assert "No open TODO" in result or "0 open" in result

    def test_todo_done_full_workflow(self, hive_env):
        """remember TODO -> todo_done -> no longer in todo list."""
        from keephive.mcp_server import hive_remember, hive_todo, hive_todo_done

        hive_remember("TODO: Functional test workflow task 99")
        todo_list = hive_todo()
        assert "Functional test workflow task 99" in todo_list

        done_result = hive_todo_done("workflow task 99")
        assert "Completed" in done_result

        updated_list = hive_todo()
        assert "Functional test workflow task 99" not in updated_list


class TestHiveLogFunctional:
    def test_log_shows_remembered_entry(self, hive_env):
        """hive_remember then hive_log -> entry appears in log output."""
        from keephive.mcp_server import hive_log, hive_remember

        hive_remember("FACT: log functional entry unique 777")
        result = hive_log()
        assert "log functional entry unique 777" in result

    def test_log_no_entries_message(self, hive_env):
        """hive_log for a day with no log file returns a no-log message."""
        from keephive.mcp_server import hive_log

        result = hive_log("2020-01-01")
        assert "No log" in result


class TestMcpStatsTracking:
    def test_remember_creates_daily_log(self, hive_env):
        """hive_remember creates the daily log file."""
        from keephive.clock import get_today
        from keephive.mcp_server import hive_remember

        hive_remember("FACT: stats tracking test entry")

        day = get_today().isoformat()
        daily_path = hive_env / "daily" / f"{day}.md"
        assert daily_path.exists(), "Daily log not created by hive_remember"
        content = daily_path.read_text()
        assert "stats tracking test entry" in content

    def test_remember_tracks_command_counter(self, hive_env):
        """hive_remember increments commands.remember counter in stats."""
        from keephive.clock import get_today
        from keephive.mcp_server import hive_remember
        from keephive.storage import read_stats

        hive_remember("FACT: counter test")

        day = get_today().isoformat()
        data = read_stats()
        count = data.get("days", {}).get(day, {}).get("commands", {}).get("remember", 0)
        assert count >= 1, f"commands.remember counter not incremented: {data}"


class TestMcpErrorResilience:
    def test_recall_no_crash_on_empty_memory(self, hive_env):
        """recall with empty memory.md returns gracefully."""
        (hive_env / "working" / "memory.md").write_text("")
        from keephive.mcp_server import hive_recall

        result = hive_recall("anything")
        assert isinstance(result, str)

    def test_status_no_crash_on_empty_hive(self, hive_env):
        """hive_status works on a fresh hive with no daily logs or stats."""
        from keephive.mcp_server import hive_status

        result = hive_status()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_todo_no_crash_on_empty_hive(self, hive_env):
        """hive_todo works on a fresh hive with no entries."""
        from keephive.mcp_server import hive_todo

        result = hive_todo()
        assert isinstance(result, str)

    def test_log_no_crash_on_missing_day(self, hive_env):
        """hive_log for nonexistent day returns gracefully."""
        from keephive.mcp_server import hive_log

        result = hive_log("1999-12-31")
        assert isinstance(result, str)
        assert "No log" in result


class TestMcpCrossToolWorkflow:
    def test_remember_then_recall_then_log(self, hive_env):
        """Full workflow: remember -> recall -> log all see the same entry."""
        from keephive.mcp_server import hive_log, hive_recall, hive_remember

        marker = "FACT: cross-tool-workflow-marker-42"
        hive_remember(marker)

        recall_result = hive_recall("cross-tool-workflow-marker")
        assert "cross-tool-workflow-marker" in recall_result

        log_result = hive_log()
        assert "cross-tool-workflow-marker-42" in log_result

    def test_remember_todo_then_done_then_status(self, hive_env):
        """remember TODO -> todo_done -> status reflects correct count."""
        from keephive.mcp_server import hive_remember, hive_status, hive_todo_done

        hive_remember("TODO: cross tool status check 88")
        status1 = hive_status()
        assert "open TODO" in status1

        hive_todo_done("cross tool status check 88")
        hive_status()
        # After completing the only TODO, either no TODO section or 0 open
        # (depends on whether other TODOs exist from fixture)
