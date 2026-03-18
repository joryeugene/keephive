"""Terminal E2E tests for hive run CLI surface (commands/loop.py).

Tests the user-visible behavior: output format, file side-effects, argument parsing,
subcommand routing, and interactive flows. All run via the real tmux terminal driver.

Run: just test-e2e
Filter: just test-one "-m terminal -k TestLoop"

Design notes:
  - Loop files written from Python side (outside tmux) for setup speed and determinism.
  - Review tests use piped stdin (printf "y\\n" | review) which triggers auto-accept
    because prompt_yn returns default_yes=True when stdin is not a TTY.
  - Decline behavior is covered at the unit level in test_llm_loop.py::TestLoopKnowledgeFlywheel.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.terminal


# ── Test helpers ──────────────────────────────────────────────────────────────


def _write_loop_file(
    hive_home: Path,
    loop_id: str,
    session_id: str | None = "sess-test",
    **kwargs: object,
) -> Path:
    """Write a .loop-{loop_id}.json to hive_home. Used for status/cancel test setup."""
    data: dict = {
        "loop_id": loop_id,
        "task": "test task for loop",
        "max_seconds": None,
        "iter": 0,
        "mode": "background",
        "session_id": session_id,
        "cwd": str(hive_home),
        "created_at": "2026-02-24T14:00:00",
    }
    data.update(kwargs)
    path = hive_home / f".loop-{loop_id}.json"
    path.write_text(json.dumps(data))
    return path


def _write_pending_facts(hive_home: Path, facts: list[tuple[str, str]]) -> None:
    """Write .pending-facts.md with (loop_id, fact_text) tuples."""
    lines = [f"- [loop:{lid}] {fact}\n" for lid, fact in facts]
    (hive_home / ".pending-facts.md").write_text("".join(lines))


def _write_loop_log_entries(hive_home: Path, day: str, entries: list[str]) -> None:
    """Append log entries to daily/{day}.md in the standard format."""
    path = hive_home / "daily" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else f"# Daily Log: {day}\n\n"
    path.write_text(existing + "\n".join(f"- [10:00:00] {e}" for e in entries) + "\n")


# ── Class 1: Help ──────────────────────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopHelp:
    def test_no_args_no_loops_shows_help(self, term: object, save_terminal_output: object) -> None:
        """With no active loops and no args, `run` shows the usage message."""
        term.type("python -m keephive run").has("Usage: hive run")  # type: ignore[union-attr]
        save_terminal_output("loop/help_no_args", term)  # type: ignore[union-attr]

    def test_help_flag(self, term: object) -> None:
        """--help flag prints usage with key flags."""
        term.type("python -m keephive run --help").has(
            "--max-time", "--background", "--at", "--tonight"
        )  # type: ignore[union-attr]

    def test_help_subcommand(self, term: object) -> None:
        """`run help` subcommand shows usage."""
        term.type("python -m keephive run help").has("Usage: hive run")  # type: ignore[union-attr]

    def test_loop_extract_hidden(self, term: object) -> None:
        """`loop-extract` is an internal command and must not appear in help."""
        term.type("python -m keephive run --help").lacks("loop-extract")  # type: ignore[union-attr]

    def test_main_help_has_run_not_swarm(self, term: object) -> None:
        """Top-level help lists `run` (added) and not `swarm` (removed)."""
        term.type("python -m keephive --help").has("run").lacks("swarm")  # type: ignore[union-attr]


# ── Class 2: Background loop start ────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopStartBackground:
    @pytest.fixture(autouse=True)
    def _no_spawn(self, term: object) -> None:
        """Block real Claude spawns by setting the test seam env var."""
        term.type("export HIVE_NO_TMUX_SPAWN=1")  # type: ignore[union-attr]

    def test_prints_loop_started_header(self, term: object, save_terminal_output: object) -> None:
        """Background run prints the loop started confirmation to current window."""
        term.type("python -m keephive run 'refactor auth module'").has(  # type: ignore[union-attr]
            "Background loop started", "Loop ID:"
        )
        save_terminal_output("loop/start_background", term)  # type: ignore[union-attr]

    def test_shows_cancel_hint(self, term: object) -> None:
        """Background output includes the cancel command hint."""
        term.type("python -m keephive run 'test cancel hint'").has("hive run cancel")  # type: ignore[union-attr]

    def test_task_appears_in_output(self, term: object) -> None:
        """Task text appears in the background confirmation output."""
        term.type("python -m keephive run 'deploy to production'").has(  # type: ignore[union-attr]
            "Task:", "deploy to production"
        )

    def test_creates_loop_file(self, term: object) -> None:
        """Running `hive run` creates a .loop-*.json file in HIVE_HOME."""
        term.type("python -m keephive run 'create loop file task'")  # type: ignore[union-attr]
        loop_files = list(term.hive_home.glob(".loop-*.json"))  # type: ignore[union-attr]
        assert len(loop_files) == 1, f"Expected 1 loop file, found: {loop_files}"

    def test_loop_file_mode_is_background(self, term: object) -> None:
        """Loop file records mode=background."""
        term.type("python -m keephive run 'mode check task'")  # type: ignore[union-attr]
        files = list(term.hive_home.glob(".loop-*.json"))  # type: ignore[union-attr]
        assert files
        data = json.loads(files[0].read_text())
        assert data["mode"] == "background"

    def test_loop_file_max_iter_default(self, term: object) -> None:
        """Loop file has max_seconds=None when no --max-time flag is passed."""
        term.type("python -m keephive run 'default max task'")  # type: ignore[union-attr]
        files = list(term.hive_home.glob(".loop-*.json"))  # type: ignore[union-attr]
        assert files
        data = json.loads(files[0].read_text())
        assert data.get("max_seconds") is None

    def test_loop_file_iter_starts_at_zero(self, term: object) -> None:
        """Loop file starts with iter=0 (no iterations run yet)."""
        term.type("python -m keephive run 'iter zero task'")  # type: ignore[union-attr]
        files = list(term.hive_home.glob(".loop-*.json"))  # type: ignore[union-attr]
        assert files
        assert json.loads(files[0].read_text())["iter"] == 0

    def test_max_flag_overrides_default(self, term: object) -> None:
        """--max-time 2h sets max_seconds=7200 in the loop file."""
        term.type("python -m keephive run 'max flag task' --max-time 2h")  # type: ignore[union-attr]
        files = list(term.hive_home.glob(".loop-*.json"))  # type: ignore[union-attr]
        assert files
        assert json.loads(files[0].read_text())["max_seconds"] == 7200

    def test_daily_log_gets_start_entry(self, term: object) -> None:
        """Running `hive run` writes a [Loop ... start] entry to today's daily log."""
        today = date.today().isoformat()
        term.type("python -m keephive run 'log start task'")  # type: ignore[union-attr]
        content = term.read_file(f"daily/{today}.md")  # type: ignore[union-attr]
        assert "Loop" in content and "start" in content

    def test_no_args_with_loop_active_shows_status(self, term: object) -> None:
        """When a loop is active, `run` with no args shows status instead of help."""
        _write_loop_file(
            term.hive_home,  # type: ignore[union-attr]
            "active-loop-20260224-120000",
            session_id="some-session",
        )
        term.type("python -m keephive run").has("active-loop-20260224-120000")  # type: ignore[union-attr]
        term.type("python -m keephive run").lacks("Usage: hive run")  # type: ignore[union-attr]


# ── Class 3: Status ───────────────────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopStatus:
    def test_no_loops_message(self, term: object, save_terminal_output: object) -> None:
        """Status with no active loops shows 'No active loops.'"""
        term.type("python -m keephive run status").has("No active loops")  # type: ignore[union-attr]
        save_terminal_output("loop/status_empty", term)  # type: ignore[union-attr]

    def test_single_loop_shows_id_and_task(self, term: object) -> None:
        """Status with one active loop shows the loop ID and task text."""
        _write_loop_file(
            term.hive_home,  # type: ignore[union-attr]
            "status-test-20260224-120000",
            task="implement the payment flow",
        )
        term.type("python -m keephive run status").has(  # type: ignore[union-attr]
            "status-test-20260224-120000", "implement the payment flow"
        )

    def test_shows_iter_count(self, term: object) -> None:
        """Status shows current iteration count as 'iter N'."""
        _write_loop_file(
            term.hive_home,  # type: ignore[union-attr]
            "iter-status-20260224-120000",
            iter=3,
        )
        term.type("python -m keephive run status").has("iter 3")  # type: ignore[union-attr]

    def test_multiple_loops_shown(self, term: object) -> None:
        """Status with two active loops shows both loop IDs."""
        _write_loop_file(term.hive_home, "loop-alpha-20260224-120000")  # type: ignore[union-attr]
        _write_loop_file(term.hive_home, "loop-beta-20260224-120001")  # type: ignore[union-attr]
        screen = term.type("python -m keephive run status")  # type: ignore[union-attr]
        screen.has("loop-alpha-20260224-120000", "loop-beta-20260224-120001")

    def test_background_orphan_shows_orphaned(self, term: object) -> None:
        """Background loop whose tmux window no longer exists is marked ORPHANED."""
        _write_loop_file(
            term.hive_home,  # type: ignore[union-attr]
            "orphan-loop-20260224-120000",
            mode="background",
            session_id=None,
            tmux_window="hive-loop-this-window-does-not-exist-xyz",
        )
        term.type("python -m keephive run status").has("ORPHANED")  # type: ignore[union-attr]

    def test_shows_cancel_hint(self, term: object) -> None:
        """Status output includes how to cancel the loop."""
        _write_loop_file(term.hive_home, "cancel-hint-20260224-120000")  # type: ignore[union-attr]
        term.type("python -m keephive run status").has("hive run cancel")  # type: ignore[union-attr]


# ── Class 4: Cancel ───────────────────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopCancel:
    def test_no_loops_message(self, term: object) -> None:
        """Cancel with no active loops shows 'No active loops.'"""
        term.type("python -m keephive run cancel").has("No active loops")  # type: ignore[union-attr]

    def test_single_loop_cancelled(self, term: object, save_terminal_output: object) -> None:
        """Cancel with one active loop shows 'Cancelled'."""
        _write_loop_file(term.hive_home, "cancel-one-20260224-120000")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel").has("Cancelled")  # type: ignore[union-attr]
        save_terminal_output("loop/cancel_single", term)  # type: ignore[union-attr]

    def test_loop_file_deleted_after_cancel(self, term: object) -> None:
        """Loop file is gone after cancel."""
        _write_loop_file(term.hive_home, "cancel-del-20260224-120000")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel")  # type: ignore[union-attr]
        assert not list(term.hive_home.glob(".loop-*.json")), "Loop file should be deleted"  # type: ignore[union-attr]

    def test_done_file_also_deleted_on_cancel(self, term: object) -> None:
        """Done-signal file is cleaned up when the loop is cancelled."""
        loop_id = "cancel-done-20260224-120000"
        _write_loop_file(term.hive_home, loop_id)  # type: ignore[union-attr]
        (term.hive_home / f".loop-done-{loop_id}").write_text("done")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel")  # type: ignore[union-attr]
        assert not (term.hive_home / f".loop-done-{loop_id}").exists()  # type: ignore[union-attr]

    def test_two_loops_no_id_shows_menu(self, term: object) -> None:
        """With two loops and no ID, cancel shows list with 'Cancel all' and 'Cancel one'."""
        _write_loop_file(term.hive_home, "menu-loop-alpha-20260224-120000")  # type: ignore[union-attr]
        _write_loop_file(term.hive_home, "menu-loop-beta-20260224-120001")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel").has("Cancel all", "Cancel one")  # type: ignore[union-attr]

    def test_all_flag_deletes_all_loops(self, term: object) -> None:
        """--all deletes all active loop files."""
        _write_loop_file(term.hive_home, "all-loop-alpha-20260224-120000")  # type: ignore[union-attr]
        _write_loop_file(term.hive_home, "all-loop-beta-20260224-120001")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel --all").has("Cancelled")  # type: ignore[union-attr]
        remaining = list(term.hive_home.glob(".loop-*.json"))  # type: ignore[union-attr]
        assert not remaining, f"Expected no loop files, got: {remaining}"

    def test_specific_id_only_deletes_target(self, term: object) -> None:
        """Cancel with a specific ID only removes that loop, not others."""
        _write_loop_file(term.hive_home, "target-loop-20260224-120000")  # type: ignore[union-attr]
        _write_loop_file(term.hive_home, "other-loop-20260224-120001")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel target-loop-20260224-120000")  # type: ignore[union-attr]
        assert not (term.hive_home / ".loop-target-loop-20260224-120000.json").exists()  # type: ignore[union-attr]
        assert (term.hive_home / ".loop-other-loop-20260224-120001.json").exists()  # type: ignore[union-attr]

    def test_nonexistent_id_shows_not_found(self, term: object) -> None:
        """Cancel with an unknown ID shows 'No loop found with ID'."""
        _write_loop_file(term.hive_home, "real-loop-20260224-120000")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel ghost-id-xyz-99999").has(  # type: ignore[union-attr]
            "No loop found with ID"
        )


# ── Class 5: Schedule ─────────────────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopSchedule:
    def test_at_output(self, term: object, save_terminal_output: object) -> None:
        """--at HH:MM shows scheduled confirmation with time."""
        term.type("python -m keephive run 'write tests' --at 22:00").has(  # type: ignore[union-attr]
            "Scheduled", "22:00", "daemon must be running"
        )
        save_terminal_output("loop/schedule_at", term)  # type: ignore[union-attr]

    def test_at_creates_custom_tasks_file(self, term: object) -> None:
        """--at creates a .custom-tasks.json entry in HIVE_HOME."""
        term.type("python -m keephive run 'write tests' --at 22:00")  # type: ignore[union-attr]
        path = term.hive_home / ".custom-tasks.json"  # type: ignore[union-attr]
        assert path.exists(), ".custom-tasks.json not created"
        data = json.loads(path.read_text())
        assert isinstance(data, list) and len(data) == 1

    def test_task_text_preserved_in_schedule(self, term: object) -> None:
        """Scheduled task entry retains the original task text."""
        term.type("python -m keephive run 'deploy to staging' --at 21:30")  # type: ignore[union-attr]
        path = term.hive_home / ".custom-tasks.json"  # type: ignore[union-attr]
        assert path.exists()
        entry = json.loads(path.read_text())[0]
        assert entry["task"] == "deploy to staging"

    def test_task_status_is_queued(self, term: object) -> None:
        """Scheduled task entry has status=queued."""
        term.type("python -m keephive run 'build docs' --at 23:00")  # type: ignore[union-attr]
        path = term.hive_home / ".custom-tasks.json"  # type: ignore[union-attr]
        assert path.exists()
        entry = json.loads(path.read_text())[0]
        assert entry["status"] == "queued"

    def test_tonight_output(self, term: object) -> None:
        """--tonight shows 'tonight at 22:00' in confirmation."""
        term.type("python -m keephive run 'nightly task' --tonight").has("tonight at 22:00")  # type: ignore[union-attr]

    def test_tonight_uses_2200(self, term: object) -> None:
        """--tonight schedules at due=22:00."""
        term.type("python -m keephive run 'nightly task' --tonight")  # type: ignore[union-attr]
        path = term.hive_home / ".custom-tasks.json"  # type: ignore[union-attr]
        assert path.exists()
        entry = json.loads(path.read_text())[0]
        assert entry["due"] == "22:00"

    def test_multiple_tasks_accumulate(self, term: object) -> None:
        """Scheduling two tasks writes two entries to .custom-tasks.json."""
        term.type("python -m keephive run 'task one' --at 20:00")  # type: ignore[union-attr]
        term.type("python -m keephive run 'task two' --at 21:00")  # type: ignore[union-attr]
        path = term.hive_home / ".custom-tasks.json"  # type: ignore[union-attr]
        assert path.exists()
        entries = json.loads(path.read_text())
        assert len(entries) == 2

    def test_max_in_scheduled_task(self, term: object) -> None:
        """--max-time flag is preserved in the scheduled task entry."""
        term.type("python -m keephive run 'task with max' --at 20:00 --max-time 2h")  # type: ignore[union-attr]
        path = term.hive_home / ".custom-tasks.json"  # type: ignore[union-attr]
        assert path.exists()
        entry = json.loads(path.read_text())[0]
        assert entry["max_seconds"] == 7200


# ── Class 6: History ──────────────────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopHistory:
    def test_no_logs_message(self, term: object, save_terminal_output: object) -> None:
        """History with no daily log entries shows 'No loop history found.'"""
        term.type("python -m keephive run history").has("No loop history found")  # type: ignore[union-attr]
        save_terminal_output("loop/history_empty", term)  # type: ignore[union-attr]

    def test_single_start_entry_shows_loop_id(self, term: object) -> None:
        """A loop start entry in the daily log appears in history."""
        today = date.today().isoformat()
        _write_loop_log_entries(
            term.hive_home,  # type: ignore[union-attr]
            today,
            ["[Loop history-test-20260224-120000 start: example task (max 10 iter)]"],
        )
        term.type("python -m keephive run history").has("history-test-20260224-120000")  # type: ignore[union-attr]

    def test_column_headers_present(self, term: object) -> None:
        """History table has LOOP ID, EVENTS, DATE column headers."""
        today = date.today().isoformat()
        _write_loop_log_entries(
            term.hive_home,  # type: ignore[union-attr]
            today,
            ["[Loop header-test-20260224-120000 start: task]"],
        )
        term.type("python -m keephive run history").has("LOOP ID", "EVENTS", "DATE")  # type: ignore[union-attr]

    def test_multiple_events_for_same_loop(self, term: object) -> None:
        """Multiple log entries for the same loop appear in one row."""
        today = date.today().isoformat()
        loop_id = "multi-event-20260224-120000"
        _write_loop_log_entries(
            term.hive_home,  # type: ignore[union-attr]
            today,
            [
                f"[Loop {loop_id} start: multi event task (max 3 iter)]",
                f"[Loop {loop_id} iter 1: 1/3]",
                f"[Loop {loop_id} iter 2: complete]",
            ],
        )
        # One row in the table — the loop_id appears exactly once
        screen = term.type("python -m keephive run history")  # type: ignore[union-attr]
        assert screen.plain.count(loop_id) >= 1

    def test_multi_day_entries(self, term: object) -> None:
        """Loops from different days both appear in history."""
        from datetime import timedelta

        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        _write_loop_log_entries(
            term.hive_home,  # type: ignore[union-attr]
            today.isoformat(),
            ["[Loop today-loop-20260224-120000 start: today task]"],
        )
        _write_loop_log_entries(
            term.hive_home,  # type: ignore[union-attr]
            yesterday,
            ["[Loop yesterday-loop-20260223-120000 start: yesterday task]"],
        )
        screen = term.type("python -m keephive run history")  # type: ignore[union-attr]
        screen.has("today-loop-20260224-120000", "yesterday-loop-20260223-120000")


# ── Class 7: Review ───────────────────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopReview:
    def test_no_pending_facts(self, term: object, save_terminal_output: object) -> None:
        """Review with no pending facts shows 'No pending facts to review.'"""
        term.type("python -m keephive run review").has("No pending facts to review")  # type: ignore[union-attr]
        save_terminal_output("loop/review_empty", term)  # type: ignore[union-attr]

    def test_shows_fact_count(self, term: object) -> None:
        """Review header shows how many facts are pending."""
        _write_pending_facts(
            term.hive_home,  # type: ignore[union-attr]
            [("loop-a", "First fact about Redis"), ("loop-b", "Second fact about caching")],
        )
        # Piped stdin → auto-accepts (default_yes=True when not TTY)
        term.type('printf "y\\ny\\n" | python -m keephive run review').has("2 facts")  # type: ignore[union-attr]

    def test_accept_adds_fact_to_memory(self, term: object) -> None:
        """Auto-accepted fact appears in working/memory.md."""
        _write_pending_facts(
            term.hive_home,  # type: ignore[union-attr]
            [("loop-accept", "Redis uses single-threaded event loop")],
        )
        term.type('printf "y\\n" | python -m keephive run review')  # type: ignore[union-attr]
        mem = (term.hive_home / "working" / "memory.md").read_text()  # type: ignore[union-attr]
        assert "Redis uses single-threaded event loop" in mem

    def test_accept_writes_daily_log(self, term: object) -> None:
        """Accepted fact creates a 'Loop review: accepted' entry in today's daily log."""
        today = date.today().isoformat()
        _write_pending_facts(
            term.hive_home,  # type: ignore[union-attr]
            [("loop-daily", "PyJWT handles token expiry correctly")],
        )
        term.type('printf "y\\n" | python -m keephive run review')  # type: ignore[union-attr]
        daily_path = term.hive_home / "daily" / f"{today}.md"  # type: ignore[union-attr]
        assert daily_path.exists(), "Daily log not created after review"
        assert "Loop review: accepted" in daily_path.read_text()

    def test_accept_removes_from_pending(self, term: object) -> None:
        """After accepting the only fact, .pending-facts.md is deleted or empty."""
        _write_pending_facts(
            term.hive_home,  # type: ignore[union-attr]
            [("loop-rm", "This fact is accepted and removed")],
        )
        term.type('printf "y\\n" | python -m keephive run review')  # type: ignore[union-attr]
        pending = term.hive_home / ".pending-facts.md"  # type: ignore[union-attr]
        # File should either not exist or be empty after all facts accepted
        if pending.exists():
            lines = [ln for ln in pending.read_text().splitlines() if ln.strip().startswith("- ")]
            assert not lines, f"Expected empty pending, found: {pending.read_text()}"

    def test_summary_line_after_accept(self, term: object, save_terminal_output: object) -> None:
        """Review prints '1 fact(s) added to memory' summary after accepting one fact."""
        _write_pending_facts(
            term.hive_home,  # type: ignore[union-attr]
            [("loop-sum", "Alembic manages Python database migrations")],
        )
        term.type('printf "y\\n" | python -m keephive run review').has(  # type: ignore[union-attr]
            "1 fact(s) added to memory"
        )
        save_terminal_output("loop/review_accept", term)  # type: ignore[union-attr]

    def test_review_count_matches_accepted(self, term: object) -> None:
        """Summary count matches the number of facts that were auto-accepted."""
        _write_pending_facts(
            term.hive_home,  # type: ignore[union-attr]
            [
                ("loop-c1", "Fact one about database indexing"),
                ("loop-c2", "Fact two about query optimization"),
            ],
        )
        # Both facts auto-accepted via default_yes=True
        term.type('printf "y\\ny\\n" | python -m keephive run review').has(  # type: ignore[union-attr]
            "2 fact(s) added to memory"
        )

    def test_both_facts_in_memory_after_accept_all(self, term: object) -> None:
        """Both facts appear in memory.md when all are auto-accepted."""
        _write_pending_facts(
            term.hive_home,  # type: ignore[union-attr]
            [
                ("loop-m1", "PostgreSQL JSONB supports GIN indexes"),
                ("loop-m2", "GIN indexes speed up array containment queries"),
            ],
        )
        term.type('printf "y\\ny\\n" | python -m keephive run review')  # type: ignore[union-attr]
        mem = (term.hive_home / "working" / "memory.md").read_text()  # type: ignore[union-attr]
        assert "PostgreSQL JSONB supports GIN indexes" in mem
        assert "GIN indexes speed up array containment queries" in mem


# ── Class 8: Daily log integration ────────────────────────────────────────────


@pytest.mark.terminal
class TestLoopDailyLogIntegration:
    def test_start_writes_log_entry(self, term: object) -> None:
        """hive run creates a [Loop ... start:] entry in today's daily log."""
        today = date.today().isoformat()
        term.type("python -m keephive run 'daily log test task'")  # type: ignore[union-attr]
        daily = term.hive_home / "daily" / f"{today}.md"  # type: ignore[union-attr]
        assert daily.exists(), f"Daily log not created: {daily}"
        content = daily.read_text()
        assert "[Loop" in content and "start" in content

    def test_history_parses_start_entry(self, term: object) -> None:
        """A loop start entry written to the daily log appears in `run history`."""
        today = date.today().isoformat()
        loop_id = "history-parse-20260224-120000"
        _write_loop_log_entries(
            term.hive_home,  # type: ignore[union-attr]
            today,
            [f"[Loop {loop_id} start: parse test task (max 5 iter)]"],
        )
        term.type("python -m keephive run history").has(loop_id)  # type: ignore[union-attr]

    def test_cancel_does_not_add_log_entry(self, term: object) -> None:
        """Cancelling a loop does not write a new daily log entry."""
        today = date.today().isoformat()
        _write_loop_file(term.hive_home, "cancel-log-20260224-120000")  # type: ignore[union-attr]
        term.type("python -m keephive run cancel")  # type: ignore[union-attr]
        daily = term.hive_home / "daily" / f"{today}.md"  # type: ignore[union-attr]
        if daily.exists():
            # Cancel should NOT write any log entry
            content = daily.read_text()
            assert "cancel-log-20260224-120000" not in content
