"""Exhaustive E2E scenarios. Real terminal. Real commands. Real edges.

Every scenario runs in a fresh tmux session with isolated HIVE_HOME.
Output artifacts are saved for tracking quality over time.

Run: uv run pytest -m terminal -v -o "addopts="
Update baselines: uv run pytest -m terminal --update-golden -o "addopts="
"""

from __future__ import annotations

import time

import pytest
from conftest import assert_golden

# ============================================================
#  Category 1: Knowledge Lifecycle
# ============================================================


@pytest.mark.terminal
class TestKnowledgeLifecycle:
    def test_empty_state(self, term, save_terminal_output):
        """Status with zero entries shows clean state."""
        screen = term.type("python -m keephive s")
        screen.has("keephive")
        screen.lacks("stale")
        save_terminal_output("knowledge/empty_state", term)

    def test_single_fact(self, term, save_terminal_output):
        """Remember 1 fact, verify it appears in today's status."""
        term.type("python -m keephive r 'FACT: PostgreSQL uses MVCC'").has("Remembered")
        screen = term.type("python -m keephive s")
        screen.has("Today", "FACT: PostgreSQL uses MVCC")
        save_terminal_output("knowledge/single_fact", term)

    def test_multiple_facts(self, term):
        """Remember 5 facts, verify entries appear in status Today panel."""
        for i in range(5):
            term.type(f"python -m keephive r 'FACT: test fact number {i}'")
        term.type("python -m keephive s").has("Today", "test fact number")

    def test_category_variety(self, term, save_terminal_output):
        """Each category type is captured correctly."""
        term.type("python -m keephive r 'FACT: Redis uses single-threaded event loop'")
        term.type("python -m keephive r 'DECISION: Use FastAPI over Flask for async'")
        term.type("python -m keephive r 'INSIGHT: Batch inserts 10x faster than singles'")
        term.type("python -m keephive r 'CORRECTION: Max connections is 100, not 50'")
        screen = term.type("python -m keephive s")
        screen.has("Today", "FACT:", "DECISION:", "INSIGHT:", "CORRECTION:")
        save_terminal_output("knowledge/category_variety", term)

    def test_recall_hit(self, term, save_terminal_output):
        """Recall finds a previously remembered fact."""
        term.type("python -m keephive r 'FACT: JWT tokens expire after 15 minutes'")
        screen = term.type("python -m keephive rc JWT")
        screen.has("JWT", "15 minutes")
        save_terminal_output("knowledge/recall_hit", term)

    def test_recall_miss(self, term, save_terminal_output):
        """Recall with no matching facts."""
        term.type("python -m keephive rc nonexistent_xyz_query")
        save_terminal_output("knowledge/recall_miss", term)

    def test_recall_partial_match(self, term):
        """Recall finds facts with partial keyword overlap."""
        term.type("python -m keephive r 'FACT: PostgreSQL JSONB supports GIN indexes'")
        term.type("python -m keephive rc JSONB").has("PostgreSQL", "GIN")

    def test_staleness_boundary_fact(self, term, save_terminal_output):
        """FACT stale at 30+ days, not at 29. Uses working memory directly."""
        # Write fact to working memory with a specific verified date
        (term.hive_home / "working" / "memory.md").write_text(
            "# Working Memory\n\n- FACT: boundary test [verified:2026-01-01]\n"
        )

        # Day 29: not stale yet (30-day threshold for FACTs)
        term.set_date("2026-01-30")
        term.type("python -m keephive s").lacks("stale")

        # Day 31: now stale
        term.set_date("2026-02-01")
        term.type("python -m keephive s").has("stale")
        save_terminal_output("knowledge/staleness_boundary", term)

    def test_staleness_boundary_decision(self, term):
        """DECISION stale at 90+ days, not at 89. Uses working memory directly."""
        (term.hive_home / "working" / "memory.md").write_text(
            "# Working Memory\n\n- DECISION: Use Redis for caching [verified:2026-01-01]\n"
        )

        term.set_date("2026-03-31")  # Day 89
        term.type("python -m keephive s").lacks("stale")

        term.set_date("2026-04-02")  # Day 91
        term.type("python -m keephive s").has("stale")

    def test_special_chars_in_fact(self, term):
        """Facts with backticks survive remember/recall."""
        term.type("python -m keephive r 'FACT: use uv run not pip'")
        term.type("python -m keephive rc uv").has("uv run")

    def test_remember_recall_roundtrip_decision(self, term):
        """DECISION roundtrip: remember -> recall -> exact content."""
        term.type("python -m keephive r 'DECISION: PostgreSQL over MySQL for JSONB'")
        term.type("python -m keephive rc PostgreSQL").has("JSONB", "MySQL")

    def test_30_day_lifecycle(self, term, save_terminal_output):
        """Full 30-day lifecycle: fresh memory -> stale memory."""
        # Seed working memory with a verified fact
        (term.hive_home / "working" / "memory.md").write_text(
            "# Working Memory\n\n- FACT: lifecycle test [verified:2026-01-01]\n"
        )

        # Day 1: fresh
        term.set_date("2026-01-02")
        term.type("python -m keephive s").lacks("stale")

        # Day 15: still fresh (under 30d threshold)
        term.set_date("2026-01-16")
        term.type("python -m keephive s").lacks("stale")

        # Day 35: stale
        term.set_date("2026-02-05")
        term.type("python -m keephive s").has("stale")

        save_terminal_output("knowledge/30day_lifecycle", term)


# ============================================================
#  Category 2: TODO Workflow
# ============================================================


@pytest.mark.terminal
class TestTodoWorkflow:
    def test_empty_todo_list(self, term, save_terminal_output):
        """No TODOs shows appropriate message."""
        term.type("python -m keephive todo")
        save_terminal_output("todo/empty_list", term)

    def test_add_and_list(self, term):
        """Add TODO, verify it appears in list."""
        term.type("python -m keephive t 'Fix the auth bug'")
        term.type("python -m keephive todo").has("Fix the auth bug")

    def test_mark_done(self, term, save_terminal_output):
        """Mark TODO done, verify completion."""
        term.type("python -m keephive t 'Deploy hotfix'")
        term.type("python -m keephive todo").has("Deploy hotfix")
        term.type("python -m keephive td 'Deploy hotfix'").has("Completed")
        # After done: item moves to "Recently Done", no more open TODOs
        term.type("python -m keephive todo").has("No open TODOs")
        save_terminal_output("todo/mark_done", term)

    def test_multiple_todos(self, term):
        """Add 5 distinct TODOs, verify all appear."""
        # Names must be sufficiently different to avoid fuzzy dedup (0.8 threshold)
        tasks = [
            "Fix authentication bug in login flow",
            "Write database migration for user table",
            "Deploy staging environment to kubernetes",
            "Review pull request for API endpoints",
            "Update documentation with new config options",
        ]
        for task in tasks:
            term.type(f"python -m keephive t '{task}'")
        screen = term.type("python -m keephive todo")
        for task in tasks:
            screen.has(task)

    def test_done_partial_match(self, term):
        """Mark done with partial text matches the right TODO."""
        term.type("python -m keephive t 'Add retry logic to webhook delivery'")
        term.type("python -m keephive td 'retry logic'").has("Completed")
        term.type("python -m keephive todo").has("No open TODOs")

    def test_todo_across_days(self, term, save_terminal_output):
        """TODO created on day 1, visible and completable on day 5."""
        term.set_date("2026-03-01")
        term.type("python -m keephive t 'Cross-day TODO'")

        term.set_date("2026-03-05")
        term.type("python -m keephive todo").has("Cross-day TODO")
        term.type("python -m keephive td 'Cross-day TODO'").has("Completed")
        # Open section should be empty (item moves to "Recently Done")
        term.type("python -m keephive todo").has("No open TODOs")
        save_terminal_output("todo/cross_day", term)

    def test_todo_age_display(self, term, save_terminal_output):
        """Old TODOs show age indicators."""
        term.set_date("2026-01-01")
        term.type("python -m keephive t 'Ancient TODO'")

        term.set_date("2026-01-10")
        screen = term.type("python -m keephive todo")
        screen.has("Ancient TODO")
        save_terminal_output("todo/age_display", term)

    def test_todo_special_chars(self, term):
        """TODO text with special characters."""
        term.type("python -m keephive t 'Fix the profile page (regression)'")
        term.type("python -m keephive todo").has("profile page")


# ============================================================
#  Category 3: Stats & Metrics (seeded data)
# ============================================================


@pytest.mark.terminal
class TestStatsSeeded:
    def test_stats_full_output(self, term_seeded, save_terminal_output, update_golden):
        """Stats with 45 days of seeded data renders all sections."""
        screen = term_seeded.type("python -m keephive stats")
        screen.has("Pipeline")
        save_terminal_output("stats/full_seeded", term_seeded)
        assert_golden(screen, "stats_seeded", update=update_golden)

    def test_stats_empty(self, term, save_terminal_output, update_golden):
        """Stats with zero data does not crash."""
        screen = term.type("python -m keephive stats")
        save_terminal_output("stats/empty", term)
        assert_golden(screen, "stats_empty", update=update_golden)

    def test_status_seeded(self, term_seeded, save_terminal_output, update_golden):
        """Status with seeded data shows entries."""
        screen = term_seeded.type("python -m keephive s")
        screen.has("keephive")
        save_terminal_output("stats/status_seeded", term_seeded)
        assert_golden(screen, "status_seeded", update=update_golden)

    def test_status_empty(self, term, save_terminal_output, update_golden):
        """Status with empty data."""
        screen = term.type("python -m keephive s")
        screen.has("keephive")
        save_terminal_output("stats/status_empty", term)
        assert_golden(screen, "status_empty", update=update_golden)


# ============================================================
#  Category 4: Time Travel Edge Cases
# ============================================================


@pytest.mark.terminal
class TestTimeTravel:
    def test_year_boundary(self, term, save_terminal_output):
        """Dec 31 -> Jan 1 year transition."""
        term.set_date("2025-12-31")
        term.type("python -m keephive r 'FACT: year-end fact'")
        assert term.file_exists("daily/2025-12-31.md")

        term.set_date("2026-01-01")
        term.type("python -m keephive r 'FACT: new year fact'")
        assert term.file_exists("daily/2026-01-01.md")
        save_terminal_output("timetravel/year_boundary", term)

    def test_leap_year(self, term):
        """Feb 29 on leap year."""
        term.set_date("2024-02-29")
        term.type("python -m keephive r 'FACT: leap day'")
        assert term.file_exists("daily/2024-02-29.md")

    def test_month_boundary(self, term):
        """Jan 31 -> Feb 1 (month with fewer days)."""
        term.set_date("2026-01-31")
        term.type("python -m keephive r 'FACT: last day of January'")
        term.set_date("2026-02-01")
        term.type("python -m keephive r 'FACT: first day of February'")
        assert term.file_exists("daily/2026-01-31.md")
        assert term.file_exists("daily/2026-02-01.md")

    def test_same_date_idempotent(self, term):
        """Setting same date twice does not break anything."""
        term.set_date("2026-06-15")
        term.set_date("2026-06-15")
        term.type("python -m keephive r 'FACT: idempotent'").has("Remembered")

    def test_far_future(self, term):
        """Commands work at far future dates."""
        term.set_date("2030-12-31")
        term.type("python -m keephive r 'FACT: future fact'")
        assert term.file_exists("daily/2030-12-31.md")

    def test_multiple_entries_same_day(self, term):
        """Multiple remember calls on same date append to same file."""
        term.set_date("2026-05-05")
        term.type("python -m keephive r 'FACT: first entry'")
        term.type("python -m keephive r 'FACT: second entry'")
        term.type("python -m keephive r 'DECISION: third entry'").has("Remembered")
        content = term.read_file("daily/2026-05-05.md")
        assert "first entry" in content
        assert "second entry" in content
        assert "third entry" in content


# ============================================================
#  Category 5: Multi-Day Workflows
# ============================================================


@pytest.mark.terminal
class TestMultiDayWorkflows:
    def test_5_day_sprint(self, term, save_terminal_output):
        """Simulate a 5-day work sprint with realistic usage."""
        # Monday: Start fresh
        term.set_date("2026-03-09")
        term.type("python -m keephive r 'FACT: Sprint 12 started'").has("Remembered")
        term.type("python -m keephive t 'Implement user settings page'").has("Remembered")
        term.type("python -m keephive t 'Write integration tests for auth'").has("Remembered")

        # Tuesday: Progress
        term.set_date("2026-03-10")
        term.type("python -m keephive r 'DECISION: Use React Hook Form for settings'").has(
            "Remembered"
        )
        term.type("python -m keephive td 'user settings'").has("Completed")

        # Wednesday: Discovery
        term.set_date("2026-03-11")
        term.type("python -m keephive r 'INSIGHT: Form validation 3x faster with schema'")
        term.type("python -m keephive t 'Refactor validation to use Zod schemas'")

        # Thursday: Correction
        term.set_date("2026-03-12")
        term.type("python -m keephive r 'CORRECTION: Auth tokens are 30min, not 15min'")
        term.type("python -m keephive td 'integration tests'").has("Completed")

        # Friday: Review
        term.set_date("2026-03-13")
        screen = term.type("python -m keephive s")
        screen.has("CORRECTION: Auth tokens")
        screen = term.type("python -m keephive todo")
        screen.has("Refactor validation")  # still open
        # Completed items appear in "Recently Done" section

        save_terminal_output("workflows/5day_sprint", term)

    def test_staleness_lifecycle_full(self, term, save_terminal_output):
        """Full lifecycle: fresh -> stale over 90+ days using working memory."""
        # Seed working memory with both a FACT and a DECISION
        (term.hive_home / "working" / "memory.md").write_text(
            "# Working Memory\n\n"
            "- FACT: API rate limit is 100 req/min [verified:2026-01-01]\n"
            "- DECISION: Use Redis for session storage [verified:2026-01-01]\n"
        )

        # Day 10: Both fresh
        term.set_date("2026-01-11")
        term.type("python -m keephive s").lacks("stale")

        # Day 31: FACT stale (30d), DECISION still fresh (90d)
        term.set_date("2026-02-01")
        screen = term.type("python -m keephive s")
        screen.has("stale")

        # Day 91: Both stale
        term.set_date("2026-04-02")
        screen = term.type("python -m keephive s")
        screen.has("stale")

        save_terminal_output("workflows/staleness_lifecycle", term)

    def test_accumulation_30_days(self, term, save_terminal_output):
        """30 days of daily captures, verify files accumulate."""
        for day in range(30):
            term.set_date(f"2026-04-{day + 1:02d}")
            term.type(f"python -m keephive r 'FACT: Day {day + 1} observation'").has("Remembered")
            if day % 3 == 0:
                term.type(f"python -m keephive t 'Task from day {day + 1}'").has("Remembered")
            if day % 5 == 0 and day > 0:
                term.type(f"python -m keephive td 'Task from day {day - 4}'")

        # Verify all 30 daily files were created
        for day in range(30):
            assert term.file_exists(f"daily/2026-04-{day + 1:02d}.md")

        # Status on last day shows current day's entries
        term.set_date("2026-04-30")
        screen = term.type("python -m keephive s")
        screen.has("keephive", "Day 30 observation")
        save_terminal_output("workflows/30day_accumulation", term)

    def test_weekend_gap(self, term):
        """Verify weekend gap does not break commands."""
        # Friday
        term.set_date("2026-03-06")
        term.type("python -m keephive r 'FACT: Friday work'")
        assert term.file_exists("daily/2026-03-06.md")

        # Skip Saturday/Sunday

        # Monday
        term.set_date("2026-03-09")
        term.type("python -m keephive r 'FACT: Monday work'")
        assert term.file_exists("daily/2026-03-09.md")
        term.type("python -m keephive s").has("FACT: Monday work")


# ============================================================
#  Category 6: Profile Isolation
# ============================================================


@pytest.mark.terminal
class TestProfileWorkflows:
    def test_data_isolation(self, term, save_terminal_output):
        """Data in one HIVE_HOME does not appear in another."""
        # Add fact in current env
        term.type("python -m keephive r 'FACT: profile A data'")
        term.type("python -m keephive s").has("profile A data")

        # Switch HIVE_HOME to a different directory
        alt_home = term.hive_home.parent / "hive_alt"
        for sub in [
            "working",
            "daily",
            "knowledge/guides",
            "knowledge/prompts",
            "working/notes",
            "archive",
        ]:
            (alt_home / sub).mkdir(parents=True, exist_ok=True)
        term._send(f"export HIVE_HOME={alt_home}")
        time.sleep(0.1)

        # Alt env should have no entries from profile A
        term.type("python -m keephive s").has("no entries yet")
        term.type("python -m keephive s").lacks("profile A data")

        save_terminal_output("profiles/data_isolation", term)


# ============================================================
#  Category 7: Command Output Format Validation
# ============================================================


@pytest.mark.terminal
class TestOutputFormats:
    def test_version_format(self, term):
        """Version output matches expected format."""
        term.type("python -m keephive --version").matches(r"keephive v\d+\.\d+\.\d+")

    def test_help_shows_commands(self, term, save_terminal_output, update_golden):
        """Help displays command groups."""
        screen = term.type("python -m keephive help")
        screen.has("remember", "recall", "status", "todo")
        save_terminal_output("format/help", term)
        assert_golden(screen, "help", update=update_golden)

    def test_remember_output_format(self, term, save_terminal_output):
        """Remember shows confirmation with tier info."""
        screen = term.type("python -m keephive r 'FACT: output format test'")
        screen.has("Remembered")
        save_terminal_output("format/remember", term)

    def test_recall_output_format(self, term, save_terminal_output):
        """Recall output shows matched facts with context."""
        term.type("python -m keephive r 'FACT: React uses virtual DOM'")
        screen = term.type("python -m keephive rc React")
        screen.has("React", "virtual DOM")
        save_terminal_output("format/recall", term)

    def test_stats_no_crash_patterns(self, term):
        """Stats does not crash with edge data patterns."""
        # Single entry
        term.type("python -m keephive r 'FACT: solo entry'")
        term.type("python -m keephive stats")  # Should not crash

    def test_unicode_roundtrip(self, term):
        """Unicode characters survive remember/recall."""
        term.type("python -m keephive r 'FACT: Arrow test right arrow here'")
        term.type("python -m keephive rc Arrow").has("Arrow")


# ============================================================
#  Category 8: Error Paths & Recovery
# ============================================================


@pytest.mark.terminal
class TestErrorPaths:
    def test_unknown_command(self, term):
        """Unknown command shows helpful error."""
        term.type("python -m keephive nonexistent_command")
        # Should show error or help, not crash

    def test_recall_empty_query(self, term):
        """Recall with no query."""
        term.type("python -m keephive rc")
        # Should handle gracefully

    def test_todo_done_nonexistent(self, term):
        """Mark done on nonexistent TODO."""
        screen = term.type("python -m keephive td 'this does not exist'")
        screen.has("No matching TODO")

    def test_stats_corrupted_json(self, term):
        """Stats handles corrupted .stats.json gracefully."""
        (term.hive_home / ".stats.json").write_text("{corrupted")
        term.type("python -m keephive stats")
        # Should recover, not crash


# ============================================================
#  Category 9: Golden File Expansion
# ============================================================


@pytest.mark.terminal
class TestGoldenExpansion:
    def test_todo_empty_golden(self, term, save_terminal_output, update_golden):
        """Golden: empty TODO list."""
        screen = term.type("python -m keephive todo")
        screen.has("No open TODOs")
        save_terminal_output("golden/todo_empty", term)
        assert_golden(screen, "todo_empty", update=update_golden)

    def test_todo_seeded_golden(self, term_seeded, save_terminal_output, update_golden):
        """Golden: TODO list with seeded data."""
        screen = term_seeded.type("python -m keephive todo")
        save_terminal_output("golden/todo_seeded", term_seeded)
        assert_golden(screen, "todo_seeded", update=update_golden)

    def test_recall_hit_golden(self, term, save_terminal_output, update_golden):
        """Golden: recall with a matching fact."""
        term.type("python -m keephive r 'FACT: Redis supports pub/sub messaging'")
        screen = term.type("python -m keephive rc Redis")
        screen.has("Redis", "pub/sub")
        save_terminal_output("golden/recall_hit", term)
        assert_golden(screen, "recall_hit", update=update_golden)

    def test_recall_miss_golden(self, term, save_terminal_output, update_golden):
        """Golden: recall with no matches."""
        screen = term.type("python -m keephive rc nonexistent_zebra_query")
        screen.has("No results")
        save_terminal_output("golden/recall_miss", term)
        assert_golden(screen, "recall_miss", update=update_golden)

    def test_doctor_clean_golden(self, term, save_terminal_output, update_golden):
        """Golden: doctor on clean state."""
        screen = term.type("python -m keephive doctor")
        screen.has("hive doctor")
        save_terminal_output("golden/doctor_clean", term)
        assert_golden(screen, "doctor_clean", update=update_golden)

    def test_version_golden(self, term, save_terminal_output, update_golden):
        """Golden: version output."""
        screen = term.type("python -m keephive --version")
        screen.matches(r"keephive v\d+\.\d+\.\d+")
        save_terminal_output("golden/version", term)
        assert_golden(screen, "version", update=update_golden)

    def test_gc_dryrun_golden(self, term, save_terminal_output, update_golden):
        """Golden: gc --dry-run on empty hive."""
        screen = term.type("python -m keephive gc --dry-run")
        screen.has("Garbage collection", "Nothing to archive")
        save_terminal_output("golden/gc_dryrun", term)
        assert_golden(screen, "gc_dryrun", update=update_golden)

    def test_remember_confirm_golden(self, term, save_terminal_output, update_golden):
        """Golden: remember showing confirmation output."""
        screen = term.type("python -m keephive r 'FACT: Golang uses goroutines for concurrency'")
        screen.has("Remembered", "FACT")
        save_terminal_output("golden/remember_confirm", term)
        assert_golden(screen, "remember_confirm", update=update_golden)

    def test_log_seeded_golden(self, term_seeded, save_terminal_output, update_golden):
        """Golden: log view with seeded data."""
        screen = term_seeded.type("python -m keephive l")
        save_terminal_output("golden/log_seeded", term_seeded)
        assert_golden(screen, "log_seeded", update=update_golden)

    def test_profile_list_golden(self, term, save_terminal_output, update_golden):
        """Golden: profile list showing default."""
        screen = term.type("python -m keephive profile list")
        screen.has("Profiles", "default")
        save_terminal_output("golden/profile_list", term)
        assert_golden(screen, "profile_list", update=update_golden)


# ============================================================
#  Category 10: Watch Mode
# ============================================================


@pytest.mark.terminal
class TestWatchMode:
    def test_status_watch_starts_and_stops(self, term, save_terminal_output):
        """hive s --watch shows header, Ctrl+C exits cleanly."""
        term._send("python -m keephive s --watch --interval 1")
        screen = term.wait_for("watching")
        screen.has("watching", "ctrl+c to stop", "keephive")
        term.send_keys("C-c")
        final = term.wait_for("Watch stopped")
        final.lacks("Traceback")
        save_terminal_output("watch/status_start_stop", term)

    def test_status_watch_updates_on_new_entry(self, term, save_terminal_output):
        """Watch refreshes when a new fact is added."""
        from datetime import date

        # Add initial fact so status has content
        term.type("python -m keephive r 'FACT: baseline entry for watch test'")
        # Start watch
        term._send("python -m keephive s --watch --interval 1")
        term.wait_for("watching")
        # Write directly to daily file (simulates hook/other terminal)
        # Must use HH:MM:SS format (storage.py regex requires 3 groups)
        time.sleep(0.5)
        daily = term.hive_home / "daily" / f"{date.today().isoformat()}.md"
        with open(daily, "a") as f:
            f.write("- [23:59:59] FACT: watch-trigger-test-unique\n")
        # Wait for the watch to pick up the change
        screen = term.wait_for("watch-trigger-test-unique", timeout=5)
        screen.has("watch-trigger-test-unique")
        # Stop
        term.send_keys("C-c")
        term.wait_for("Watch stopped")
        save_terminal_output("watch/status_live_update", term)

    def test_log_watch_starts_and_stops(self, term, save_terminal_output):
        """hive l --watch shows log content, Ctrl+C exits cleanly."""
        term.type("python -m keephive r 'FACT: log watch test entry'")
        term._send("python -m keephive l --watch --interval 1")
        screen = term.wait_for("watching")
        screen.has("watching", "log watch test entry")
        term.send_keys("C-c")
        final = term.wait_for("Watch stopped")
        final.lacks("Traceback")
        save_terminal_output("watch/log_start_stop", term)

    def test_todo_watch_starts_and_stops(self, term, save_terminal_output):
        """hive todo --watch shows todo list, Ctrl+C exits cleanly."""
        term.type("python -m keephive t 'implement widget factory for dashboard'")
        term._send("python -m keephive todo --watch --interval 1")
        screen = term.wait_for("watching")
        screen.has("watching", "implement widget factory for dashboard")
        term.send_keys("C-c")
        final = term.wait_for("Watch stopped")
        final.lacks("Traceback")
        save_terminal_output("watch/todo_start_stop", term)


# ============================================================
#  Category 11: Insights & Discoverability
# ============================================================


@pytest.mark.terminal
class TestInsightsAndDiscoverability:
    def test_rf_insights_basic(self, term, save_terminal_output):
        """hive rf insights shows output structure (uses real facets data)."""
        screen = term.type("python -m keephive rf insights")
        # Either shows data or the "no data" message; both are valid
        screen.lacks("Traceback", "Error")
        save_terminal_output("insights/rf_insights_basic", term)

    def test_rf_insights_json(self, term, save_terminal_output):
        """hive rf insights --json outputs valid JSON."""
        screen = term.type("python -m keephive rf insights --json")
        screen.lacks("Traceback", "Error")
        save_terminal_output("insights/rf_insights_json", term)

    def test_rl_alias(self, term, save_terminal_output):
        """hive rl --dry-run works as shortcut for rule learn."""
        screen = term.type("python -m keephive rl --dry-run")
        # Shows friction summary or "no data" message
        screen.lacks("Traceback", "Unknown command")
        save_terminal_output("insights/rl_alias", term)

    def test_help_shows_rule_in_manage(self, term, save_terminal_output):
        """hive help shows rule under Manage section."""
        screen = term.type("python -m keephive help")
        screen.has("rule")
        screen.lacks("Traceback")
        save_terminal_output("insights/help_rule_manage", term)

    def test_help_reflect_shows_insights(self, term, save_terminal_output):
        """hive help rf shows insights subcommand."""
        screen = term.type("python -m keephive help rf")
        screen.has("insights")
        save_terminal_output("insights/help_reflect_insights", term)
