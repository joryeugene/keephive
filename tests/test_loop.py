"""Tests for hive run (commands/loop.py) — loop mechanics, state machine, helpers."""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Helper ────────────────────────────────────────────────────────────────────


def make_loop_file(hive_env: Path, loop_id: str, session_id: str | None = None, **kwargs) -> Path:
    """Write a .loop-{loop_id}.json file in hive_env and return its path."""
    data = {
        "loop_id": loop_id,
        "task": "test task",
        "max_iter": 10,
        "iter": 0,
        "mode": "in-session",
        "session_id": session_id,
        "cwd": "/tmp/test",
        "created_at": "2026-02-24T14:00:00",
    }
    data.update(kwargs)
    path = hive_env / f".loop-{loop_id}.json"
    path.write_text(json.dumps(data))
    return path


# ── _sanitize_loop_id ─────────────────────────────────────────────────────────


class TestSanitizeLoopId:
    def test_extracts_meaningful_word(self, hive_env, monkeypatch):
        """Picks the first non-stopword word of 4+ chars."""
        from keephive.commands.loop import _sanitize_loop_id

        result = _sanitize_loop_id("refactor auth module")
        assert result.startswith("refactor-"), f"Expected 'refactor-...', got {result!r}"

    def test_skips_short_words(self, hive_env, monkeypatch):
        """Words under 4 chars are skipped."""
        from keephive.commands.loop import _sanitize_loop_id

        result = _sanitize_loop_id("fix the auth system")
        # "fix" = 3 chars (skip), "the" = stopword, "auth" = 4 chars
        assert result.startswith("auth-"), f"Expected 'auth-...', got {result!r}"

    def test_includes_timestamp_suffix(self, hive_env, monkeypatch):
        """ID always ends with YYYYMMDD-HHMMSS."""
        import re

        from keephive.commands.loop import _sanitize_loop_id

        result = _sanitize_loop_id("security audit codebase")
        assert re.search(r"\d{8}-\d{6}$", result), f"No timestamp suffix in {result!r}"

    def test_fallback_on_empty_task(self, hive_env, monkeypatch):
        """Gracefully handles a task with no alpha chars."""
        from keephive.commands.loop import _sanitize_loop_id

        result = _sanitize_loop_id("123 456")
        # Falls back to "loop" prefix
        assert result.startswith("loop-"), f"Expected 'loop-...', got {result!r}"


# ── _parse_run_flags ──────────────────────────────────────────────────────────


class TestParseRunFlags:
    def test_defaults(self, hive_env):
        """Empty args returns sensible defaults."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags([])
        assert opts["max_iter"] == 10
        assert opts["background"] is False
        assert opts["at"] is None
        assert opts["tonight"] is False

    def test_max_iter_flag(self, hive_env):
        """--max N sets max_iter correctly."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags(["--max", "5"])
        assert opts["max_iter"] == 5

    def test_max_iter_non_integer_ignored(self, hive_env):
        """--max with non-integer leaves default."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags(["--max", "banana"])
        assert opts["max_iter"] == 10

    def test_background_flag(self, hive_env):
        """--background sets background=True."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags(["--background"])
        assert opts["background"] is True

    def test_at_flag(self, hive_env):
        """--at HH:MM sets at correctly."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags(["--at", "22:30"])
        assert opts["at"] == "22:30"

    def test_tonight_flag(self, hive_env):
        """--tonight sets tonight=True."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags(["--tonight"])
        assert opts["tonight"] is True

    def test_combined_flags(self, hive_env):
        """Multiple flags all parsed correctly."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags(["--max", "3", "--background"])
        assert opts["max_iter"] == 3
        assert opts["background"] is True
        # --safe and opts["safe"] assertions removed


# ── TestSafeFlagRemoved ───────────────────────────────────────────────────────


class TestSafeFlagRemoved:
    def test_help_does_not_mention_safe(self, hive_env, capsys):
        """--safe must not appear in help (it has zero implementation)."""
        from keephive.commands.loop import _print_run_help

        _print_run_help()
        captured = capsys.readouterr()
        assert "--safe" not in captured.out

    def test_parse_flags_no_safe_key(self, hive_env):
        """opts dict must not contain a 'safe' key after --safe flag removal."""
        from keephive.commands.loop import _parse_run_flags

        opts = _parse_run_flags([])
        assert "safe" not in opts


# ── TestLoopExtractIncludesTodos ──────────────────────────────────────────────


class TestLoopExtractIncludesTodos:
    def test_todos_included_in_pending_facts(self, hive_env, monkeypatch):
        """result.todos must be appended to pending facts, not silently dropped."""
        from keephive.commands.loop import _do_loop_extract
        from keephive.models import LoopExtractionResponse
        from keephive.storage import daily_file, hive_dir
        from keephive.clock import get_today

        loop_id = "test-todos-bug"
        today = get_today()
        log_path = daily_file(today.isoformat())
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"- [10:00:00] [Loop {loop_id} start: test task (max 3 iter)]\n"
        )

        # LLM response with a todo — this is what gets silently dropped currently
        response = LoopExtractionResponse(
            facts=["Redis cache reduces read latency"],
            decisions=["Use write-through caching for user sessions"],
            todos=["Follow up on cache invalidation strategy"],
        )
        monkeypatch.setattr(
            "keephive.claude.run_claude_pipe",
            lambda *_a, **_kw: response,
        )

        _do_loop_extract(loop_id)

        pending = (hive_dir() / ".pending-facts.md").read_text()
        # All three types should appear; todos were the missing ones
        assert "Redis cache" in pending
        assert "write-through" in pending
        assert "cache invalidation strategy" in pending  # This line fails before fix


# ── TestStopHookPromptFileCleanup ─────────────────────────────────────────────


class TestStopHookPromptFileCleanup:
    def test_prompt_file_deleted_on_loop_completion(self, hive_env, monkeypatch):
        """stop hook must delete .loop-prompt-{id}.txt when loop completes at max_iter."""
        import io
        import json
        from unittest.mock import MagicMock

        import pytest

        from keephive.hooks.stop import hook_stop
        from keephive.storage import hive_dir

        loop_id = "prm-cleanup-99"
        sess = "sess-prm-test"

        loop_file = hive_dir() / f".loop-{loop_id}.json"
        prompt_file = hive_dir() / f".loop-prompt-{loop_id}.txt"

        # iter=9, max_iter=10 → next iter=10 = done
        loop_file.write_text(json.dumps({
            "loop_id": loop_id, "task": "prompt cleanup test",
            "max_iter": 10, "iter": 9,
            "mode": "background", "session_id": sess,
            "cwd": str(hive_dir()), "created_at": "2026-02-24T14:00:00",
        }))
        prompt_file.write_text("fake tmux prompt content")

        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)  # Ensure in-session mode path
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": sess, "cwd": "/tmp"})))
        monkeypatch.setattr("sys.stdout", io.StringIO())
        monkeypatch.setattr("subprocess.Popen", MagicMock())  # Don't spawn loop-extract

        with pytest.raises(SystemExit) as exc:
            hook_stop([])

        assert exc.value.code == 0
        assert not prompt_file.exists(), ".loop-prompt-*.txt must be deleted on loop completion"


# ── _build_first_iter_output ──────────────────────────────────────────────────


class TestBuildFirstIterOutput:
    def test_contains_task(self, hive_env):
        """Output includes the task text."""
        from keephive.commands.loop import _build_first_iter_output

        out = _build_first_iter_output("audit-20260224-143052", "security audit", 5, [])
        assert "security audit" in out

    def test_contains_done_path(self, hive_env):
        """Output contains the absolute done-signal path."""
        from keephive.commands.loop import _build_first_iter_output
        from keephive.storage import hive_dir

        loop_id = "refactor-20260224-143052"
        out = _build_first_iter_output(loop_id, "refactor auth", 10, [])
        expected_path = str(hive_dir() / f".loop-done-{loop_id}")
        assert expected_path in out, f"Done path {expected_path!r} not found in output"

    def test_includes_seed_lines(self, hive_env):
        """CONTEXT block included when seed_lines is non-empty."""
        from keephive.commands.loop import _build_first_iter_output

        out = _build_first_iter_output(
            "loop-id", "task", 10, ["- JWT tokens: RS256", "- Redis: sorted set"]
        )
        assert "CONTEXT:" in out
        assert "JWT tokens" in out
        assert "Redis" in out

    def test_no_context_when_empty_seed(self, hive_env):
        """CONTEXT block absent when seed_lines is empty."""
        from keephive.commands.loop import _build_first_iter_output

        out = _build_first_iter_output("loop-id", "task", 10, [])
        assert "CONTEXT:" not in out

    def test_shows_iteration_header(self, hive_env):
        """Includes iteration header showing max_iter."""
        from keephive.commands.loop import _build_first_iter_output

        out = _build_first_iter_output("loop-id", "task", 7, [])
        assert "1/7" in out


# ── _loop_done_path ───────────────────────────────────────────────────────────


class TestLoopDonePath:
    def test_returns_absolute_path(self, hive_env):
        """Returns an absolute path inside hive_dir."""
        from keephive.commands.loop import _loop_done_path
        from keephive.storage import hive_dir

        p = _loop_done_path("audit-20260224-143052")
        assert p.is_absolute()
        assert str(p).startswith(str(hive_dir()))

    def test_includes_loop_id(self, hive_env):
        """Path contains the loop_id."""
        from keephive.commands.loop import _loop_done_path

        p = _loop_done_path("refactor-20260224-143052")
        assert "refactor-20260224-143052" in p.name


# ── _find_loop_for_session ────────────────────────────────────────────────────


class TestFindLoopForSession:
    """The dual-mode loop lookup — most critical path in the system."""

    def test_returns_none_when_no_loop_files(self, hive_env, monkeypatch):
        """No .loop-*.json files → (None, None)."""
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        from keephive.commands.loop import _find_loop_for_session

        req, path = _find_loop_for_session("sess-abc")
        assert req is None
        assert path is None

    def test_in_session_mode_finds_matching_session_id(self, hive_env, monkeypatch):
        """Scans .loop-*.json and returns the one with matching session_id."""
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        make_loop_file(hive_env, "audit-001", session_id="sess-target")
        make_loop_file(hive_env, "other-001", session_id="sess-other")

        from keephive.commands.loop import _find_loop_for_session

        req, path = _find_loop_for_session("sess-target")
        assert req is not None
        assert req["loop_id"] == "audit-001"
        assert path.name == ".loop-audit-001.json"

    def test_in_session_mode_no_match_returns_none(self, hive_env, monkeypatch):
        """Non-matching session_id → (None, None)."""
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        make_loop_file(hive_env, "audit-001", session_id="sess-other")

        from keephive.commands.loop import _find_loop_for_session

        req, path = _find_loop_for_session("sess-mine")
        assert req is None
        assert path is None

    def test_background_mode_direct_lookup(self, hive_env, monkeypatch):
        """HIVE_LOOP_ID set → looks up .loop-{id}.json directly (O(1) not scan)."""
        loop_id = "audit-20260224-143052"
        make_loop_file(hive_env, loop_id, session_id="sess-bg")
        monkeypatch.setenv("HIVE_LOOP_ID", loop_id)

        from keephive.commands.loop import _find_loop_for_session

        req, path = _find_loop_for_session("sess-bg")
        assert req is not None
        assert req["loop_id"] == loop_id

    def test_background_mode_claims_null_session(self, hive_env, monkeypatch):
        """HIVE_LOOP_ID set + session_id=None → atomically claims the session_id."""
        loop_id = "audit-20260224-143052"
        loop_file = make_loop_file(hive_env, loop_id, session_id=None)
        monkeypatch.setenv("HIVE_LOOP_ID", loop_id)

        from keephive.commands.loop import _find_loop_for_session

        req, path = _find_loop_for_session("sess-new")
        # Claim written back to file
        assert req["session_id"] == "sess-new"
        written = json.loads(loop_file.read_text())
        assert written["session_id"] == "sess-new"

    def test_background_mode_missing_file_returns_none(self, hive_env, monkeypatch):
        """HIVE_LOOP_ID points to non-existent file → (None, None)."""
        monkeypatch.setenv("HIVE_LOOP_ID", "ghost-loop-id")

        from keephive.commands.loop import _find_loop_for_session

        req, path = _find_loop_for_session("sess-any")
        assert req is None
        assert path is None

    def test_corrupt_loop_file_skipped(self, hive_env, monkeypatch):
        """Corrupt JSON in a loop file is skipped, not raised."""
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        (hive_env / ".loop-corrupt-001.json").write_text("{{{not valid json")
        make_loop_file(hive_env, "good-001", session_id="sess-x")

        from keephive.commands.loop import _find_loop_for_session

        # Should not raise; finds the good file
        req, path = _find_loop_for_session("sess-x")
        assert req is not None
        assert req["loop_id"] == "good-001"


# ── _write_iter_log ───────────────────────────────────────────────────────────


class TestWriteIterLog:
    def test_writes_to_daily_log(self, hive_env):
        """Writes a [Loop ...] entry to today's daily log."""
        from keephive.clock import get_today
        from keephive.commands.loop import _write_iter_log
        from keephive.storage import daily_file

        _write_iter_log("audit-20260224-143052", 3, "3/10")

        today = get_today().isoformat()
        content = daily_file(today).read_text()
        assert "Loop audit-20260224-143052" in content
        assert "3/10" in content

    def test_complete_status_logged(self, hive_env):
        """'complete' status is written correctly."""
        from keephive.clock import get_today
        from keephive.commands.loop import _write_iter_log
        from keephive.storage import daily_file

        _write_iter_log("loop-x", 10, "complete")

        today = get_today().isoformat()
        content = daily_file(today).read_text()
        assert "complete" in content


# ── cmd_loop dispatcher ───────────────────────────────────────────────────────


class TestCmdLoopDispatcher:
    def test_help_flag_prints_help(self, hive_env, capsys):
        """--help shows usage text."""
        from keephive.commands.loop import cmd_loop

        cmd_loop(["--help"])
        out = capsys.readouterr().out
        assert "Usage: hive run" in out

    def test_no_args_shows_help_when_no_active_loops(self, hive_env, capsys):
        """No args + no active loops → help text."""
        from keephive.commands.loop import cmd_loop

        cmd_loop([])
        out = capsys.readouterr().out
        assert "Usage: hive run" in out

    def test_no_args_shows_status_when_loops_exist(self, hive_env, monkeypatch, capsys):
        """No args + active loop → status output."""
        make_loop_file(hive_env, "audit-20260224-143052", session_id="sess-active")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop([])
        out = capsys.readouterr().out
        # Status shows the loop ID
        assert "audit-20260224-143052" in out

    def test_status_subcommand(self, hive_env, monkeypatch, capsys):
        """'status' subcommand works."""
        make_loop_file(hive_env, "refactor-20260224", session_id="sess-s")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["status"])
        out = capsys.readouterr().out
        assert "refactor-20260224" in out

    def test_status_shows_no_active_loops_message(self, hive_env, capsys):
        """Status with no loops → 'No active loops.'"""
        from keephive.commands.loop import cmd_loop

        cmd_loop(["status"])
        out = capsys.readouterr().out
        assert "No active loops" in out


# ── _cmd_run_cancel ───────────────────────────────────────────────────────────


class TestCmdRunCancel:
    def test_cancel_single_loop(self, hive_env, monkeypatch, capsys):
        """Single active loop cancelled without needing --all."""
        loop_id = "audit-20260224-143052"
        loop_file = make_loop_file(hive_env, loop_id, session_id="sess-cancel")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["cancel"])
        out = capsys.readouterr().out

        assert "Cancelled" in out
        assert not loop_file.exists(), "Loop file should be deleted"

    def test_cancel_by_id(self, hive_env, monkeypatch, capsys):
        """cancel {id} removes only the specified loop."""
        id1 = "audit-20260224-143052"
        id2 = "refactor-20260224-143100"
        file1 = make_loop_file(hive_env, id1, session_id="sess-1")
        file2 = make_loop_file(hive_env, id2, session_id="sess-2")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["cancel", id1])
        capsys.readouterr()

        assert not file1.exists(), "Target loop file should be deleted"
        assert file2.exists(), "Non-target loop file should remain"

    def test_cancel_all_removes_everything(self, hive_env, monkeypatch, capsys):
        """cancel --all removes all loop files."""
        file1 = make_loop_file(hive_env, "loop-001", session_id="sess-1")
        file2 = make_loop_file(hive_env, "loop-002", session_id="sess-2")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["cancel", "--all"])
        capsys.readouterr()

        assert not file1.exists()
        assert not file2.exists()

    def test_cancel_multiple_without_target_shows_list(self, hive_env, monkeypatch, capsys):
        """Two loops + no target → shows list, asks user to specify."""
        make_loop_file(hive_env, "loop-001", session_id="sess-1")
        make_loop_file(hive_env, "loop-002", session_id="sess-2")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["cancel"])
        out = capsys.readouterr().out

        assert "cancel --all" in out or "Cancel all" in out

    def test_cancel_no_loops(self, hive_env, capsys):
        """cancel with no loops shows 'No active loops'."""
        from keephive.commands.loop import cmd_loop

        cmd_loop(["cancel"])
        out = capsys.readouterr().out
        assert "No active loops" in out

    def test_cancel_removes_done_signal_file(self, hive_env, monkeypatch, capsys):
        """Cancellation also removes .loop-done-{id} file if present."""
        loop_id = "audit-20260224-143052"
        make_loop_file(hive_env, loop_id, session_id="sess-cancel")
        done_file = hive_env / f".loop-done-{loop_id}"
        done_file.write_text("done")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["cancel"])
        capsys.readouterr()

        assert not done_file.exists(), ".loop-done-* should be removed on cancel"


# ── _cmd_run_task (in-session mode) ──────────────────────────────────────────


class TestCmdRunTaskInSession:
    def test_creates_loop_file(self, hive_env, monkeypatch, capsys):
        """In-session mode creates a .loop-*.json file."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["refactor auth module"])
        capsys.readouterr()

        loop_files = list(hive_env.glob(".loop-*.json"))
        assert len(loop_files) == 1, f"Expected 1 loop file, got {len(loop_files)}"

    def test_loop_file_has_correct_task(self, hive_env, monkeypatch, capsys):
        """Loop file stores the task string correctly."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["security audit codebase"])
        capsys.readouterr()

        loop_files = list(hive_env.glob(".loop-*.json"))
        data = json.loads(loop_files[0].read_text())
        assert data["task"] == "security audit codebase"
        assert data["mode"] == "in-session"
        assert data["max_iter"] == 10

    def test_loop_file_respects_max_flag(self, hive_env, monkeypatch, capsys):
        """--max N written to loop file."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["task description", "--max", "3"])
        capsys.readouterr()

        loop_files = list(hive_env.glob(".loop-*.json"))
        data = json.loads(loop_files[0].read_text())
        assert data["max_iter"] == 3

    def test_first_iter_output_printed_to_stdout(self, hive_env, monkeypatch, capsys):
        """In-session mode prints first iteration prompt to stdout (Claude reads it)."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["refactor auth module"])
        out = capsys.readouterr().out

        assert "TASK: refactor auth module" in out
        assert "Signal done:" in out  # ASCII banner uses "Signal done:" (was "Signal completion")

    def test_guard_against_second_loop_in_same_session(self, hive_env, monkeypatch, capsys):
        """Second in-session loop for same session_id is blocked (H6)."""
        session_id = "sess-in-session"
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_SESSION_ID", session_id)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        # Pre-existing loop for this session
        make_loop_file(hive_env, "existing-loop", session_id=session_id, mode="in-session")

        from keephive.commands.loop import cmd_loop

        cmd_loop(["another task"])
        out = capsys.readouterr().out

        assert "Loop already active" in out or "already active" in out.lower()
        # Only the original loop file should exist
        assert len(list(hive_env.glob(".loop-*.json"))) == 1


# ── Storage integration (pending-facts roundtrip) ─────────────────────────────


class TestPendingFactsRoundtrip:
    def test_append_and_read_roundtrip(self, hive_env):
        """append_pending_facts + read_pending_facts roundtrip."""
        from keephive.storage import append_pending_facts, read_pending_facts

        facts = ["JWT uses RS256", "Redis TTL matches token expiry"]
        append_pending_facts(facts, "audit-20260224-143052")

        items = read_pending_facts()
        assert len(items) == 2
        assert items[0]["fact"] == "JWT uses RS256"
        assert items[0]["loop_id"] == "audit-20260224-143052"
        assert items[1]["fact"] == "Redis TTL matches token expiry"

    def test_append_multiple_loops(self, hive_env):
        """Facts from different loops are all preserved."""
        from keephive.storage import append_pending_facts, read_pending_facts

        append_pending_facts(["fact from loop 1"], "loop-001")
        append_pending_facts(["fact from loop 2"], "loop-002")

        items = read_pending_facts()
        loop_ids = {item["loop_id"] for item in items}
        assert "loop-001" in loop_ids
        assert "loop-002" in loop_ids

    def test_clear_reviewed_facts_removes_by_index(self, hive_env):
        """clear_reviewed_facts removes facts at specified indices."""
        from keephive.storage import append_pending_facts, clear_reviewed_facts, read_pending_facts

        append_pending_facts(["fact A", "fact B", "fact C"], "loop-001")

        clear_reviewed_facts([0, 2])

        remaining = read_pending_facts()
        facts_text = [item["fact"] for item in remaining]
        assert "fact A" not in facts_text, "Index 0 should be removed"
        assert "fact C" not in facts_text, "Index 2 should be removed"
        assert "fact B" in facts_text, "Index 1 should remain"

    def test_empty_facts_writes_nothing(self, hive_env):
        """append_pending_facts with empty list doesn't create a file."""
        from keephive.storage import append_pending_facts, pending_facts_file

        append_pending_facts([], "loop-001")

        # File either doesn't exist or is empty
        pf = pending_facts_file()
        if pf.exists():
            assert pf.read_text().strip() == ""


# ── Stop hook integration: loop intercept ────────────────────────────────────


class TestStopHookLoopIntercept:
    """Stop hook correctly intercepts loop state and drives the state machine."""

    def run_stop_hook(self, session_id: str, monkeypatch, cwd: str = "") -> tuple[str, int]:
        """Run hook_stop, return (stdout, exit_code)."""
        import io
        import sys

        payload = json.dumps({"session_id": session_id, "cwd": cwd})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        monkeypatch.setenv("HIVE_STOP_NUDGE_INTERVAL", "999")

        captured_stdout = []
        exit_code_holder = [0]

        original_write = sys.stdout.write

        def capturing_write(s):
            captured_stdout.append(s)
            return original_write(s)

        with patch("sys.stdout.write", side_effect=capturing_write):
            try:
                from keephive.hooks import stop  # Force reimport for isolation

                import importlib

                importlib.reload(stop)
                stop.hook_stop([])
            except SystemExit as e:
                exit_code_holder[0] = int(e.code) if e.code is not None else 0

        return "".join(captured_stdout), exit_code_holder[0]

    def test_loop_continues_emits_exit_2(self, hive_env, monkeypatch, capsys):
        """With active loop not at max_iter, stop hook emits exit code 2."""
        session_id = "sess-loop-continue"
        make_loop_file(hive_env, "test-loop-001", session_id=session_id, iter=0, max_iter=5)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        monkeypatch.setenv("HIVE_STOP_NUDGE_INTERVAL", "999")

        payload = json.dumps({"session_id": session_id, "cwd": ""})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        import pytest

        from keephive.hooks.stop import hook_stop

        with pytest.raises(SystemExit) as exc:
            hook_stop([])

        assert exc.value.code == 2, f"Expected exit(2) for loop continuation, got {exc.value.code}"

    def test_loop_continues_increments_iter(self, hive_env, monkeypatch, capsys):
        """Continuation increments iter in the loop file."""
        session_id = "sess-loop-iter"
        loop_file = make_loop_file(
            hive_env, "iter-loop-001", session_id=session_id, iter=2, max_iter=5
        )
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        monkeypatch.setenv("HIVE_STOP_NUDGE_INTERVAL", "999")

        payload = json.dumps({"session_id": session_id, "cwd": ""})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        import pytest

        from keephive.hooks.stop import hook_stop

        with pytest.raises(SystemExit):
            hook_stop([])

        # iter bumped from 2 → 3
        data = json.loads(loop_file.read_text())
        assert data["iter"] == 3

    def test_loop_completes_at_max_iter(self, hive_env, monkeypatch, capsys):
        """At max_iter, stop hook exits 0 and removes loop file."""
        session_id = "sess-loop-done"
        loop_file = make_loop_file(
            hive_env, "done-loop-001", session_id=session_id, iter=4, max_iter=5
        )
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        monkeypatch.setenv("HIVE_STOP_NUDGE_INTERVAL", "999")

        payload = json.dumps({"session_id": session_id, "cwd": ""})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        import pytest

        from keephive.hooks.stop import hook_stop

        with patch("subprocess.Popen"):  # suppress loop-extract spawn
            with pytest.raises(SystemExit) as exc:
                hook_stop([])

        assert exc.value.code == 0
        assert not loop_file.exists(), "Loop file should be deleted at completion"

    def test_loop_completes_on_done_signal_file(self, hive_env, monkeypatch, capsys):
        """Done signal file triggers loop completion even before max_iter."""
        session_id = "sess-loop-signal"
        loop_id = "signal-loop-001"
        loop_file = make_loop_file(
            hive_env, loop_id, session_id=session_id, iter=1, max_iter=10
        )
        # Write the done signal
        (hive_env / f".loop-done-{loop_id}").write_text("done")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        monkeypatch.setenv("HIVE_STOP_NUDGE_INTERVAL", "999")

        payload = json.dumps({"session_id": session_id, "cwd": ""})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        import pytest

        from keephive.hooks.stop import hook_stop

        with patch("subprocess.Popen"):
            with pytest.raises(SystemExit) as exc:
                hook_stop([])

        assert exc.value.code == 0
        assert not loop_file.exists()

    def test_no_loop_falls_through_to_nudge(self, hive_env, monkeypatch, capsys):
        """With no active loop, stop hook runs normal nudge logic."""
        session_id = "sess-no-loop"
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        monkeypatch.setenv("HIVE_STOP_NUDGE_INTERVAL", "999")

        payload = json.dumps({"session_id": session_id, "cwd": ""})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        from keephive.hooks.stop import hook_stop

        # Should NOT raise SystemExit (no loop → normal exit path)
        hook_stop([])  # no exception = pass


# ── SessionEnd loop cleanup ───────────────────────────────────────────────────


class TestSessionEndLoopCleanup:
    def test_cleanup_removes_owned_loop_file(self, hive_env, monkeypatch):
        """SessionEnd removes .loop-*.json files owned by the ending session."""
        session_id = "sess-ending"
        loop_file = make_loop_file(hive_env, "cleanup-loop", session_id=session_id)

        payload = json.dumps({"session_id": session_id})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        with patch("subprocess.Popen"):  # suppress soul-update/self-improve
            from keephive.hooks.sessionend import hook_sessionend

            hook_sessionend([])

        assert not loop_file.exists(), "Loop file should be cleaned up on SessionEnd"

    def test_cleanup_leaves_other_sessions_loops(self, hive_env, monkeypatch):
        """SessionEnd only removes loops for the ending session, not others."""
        my_session = "sess-mine"
        other_session = "sess-other"
        my_loop = make_loop_file(hive_env, "my-loop", session_id=my_session)
        other_loop = make_loop_file(hive_env, "other-loop", session_id=other_session)

        payload = json.dumps({"session_id": my_session})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        with patch("subprocess.Popen"):
            from keephive.hooks.sessionend import hook_sessionend

            hook_sessionend([])

        assert not my_loop.exists()
        assert other_loop.exists(), "Another session's loop should not be touched"

    def test_cleanup_removes_done_signal_file(self, hive_env, monkeypatch):
        """SessionEnd also removes .loop-done-{id} for cleaned-up loops."""
        session_id = "sess-done-cleanup"
        loop_id = "cleanup-loop-001"
        make_loop_file(hive_env, loop_id, session_id=session_id)
        done_file = hive_env / f".loop-done-{loop_id}"
        done_file.write_text("done")

        payload = json.dumps({"session_id": session_id})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        with patch("subprocess.Popen"):
            from keephive.hooks.sessionend import hook_sessionend

            hook_sessionend([])

        assert not done_file.exists(), ".loop-done-* should be cleaned up on SessionEnd"

    def test_cleanup_produces_no_stdout(self, hive_env, monkeypatch, capsys):
        """SessionEnd hook must produce zero stdout (protocol invariant)."""
        session_id = "sess-silent"
        make_loop_file(hive_env, "silent-loop", session_id=session_id)

        payload = json.dumps({"session_id": session_id})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        with patch("subprocess.Popen"):
            from keephive.hooks.sessionend import hook_sessionend

            hook_sessionend([])

        out = capsys.readouterr().out
        assert out == "", f"SessionEnd must produce no stdout. Got: {out!r}"


# ── Phase 3: Background tmux mode ────────────────────────────────────────────


class TestBackgroundMode:
    """_launch_background() creates correct loop file + launches tmux window."""

    def test_background_mode_creates_loop_file_with_null_session(
        self, hive_env, monkeypatch, capsys
    ):
        """Background mode loop file has session_id=None (claimed by Stop hook later)."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            from keephive.commands.loop import cmd_loop

            cmd_loop(["security audit", "--background"])
        capsys.readouterr()

        loop_files = list(hive_env.glob(".loop-*.json"))
        assert len(loop_files) == 1

        data = json.loads(loop_files[0].read_text())
        assert data["mode"] == "background"
        assert data["session_id"] is None, "Background loops start unclaimed"
        assert data["tmux_window"].startswith("hive-loop-")

    def test_background_mode_sets_hive_loop_id_in_tmux_command(
        self, hive_env, monkeypatch, capsys
    ):
        """The tmux command string includes HIVE_LOOP_ID=<id>."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        captured_commands: list = []

        def mock_run(cmd, **kwargs):
            captured_commands.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            from keephive.commands.loop import cmd_loop

            cmd_loop(["refactor auth", "--background"])
        capsys.readouterr()

        # Find the tmux new-window call
        tmux_calls = [c for c in captured_commands if "tmux" in str(c)]
        assert tmux_calls, "Expected at least one tmux call"
        tmux_cmd_str = " ".join(str(x) for x in tmux_calls[-1])
        assert "HIVE_LOOP_ID=" in tmux_cmd_str, f"HIVE_LOOP_ID not in tmux command: {tmux_cmd_str}"

    def test_background_mode_fails_gracefully_when_tmux_unavailable(
        self, hive_env, monkeypatch, capsys
    ):
        """No tmux → prints error, doesn't create loop file."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        # tmux info returns non-zero (not available)
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            from keephive.commands.loop import cmd_loop

            cmd_loop(["task description", "--background"])

        out = capsys.readouterr().out
        assert "tmux" in out.lower() or "Background mode" in out

    def test_outside_session_auto_routes_to_background(self, hive_env, monkeypatch, capsys):
        """Without CLAUDECODE and with tmux available, auto-routes to background."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)
        monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            from keephive.commands.loop import cmd_loop

            cmd_loop(["test task"])  # No --background flag
        capsys.readouterr()

        loop_files = list(hive_env.glob(".loop-*.json"))
        assert len(loop_files) == 1
        data = json.loads(loop_files[0].read_text())
        assert data["mode"] == "background"

    def test_cancel_background_loop_kills_tmux_window(self, hive_env, monkeypatch, capsys):
        """Cancelling a background loop runs tmux kill-window."""
        loop_id = "audit-20260224-143052"
        make_loop_file(
            hive_env,
            loop_id,
            session_id=None,
            mode="background",
            tmux_window=f"hive-loop-{loop_id[:20]}",
        )
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        kill_calls: list = []

        def mock_run(cmd, **kwargs):
            kill_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = f"hive-loop-{loop_id[:20]}"  # window exists
            return result

        with patch("subprocess.run", side_effect=mock_run):
            from keephive.commands.loop import cmd_loop

            cmd_loop(["cancel"])
        capsys.readouterr()

        kill_cmds = [c for c in kill_calls if "kill-window" in str(c)]
        assert kill_cmds, "Expected tmux kill-window to be called for background loop"

    def test_background_mode_no_batch_flag(self, hive_env, monkeypatch, capsys):
        """The claude command must NOT include -p (batch mode kills Stop hook)."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        captured = []

        def mock_run(cmd, **kw):
            captured.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("subprocess.Popen", MagicMock())

        from keephive.commands.loop import cmd_loop

        cmd_loop(["refactor auth", "--background"])
        capsys.readouterr()

        tmux = next(c for c in captured if c[0] == "tmux" and "new-window" in c)
        cmd_str = tmux[-1]  # last arg to tmux new-window is the shell command
        assert " -p " not in cmd_str and not cmd_str.endswith(" -p"), (
            f"claude must not use -p batch mode: {cmd_str}"
        )
        assert "claude --dangerously-skip-permissions" in cmd_str

    def test_proceed_trigger_polls_for_prompt_not_fixed_sleep(self, hive_env, monkeypatch, capsys):
        """Trigger command must poll for Claude's '>' prompt, not use a fixed sleep.

        Fixed sleep races against SessionStart hook + model init: Enter arrives
        before Claude's input handler is ready, gets swallowed, 'proceed' appears
        in the input box unsubmitted.
        """
        monkeypatch.delenv("CLAUDECODE", raising=False)
        popen_calls = []

        def mock_popen(cmd, **kw):
            popen_calls.append(cmd)
            return MagicMock()

        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: MagicMock(returncode=0, stderr=""))
        monkeypatch.setattr("subprocess.Popen", mock_popen)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["refactor auth", "--background"])
        capsys.readouterr()

        assert popen_calls, "Expected Popen to be called for proceed trigger"
        trigger = popen_calls[0]
        assert "sleep 5" not in trigger, "Must not use fixed sleep — races against init"
        assert "timeout" in trigger, "Must have a timeout guard for the poll loop"
        assert "grep -q" in trigger and "^>" in trigger, (
            "Must poll for Claude's '>' prompt before sending keys"
        )

    def test_window_name_uses_tail_for_uniqueness(self):
        """Window name must use last chars of loop_id (unique timestamp) not first (stable prefix)."""
        # Two IDs that collide under [:20] but not under [-10:]
        id1 = "implement-20260224-224510"  # "implement-20260224-2" under [:20]
        id2 = "implement-20260224-224531"  # "implement-20260224-2" under [:20]
        assert id1[:20] == id2[:20], "precondition: old formula collides"
        wn1 = f"hive-loop-{id1[-10:]}"
        wn2 = f"hive-loop-{id2[-10:]}"
        assert wn1 != wn2, f"window names must differ: {wn1}"


# ── Phase 4: Scheduled daemon mode ───────────────────────────────────────────


class TestScheduledMode:
    """hive run --at / --tonight queues tasks for daemon execution."""

    def test_schedule_at_creates_queue_entry(self, hive_env, monkeypatch, capsys):
        """--at HH:MM writes a queued entry to .custom-tasks.json."""
        from keephive.storage import read_custom_task_queue

        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["write tests for auth", "--at", "22:00"])
        capsys.readouterr()

        tasks = read_custom_task_queue()
        assert len(tasks) == 1
        task = tasks[0]
        assert task["task"] == "write tests for auth"
        assert task["due"] == "22:00"
        assert task["status"] == "queued"

    def test_tonight_flag_sets_22_00(self, hive_env, monkeypatch, capsys):
        """--tonight sets due=22:00."""
        from keephive.storage import read_custom_task_queue

        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["nightly standup", "--tonight"])
        capsys.readouterr()

        tasks = read_custom_task_queue()
        assert tasks[0]["due"] == "22:00"

    def test_multiple_tasks_accumulate(self, hive_env, monkeypatch, capsys):
        """Multiple --at calls accumulate in the queue."""
        from keephive.storage import read_custom_task_queue

        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("HIVE_LOOP_ID", raising=False)

        from keephive.commands.loop import cmd_loop

        cmd_loop(["task one", "--at", "20:00"])
        cmd_loop(["task two", "--at", "21:00"])
        capsys.readouterr()

        tasks = read_custom_task_queue()
        assert len(tasks) == 2

    def test_update_custom_task_status(self, hive_env):
        """update_custom_task_status changes status without corrupting other tasks."""
        from keephive.storage import append_custom_task, read_custom_task_queue, update_custom_task_status

        append_custom_task(
            {"task_id": "t1", "task": "task one", "status": "queued", "due": "22:00", "due_date": "2026-02-24"}
        )
        append_custom_task(
            {"task_id": "t2", "task": "task two", "status": "queued", "due": "22:30", "due_date": "2026-02-24"}
        )

        update_custom_task_status("t1", "running")

        tasks = read_custom_task_queue()
        by_id = {t["task_id"]: t for t in tasks}
        assert by_id["t1"]["status"] == "running"
        assert by_id["t2"]["status"] == "queued", "Other task status should be unchanged"


class TestIsCustomTaskDue:
    """_is_custom_task_due handles date + time logic correctly."""

    def test_task_due_now(self, hive_env, monkeypatch):
        """Returns True when current time >= due time today."""
        from keephive.commands.daemon import _is_custom_task_due

        now = datetime(2026, 2, 24, 22, 5)
        with patch("keephive.clock.get_now", return_value=now):
            task = {"due": "22:00", "due_date": "2026-02-24", "status": "queued"}
            assert _is_custom_task_due(task) is True

    def test_task_not_yet_due(self, hive_env, monkeypatch):
        """Returns False when current time < due time."""
        from keephive.commands.daemon import _is_custom_task_due

        now = datetime(2026, 2, 24, 21, 55)
        with patch("keephive.clock.get_now", return_value=now):
            task = {"due": "22:00", "due_date": "2026-02-24", "status": "queued"}
            assert _is_custom_task_due(task) is False

    def test_task_due_date_mismatch(self, hive_env, monkeypatch):
        """Returns False when due_date is not today (tasks don't carry over)."""
        from keephive.commands.daemon import _is_custom_task_due

        now = datetime(2026, 2, 25, 22, 5)  # Next day
        with patch("keephive.clock.get_now", return_value=now):
            task = {"due": "22:00", "due_date": "2026-02-24", "status": "queued"}
            assert _is_custom_task_due(task) is False

    def test_missing_due_returns_false(self, hive_env):
        """Returns False when due key is missing."""
        from keephive.commands.daemon import _is_custom_task_due

        assert _is_custom_task_due({"status": "queued"}) is False

    def test_malformed_due_returns_false(self, hive_env):
        """Returns False on malformed due time (not HH:MM)."""
        from keephive.commands.daemon import _is_custom_task_due

        assert _is_custom_task_due({"due": "tonight", "due_date": "2026-02-24"}) is False


class TestTickCustomTaskIntegration:
    """_tick() picks up and runs queued custom tasks when due."""

    def test_tick_runs_due_custom_task(self, hive_env, monkeypatch):
        """_tick() calls _execute_custom_task for queued tasks that are due."""
        from keephive.storage import append_custom_task, read_custom_task_queue

        now = datetime(2026, 2, 24, 22, 5)
        append_custom_task(
            {
                "task_id": "tick-test-001",
                "task": "run integration tests",
                "status": "queued",
                "due": "22:00",
                "due_date": "2026-02-24",
                "cwd": str(hive_env),
                "max_iter": 2,
                "created_at": "2026-02-24T14:00:00",
            }
        )

        executed: list = []

        def fake_execute(task):
            executed.append(task["task_id"])
            return True

        with (
            patch("keephive.clock.get_now", return_value=now),
            patch("keephive.commands.daemon._execute_custom_task", side_effect=fake_execute),
            patch("keephive.commands.daemon.read_daemon_config", return_value={"tasks": {}}),
            patch("keephive.commands.daemon.read_daemon_state", return_value={}),
        ):
            from keephive.commands.daemon import _tick

            _tick()

        assert "tick-test-001" in executed, "Tick should have executed the due custom task"

    def test_tick_skips_non_queued_tasks(self, hive_env, monkeypatch):
        """_tick() skips custom tasks that are already running/done/failed."""
        from keephive.storage import append_custom_task

        now = datetime(2026, 2, 24, 22, 5)

        for status in ("running", "done", "failed"):
            append_custom_task(
                {
                    "task_id": f"skip-{status}",
                    "task": f"task with {status} status",
                    "status": status,
                    "due": "22:00",
                    "due_date": "2026-02-24",
                    "cwd": str(hive_env),
                    "max_iter": 2,
                    "created_at": "2026-02-24T14:00:00",
                }
            )

        executed: list = []

        def fake_execute(task):
            executed.append(task["task_id"])
            return True

        with (
            patch("keephive.clock.get_now", return_value=now),
            patch("keephive.commands.daemon._execute_custom_task", side_effect=fake_execute),
            patch("keephive.commands.daemon.read_daemon_config", return_value={"tasks": {}}),
            patch("keephive.commands.daemon.read_daemon_state", return_value={}),
        ):
            from keephive.commands.daemon import _tick

            _tick()

        assert len(executed) == 0, f"No non-queued tasks should run, but ran: {executed}"
