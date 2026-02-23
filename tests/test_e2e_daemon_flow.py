"""Terminal E2E tests for daemon lifecycle, hive flow, soul identity, self-improve, and hook debug log.

Every test runs in a fresh tmux session with isolated HIVE_HOME.
HIVE_SKIP_LLM=1 is set by the Terminal fixture, so daemon tasks that call LLM
return False → throttle/state seeding is used instead of actual LLM execution.

Run: uv run pytest tests/test_e2e_daemon_flow.py -v -o "addopts="
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pytest


# ============================================================
#  Category 1: hive flow — guided maintenance workflow
# ============================================================


@pytest.mark.terminal
class TestHiveFlow:
    def test_flow_all_stages_empty(self, term, save_terminal_output):
        """All queues empty: all stage lines show 'queue empty', flow completes."""
        screen = term.type("python -m keephive flow --skip-verify")
        screen.has("hive flow")
        screen.has("queue empty")
        screen.has("Flow complete")
        save_terminal_output("flow/all_stages_empty", term)

    def test_flow_stage_headers_printed(self, term):
        """Stage 2/5 through 5/5 all appear in output with correct numbering."""
        screen = term.type("python -m keephive flow --skip-verify")
        screen.has("Stage 2/5", "Stage 3/5", "Stage 4/5", "Stage 5/5")

    def test_flow_skip_verify_flag(self, term, save_terminal_output):
        """--skip-verify causes Stage 5 to print 'skipped' instead of running verify."""
        screen = term.type("python -m keephive flow --skip-verify")
        screen.has("skipped (--skip-verify)")
        # Active stage 5 header only appears when verify actually runs
        screen.lacks("Verify Stale Facts")
        save_terminal_output("flow/skip_verify", term)

    def test_flow_complete_message(self, term):
        """Flow always ends with Flow complete message."""
        screen = term.type("python -m keephive flow --skip-verify")
        screen.has("Flow complete")

    def test_flow_triage_shows_counts(self, term):
        """Stage 1 triage shows all three queue labels and 'All queues empty' when clean."""
        screen = term.type("python -m keephive flow --skip-verify")
        screen.has("Pending facts")
        screen.has("Pending rules")
        screen.has("Pending improvements")
        screen.has("All queues empty")

    def test_flow_skips_empty_stages(self, term):
        """Empty stages show 'queue empty' rather than the bold active stage header."""
        screen = term.type("python -m keephive flow --skip-verify")
        # When fact queue is empty, Stage 2 shows dim "queue empty" variant
        screen.has("Stage 2/5: Fact Review")
        screen.has("queue empty")


# ============================================================
#  Category 2: daemon lifecycle
# ============================================================


@pytest.mark.terminal
class TestDaemonLifecycle:
    def test_daemon_status_never_run(self, term, save_terminal_output):
        """Daemon status with no state file shows 'never run' for hook-triggered tasks."""
        screen = term.type("python -m keephive daemon status")
        screen.has("KingBee Daemon")
        screen.has("never run")
        screen.has("soul-update")
        screen.has("self-improve")
        save_terminal_output("daemon/status_never_run", term)

    def test_daemon_status_with_state(self, term):
        """Seeded daemon state shows formatted last-run times in status table."""
        state = {
            "soul-update": {"last_run": "2026-02-22T10:00:00"},
            "self-improve": {"last_run": "2026-02-15T08:30:00"},
        }
        (term.hive_home / ".daemon-state.json").write_text(json.dumps(state))
        term.set_date("2026-02-23")
        screen = term.type("python -m keephive daemon status")
        screen.has("soul-update")
        # _fmt_run returns "Feb 22" for a date that is not today
        screen.has("Feb 22")

    def test_daemon_run_self_improve_throttle_respected(self, term):
        """Self-improve run within 7-day throttle window outputs 'skipped' message and logs it."""
        # Seed state with a last_run from today (0 days since last run < 7 day threshold)
        state = {"self-improve": {"last_run": datetime.now().isoformat()}}
        (term.hive_home / ".daemon-state.json").write_text(json.dumps(state))
        screen = term.type("python -m keephive daemon run self-improve")
        screen.has("skipped")
        # daemon.log must also record the throttle skip with a diagnostic message
        log_text = term.read_file("daemon.log")
        assert "self-improve: throttled" in log_text, (
            f"Expected 'self-improve: throttled' in daemon.log: {log_text}"
        )

    def test_daemon_run_soul_update_throttle_respected(self, term):
        """Soul-update run within 1-hour throttle window outputs 'skipped' message and logs it."""
        # Seed state with last_run just now (within 1 hour throttle)
        state = {"soul-update": {"last_run": datetime.now().isoformat()}}
        (term.hive_home / ".daemon-state.json").write_text(json.dumps(state))
        screen = term.type("python -m keephive daemon run soul-update")
        screen.has("skipped")
        # daemon.log must also record the throttle skip with a diagnostic message
        log_text = term.read_file("daemon.log")
        assert "soul-update: throttled" in log_text, (
            f"Expected 'soul-update: throttled' in daemon.log: {log_text}"
        )

    def test_daemon_run_unknown_task(self, term):
        """Running an unknown task produces a 'skipped' message, no crash or traceback."""
        # Unknown tasks return False from _execute_task → 'skipped' via _run_task
        screen = term.type("python -m keephive daemon run nonexistent-task-xyz")
        screen.has("skipped")
        screen.lacks("Traceback")

    def test_daemon_start_creates_pid(self, term):
        """daemon start spawns the background process and writes .daemon.pid."""
        term.type("python -m keephive daemon start")
        assert term.file_exists(".daemon.pid"), ".daemon.pid should exist after start"
        # Clean up: stop the daemon so it doesn't outlive the test
        term.type("python -m keephive daemon stop")

    def test_daemon_stop_removes_pid(self, term):
        """daemon stop kills the process and removes .daemon.pid."""
        term.type("python -m keephive daemon start")
        assert term.file_exists(".daemon.pid"), ".daemon.pid should exist before stop"
        term.type("python -m keephive daemon stop")
        assert not term.file_exists(".daemon.pid"), ".daemon.pid should be removed after stop"

    def test_daemon_log_shows_entries(self, term):
        """daemon log subcommand shows entries from daemon.log file."""
        log_content = "[2026-02-23T10:00:00] KingBee daemon loop started\n"
        (term.hive_home / "daemon.log").write_text(log_content)
        screen = term.type("python -m keephive daemon log")
        screen.has("KingBee daemon loop started")


# ============================================================
#  Category 3: SOUL.md identity
# ============================================================


@pytest.mark.terminal
class TestSoulIdentity:
    def test_soul_missing_graceful_sessionstart(self, term):
        """SessionStart hook runs without SOUL.md present — no crash, valid JSON output."""
        assert not term.file_exists("SOUL.md"), "SOUL.md should be absent for this test"
        # Redirect to a file: the hook's JSON output is thousands of chars long, which
        # causes tmux's capture-pane to concatenate it with the END marker on the same
        # logical line, breaking type()'s exact-line marker check.
        term.type(
            'echo \'{"source":"test","cwd":"/test"}\''
            ' | python -m keephive hook-sessionstart >"$HIVE_HOME/.hook-test-out.json" 2>&1'
        )
        output = term.read_file(".hook-test-out.json")
        assert "Traceback" not in output
        assert any(k in output for k in ["additionalContext", "hookEventName", "hookSpecificOutput"])

    def test_soul_injected_via_sessionstart(self, term):
        """Pre-written SOUL.md appears verbatim in sessionstart additionalContext."""
        soul_content = "## Summary\nKingBee test identity here."
        (term.hive_home / "SOUL.md").write_text(soul_content)
        # Redirect to a file: same long-output tmux marker issue as test above.
        term.type(
            'echo \'{"source":"test","cwd":"/test"}\''
            ' | python -m keephive hook-sessionstart >"$HIVE_HOME/.hook-test-out.json" 2>&1'
        )
        output = term.read_file(".hook-test-out.json")
        assert "## Summary" in output
        assert "KingBee test identity here" in output

    def test_daemon_soul_update_throttle_skips(self, term):
        """Soul-update run within 1-hour throttle produces 'skipped' in terminal output."""
        state = {"soul-update": {"last_run": datetime.now().isoformat()}}
        (term.hive_home / ".daemon-state.json").write_text(json.dumps(state))
        screen = term.type("python -m keephive daemon run soul-update")
        screen.has("soul-update")
        screen.has("skipped")

    def test_checkup_shows_soul_freshness(self, term, save_terminal_output):
        """hive checkup reports SOUL.md status — present or absent — without crashing."""
        # Write a SOUL.md so checkup has something to report on
        (term.hive_home / "SOUL.md").write_text("## Summary\nTest soul.")
        screen = term.type("python -m keephive checkup")
        screen.has("SOUL")
        screen.lacks("Traceback")
        save_terminal_output("checkup/soul_freshness", term)


# ============================================================
#  Category 4: self-improve proposal loop
# ============================================================


@pytest.mark.terminal
class TestSelfImproveLoop:
    def test_improve_empty_list(self, term, save_terminal_output):
        """No pending improvements shows KingBee status message."""
        screen = term.type("python -m keephive improve list")
        screen.has("KingBee is still learning")
        save_terminal_output("improve/empty_list", term)

    def test_improve_list_with_pending(self, term):
        """Seeded proposals appear in improve list with type and content."""
        proposals = [
            {
                "type": "rule",
                "rationale": "test rationale here",
                "rule": "Always use uv for Python",
                "proposed_at": datetime.now().isoformat(),
            }
        ]
        (term.hive_home / ".pending-improvements.json").write_text(json.dumps(proposals))
        screen = term.type("python -m keephive improve list")
        screen.has("RULE")
        screen.has("Always use uv")

    def test_improve_list_shows_age(self, term):
        """List shows 'today' for freshly created proposals."""
        proposals = [
            {
                "type": "skill",
                "name": "test-skill",
                "rationale": "test rationale",
                "content": "test content",
                "proposed_at": datetime.now().isoformat(),
            }
        ]
        (term.hive_home / ".pending-improvements.json").write_text(json.dumps(proposals))
        screen = term.type("python -m keephive improve list")
        screen.has("today")

    def test_improve_clear_stale(self, term):
        """clear-stale removes proposals older than 30 days and reports count."""
        old_date = (datetime.now() - timedelta(days=35)).isoformat()
        proposals = [
            {
                "type": "rule",
                "rationale": "old proposal to remove",
                "rule": "Old rule nobody reads",
                "proposed_at": old_date,
            }
        ]
        (term.hive_home / ".pending-improvements.json").write_text(json.dumps(proposals))
        screen = term.type("python -m keephive improve clear-stale")
        screen.has("Removed 1 stale")

    def test_improve_throttle_shown_in_terminal(self, term):
        """When self-improve is throttled, terminal output shows 'skipped'.

        Throttle skips are intentionally silent to daemon.log (not a diagnostic
        event). The _run_task caller prints the 'skipped' message to stdout.
        """
        state = {"self-improve": {"last_run": datetime.now().isoformat()}}
        (term.hive_home / ".daemon-state.json").write_text(json.dumps(state))
        screen = term.type("python -m keephive daemon run self-improve")
        screen.has("skipped")
        screen.lacks("Traceback")


# ============================================================
#  Category 5: hook debug log
# ============================================================


@pytest.mark.terminal
class TestHookDebugLog:
    def test_hook_debug_log_written_on_precompact(self, term):
        """PreCompact hook writes to .hook-debug.log on each invocation."""
        term.type("echo '{}' | python -m keephive hook-precompact")
        assert term.file_exists(".hook-debug.log"), ".hook-debug.log should be created"

    def test_hook_debug_log_format(self, term):
        """Hook debug log entries use ISO timestamp format [YYYY-MM-DDTHH:MM:SS]."""
        term.type("echo '{}' | python -m keephive hook-precompact")
        log_content = term.read_file(".hook-debug.log")
        assert re.search(
            r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\]", log_content
        ), f"Expected ISO timestamp in log, got:\n{log_content}"

    def test_hook_debug_log_trigger_captured(self, term):
        """Hook debug log captures the trigger field from hook input."""
        term.type(
            'echo \'{"trigger":"manual"}\' | python -m keephive hook-precompact'
        )
        log_content = term.read_file(".hook-debug.log")
        assert "trigger=manual" in log_content, (
            f"Expected 'trigger=manual' in log, got:\n{log_content}"
        )
