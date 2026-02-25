"""LLM E2E + stop hook integration tests for hive run (commands/loop.py).

Classes 1-2 (stop hook, continuation quality): no real LLM needed — pure I/O and JSON
contract checks. Included here because they test the loop subsystem, not shared logic.

Classes 3-5 (extract quality, flywheel, prompt improvement): some call real LLM (haiku),
others use mocked prompts. All require `just test-llm` to run.

IMPORTANT: Do NOT run from inside a Claude Code session. claude -p conflicts with
the Claude Code socket. Run via: just test-llm (from a regular terminal).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.llm


# ── Helpers ───────────────────────────────────────────────────────────────────


def _skip_if_no_claude() -> None:
    import shutil

    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")


def _make_loop_file(
    hive_dir: Path,
    loop_id: str,
    session_id: str = "sess-test",
    **kwargs,
) -> Path:
    """Write a .loop-{loop_id}.json to hive_dir with sensible defaults."""
    data = {
        "loop_id": loop_id,
        "task": "test task",
        "max_iter": 10,
        "iter": 0,
        "mode": "in-session",
        "session_id": session_id,
        "cwd": str(hive_dir),
        "created_at": "2026-02-24T14:00:00",
    }
    data.update(kwargs)
    path = hive_dir / f".loop-{loop_id}.json"
    path.write_text(json.dumps(data))
    return path


def _run_stop_hook(
    hive_dir: Path,
    session_id: str = "sess-test",
    cwd: str = "/tmp",
) -> tuple[str, int]:
    """Invoke the stop hook via subprocess, return (stdout, exit_code).

    The stop hook reads JSON from stdin (session_id, cwd) and:
    - Exits 2 with JSON continuation when a loop is active for that session.
    - Exits 0 normally when no loop matches.
    """
    import os

    env = dict(os.environ)
    env["HIVE_HOME"] = str(hive_dir)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    result = subprocess.run(
        [sys.executable, "-m", "keephive", "hook-stop"],
        input=json.dumps({"session_id": session_id, "cwd": cwd}),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result.stdout, result.returncode


# ── TestLoopStopHookIntegration ───────────────────────────────────────────────


class TestLoopStopHookIntegration:
    """Stop hook exit-code and JSON contract.

    The stop hook drives iteration via exit codes: 2 = continue, 0 = done.
    These tests verify the state machine directly without any LLM calls.
    """

    def test_exits_2_when_loop_active(self, llm_hive_env: Path) -> None:
        """Stop hook exits 2 when a loop file matches the session."""
        _make_loop_file(llm_hive_env, "active-20260224-120000", session_id="sess-active")
        _, code = _run_stop_hook(llm_hive_env, "sess-active")
        assert code == 2

    def test_advances_iter_on_each_call(self, llm_hive_env: Path) -> None:
        """Each stop hook invocation increments the iter field in the loop file."""
        loop_id = "iter-adv-20260224-120000"
        _make_loop_file(llm_hive_env, loop_id, session_id="sess-adv", iter=0, max_iter=5)

        for expected in range(1, 4):
            _run_stop_hook(llm_hive_env, "sess-adv")
            data = json.loads((llm_hive_env / f".loop-{loop_id}.json").read_text())
            assert data["iter"] == expected, f"Expected iter={expected}, got {data['iter']}"

    def test_exits_0_at_max_iter(self, llm_hive_env: Path) -> None:
        """Stop hook exits 0 (completion) when iter+1 reaches max_iter."""
        _make_loop_file(
            llm_hive_env,
            "max-20260224-120000",
            session_id="sess-max",
            iter=9,
            max_iter=10,
        )
        _, code = _run_stop_hook(llm_hive_env, "sess-max")
        assert code == 0

    def test_loop_file_deleted_at_completion(self, llm_hive_env: Path) -> None:
        """Loop file is removed when iteration count is exhausted."""
        loop_id = "del-20260224-120000"
        _make_loop_file(llm_hive_env, loop_id, session_id="sess-del", iter=9, max_iter=10)
        _run_stop_hook(llm_hive_env, "sess-del")
        assert not (llm_hive_env / f".loop-{loop_id}.json").exists()

    def test_done_signal_exits_0_early(self, llm_hive_env: Path) -> None:
        """Writing the done-signal file causes early completion (exit 0) below max_iter."""
        loop_id = "sig-20260224-120000"
        _make_loop_file(llm_hive_env, loop_id, session_id="sess-sig", iter=2, max_iter=10)
        (llm_hive_env / f".loop-done-{loop_id}").write_text("done")
        _, code = _run_stop_hook(llm_hive_env, "sess-sig")
        assert code == 0

    def test_done_signal_and_loop_files_both_deleted(self, llm_hive_env: Path) -> None:
        """Both loop file and done-signal file are cleaned up on completion."""
        loop_id = "sig2-20260224-120000"
        _make_loop_file(llm_hive_env, loop_id, session_id="sess-sig2", iter=2, max_iter=10)
        (llm_hive_env / f".loop-done-{loop_id}").write_text("done")
        _run_stop_hook(llm_hive_env, "sess-sig2")
        assert not (llm_hive_env / f".loop-{loop_id}.json").exists()
        assert not (llm_hive_env / f".loop-done-{loop_id}").exists()

    def test_stdout_is_valid_json_when_continuing(self, llm_hive_env: Path) -> None:
        """Stop hook stdout is valid JSON when exit code is 2."""
        _make_loop_file(llm_hive_env, "json-20260224-120000", session_id="sess-json")
        stdout, code = _run_stop_hook(llm_hive_env, "sess-json")
        assert code == 2
        parsed = json.loads(stdout)
        assert "hookSpecificOutput" in parsed

    def test_additional_context_contains_task(self, llm_hive_env: Path) -> None:
        """The additionalContext JSON field contains the loop task text."""
        _make_loop_file(
            llm_hive_env,
            "ctx-20260224-120000",
            session_id="sess-ctx",
            task="refactor the payment service",
        )
        stdout, code = _run_stop_hook(llm_hive_env, "sess-ctx")
        assert code == 2
        ctx = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
        assert "refactor the payment service" in ctx

    def test_exits_0_when_no_loop_matches(self, llm_hive_env: Path) -> None:
        """Stop hook exits 0 (normal flow) when no loop file matches the session."""
        _, code = _run_stop_hook(llm_hive_env, "no-such-session-xyz")
        assert code == 0


# ── TestLoopContinuationPromptQuality ─────────────────────────────────────────


class TestLoopContinuationPromptQuality:
    """Quality checks for the continuation prompt the stop hook emits.

    The additionalContext is what the agent reads at the start of every iteration.
    These tests define what "good enough" looks like. If they fail after a change
    to stop.py or loop.py prompts, it means the continuation degraded.
    """

    def _get_context(self, hive_dir: Path, **kwargs: object) -> str:
        loop_id = "qual-20260224-120000"
        session_id = "sess-qual"
        defaults: dict = {"task": "implement OAuth2 authentication", "iter": 0, "max_iter": 5}
        defaults.update(kwargs)
        _make_loop_file(hive_dir, loop_id, session_id=session_id, **defaults)
        stdout, code = _run_stop_hook(hive_dir, session_id)
        assert code == 2, f"Expected exit 2, got {code}. stdout: {stdout!r}"
        return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]

    def test_context_contains_task(self, llm_hive_env: Path) -> None:
        """additionalContext contains the original task string."""
        ctx = self._get_context(llm_hive_env, task="implement OAuth2 authentication")
        assert "implement OAuth2 authentication" in ctx

    def test_context_has_iteration_numbers(self, llm_hive_env: Path) -> None:
        """Current iteration count (N/M) appears in the continuation prompt."""
        # iter=2 means iteration 2 just completed; next prompt shows 3/5
        ctx = self._get_context(llm_hive_env, iter=2, max_iter=5)
        assert "3" in ctx and "5" in ctx

    def test_context_has_done_signal_path(self, llm_hive_env: Path) -> None:
        """Done-signal path (.loop-done-*) is included so the agent can signal completion."""
        ctx = self._get_context(llm_hive_env)
        assert ".loop-done-" in ctx

    def test_context_starts_with_continue(self, llm_hive_env: Path) -> None:
        """Continuation prompt starts with 'Continue:' for clear actionability."""
        ctx = self._get_context(llm_hive_env)
        assert ctx.startswith("Continue:"), f"Expected 'Continue:' prefix, got: {ctx[:60]!r}"

    def test_context_is_substantive(self, llm_hive_env: Path) -> None:
        """Continuation prompt has >10 words — not a one-liner."""
        ctx = self._get_context(llm_hive_env, task="write unit tests for the auth module")
        word_count = len(ctx.split())
        assert word_count > 10, f"Context too terse ({word_count} words): {ctx!r}"

    def test_done_signal_path_is_absolute(self, llm_hive_env: Path) -> None:
        """Done-signal path in the continuation is an absolute filesystem path."""
        import re

        ctx = self._get_context(llm_hive_env)
        paths = re.findall(r"(/[^\s]+\.loop-done-[^\s]+)", ctx)
        assert paths, f"No absolute done-signal path found in: {ctx!r}"

    def test_completion_stdout_is_valid_json(self, llm_hive_env: Path) -> None:
        """Completion (exit 0) stdout is also valid JSON — consistent contract."""
        _make_loop_file(
            llm_hive_env,
            "comp-json-20260224-120000",
            session_id="sess-comp",
            iter=9,
            max_iter=10,
        )
        stdout, code = _run_stop_hook(llm_hive_env, "sess-comp")
        assert code == 0
        json.loads(stdout)  # Must not raise

    def test_completion_context_mentions_review(self, llm_hive_env: Path) -> None:
        """Completion message tells agent what to do next (review extracted facts)."""
        _make_loop_file(
            llm_hive_env,
            "comp-msg-20260224-120000",
            session_id="sess-compmsg",
            iter=9,
            max_iter=10,
        )
        stdout, _ = _run_stop_hook(llm_hive_env, "sess-compmsg")
        ctx = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
        assert "complete" in ctx.lower() or "review" in ctx.lower()


# ── TestLoopExtractQuality ─────────────────────────────────────────────────────


class TestLoopExtractQuality:
    """loop-extract quality: verifies extraction produces real output, not empty/corrupt.

    These tests call real LLM (haiku) via run_claude_pipe().
    """

    def test_no_crash_on_unknown_loop_id(self, llm_hive_env: Path) -> None:
        """loop-extract with unknown loop_id exits cleanly (best-effort contract)."""
        _skip_if_no_claude()
        import os

        env = dict(os.environ)
        env["HIVE_HOME"] = str(llm_hive_env)
        env.pop("HIVE_SKIP_LLM", None)

        result = subprocess.run(
            [sys.executable, "-m", "keephive", "loop-extract", "ghost-loop-xyz"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert "Traceback" not in result.stderr, f"Unexpected crash:\n{result.stderr}"
        # No loop entries in log → no pending facts created
        assert not (llm_hive_env / ".pending-facts.md").exists()

    def test_no_crash_on_empty_daily_log(self, llm_hive_env: Path) -> None:
        """loop-extract with matching loop_id but no matching log lines exits cleanly."""
        _skip_if_no_claude()
        import datetime
        import os

        loop_id = "empty-log-test-20260224"
        today = datetime.date.today().isoformat()
        daily = llm_hive_env / "daily" / f"{today}.md"
        daily.parent.mkdir(parents=True, exist_ok=True)
        # Daily log exists but has no entries for this loop_id
        daily.write_text(f"# Daily Log: {today}\n\n- [10:00:00] unrelated entry\n")

        env = dict(os.environ)
        env["HIVE_HOME"] = str(llm_hive_env)
        env.pop("HIVE_SKIP_LLM", None)

        result = subprocess.run(
            [sys.executable, "-m", "keephive", "loop-extract", loop_id],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert "Traceback" not in result.stderr

    def test_rich_log_produces_pending_facts(self, llm_hive_env: Path) -> None:
        """loop-extract with a detailed session log yields at least 1 pending fact."""
        _skip_if_no_claude()
        import datetime
        import os

        loop_id = "rich-log-extract-20260224"
        today = datetime.date.today().isoformat()
        daily = llm_hive_env / "daily" / f"{today}.md"
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text(
            f"# Daily Log: {today}\n\n"
            f"- [10:00] [Loop {loop_id} start: refactor auth module (max 3 iter)]\n"
            f"- [10:02] Decision: use PyJWT instead of manual JWT implementation\n"
            f"- [10:04] Discovered token expiry must use UTC timestamps not local time\n"
            f"- [10:06] [Loop {loop_id} iter 1: 1/3]\n"
            f"- [10:08] All 12 auth unit tests passing after the refactor\n"
            f"- [10:10] [Loop {loop_id} iter 2: complete]\n"
        )

        env = dict(os.environ)
        env["HIVE_HOME"] = str(llm_hive_env)
        env.pop("HIVE_SKIP_LLM", None)

        subprocess.run(
            [sys.executable, "-m", "keephive", "loop-extract", loop_id],
            capture_output=True,
            text=True,
            env=env,
            timeout=90,
        )

        pending = llm_hive_env / ".pending-facts.md"
        if pending.exists():
            lines = [ln for ln in pending.read_text().splitlines() if ln.strip().startswith("- ")]
            assert len(lines) >= 1, f"Expected ≥1 fact from rich log:\n{pending.read_text()}"
            for line in lines:
                fact = line.lstrip("- ").strip()
                assert len(fact) > 10, f"Fact too short to be meaningful: {fact!r}"


# ── TestLoopKnowledgeFlywheel ──────────────────────────────────────────────────


class TestLoopKnowledgeFlywheel:
    """Full cycle: loop completes → facts reviewed → facts seed next loop.

    These tests use mocked prompt_yn to control accept/decline behavior.
    No real LLM needed.
    """

    def test_accepted_fact_written_to_memory(
        self, llm_hive_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Accepted facts are appended to working/memory.md."""
        from keephive.storage import append_pending_facts, memory_file

        append_pending_facts(["JWT tokens expire after 15 minutes"], "loop-accept-test")
        monkeypatch.setattr("keephive.output.prompt_yn", lambda *_: True)

        from keephive.commands.loop import _cmd_run_review

        _cmd_run_review()
        assert "JWT tokens expire after 15 minutes" in memory_file().read_text()

    def test_declined_fact_stays_in_pending(
        self, llm_hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Facts declined during review are not written to memory."""
        from keephive.storage import append_pending_facts, memory_file, read_pending_facts

        append_pending_facts(["This fact will be declined"], "loop-decline-test")
        monkeypatch.setattr("keephive.output.prompt_yn", lambda *_: False)

        from keephive.commands.loop import _cmd_run_review

        _cmd_run_review()

        remaining_text = [item["fact"] for item in read_pending_facts()]
        assert "This fact will be declined" in remaining_text
        # Also confirm it was NOT added to memory
        if (mem := memory_file()).exists():
            assert "This fact will be declined" not in mem.read_text()

    def test_accepted_fact_logged_to_daily(
        self, llm_hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Accepting a fact writes a 'Loop review: accepted' entry to today's daily log."""
        import datetime

        from keephive.storage import append_pending_facts

        append_pending_facts(["Redis is single-threaded"], "loop-log-test")
        monkeypatch.setattr("keephive.output.prompt_yn", lambda *_: True)

        from keephive.commands.loop import _cmd_run_review

        _cmd_run_review()

        today = datetime.date.today().isoformat()
        daily = llm_hive_env / "daily" / f"{today}.md"
        assert daily.exists(), "Daily log not created after review accept"
        assert "Loop review: accepted" in daily.read_text()

    def test_summary_line_counts_accepted(
        self,
        llm_hive_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Review prints accurate count: '1 fact(s) added to memory' when 1 of 2 accepted."""
        from keephive.storage import append_pending_facts

        append_pending_facts(["Fact A to accept", "Fact B to decline"], "loop-summary-test")
        call_count: dict[str, int] = {"n": 0}

        def _mixed(*_: object) -> bool:
            call_count["n"] += 1
            return call_count["n"] == 1  # Accept first, decline second

        monkeypatch.setattr("keephive.output.prompt_yn", _mixed)

        from keephive.commands.loop import _cmd_run_review

        _cmd_run_review()
        out = capsys.readouterr().out
        assert "1 fact(s) added to memory" in out

    def test_memory_grows_after_review(
        self, llm_hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """memory.md line count increases after accepting a fact."""
        from keephive.storage import append_pending_facts, memory_file

        mem = memory_file()
        mem.write_text("# Working Memory\n\n- Existing fact\n")
        before = len(mem.read_text().splitlines())

        append_pending_facts(["New insight from loop"], "loop-grow-test")
        monkeypatch.setattr("keephive.output.prompt_yn", lambda *_: True)

        from keephive.commands.loop import _cmd_run_review

        _cmd_run_review()
        assert len(mem.read_text().splitlines()) > before

    def test_facts_seed_next_loop_context(
        self,
        llm_hive_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Memory facts relevant to the task appear in the CONTEXT block of the loop start."""
        from keephive.storage import memory_file

        memory_file().write_text(
            "# Working Memory\n\n- JWT authentication uses HMAC-SHA256 signing\n"
        )
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_SESSION_ID", "seed-ctx-session-unique")

        from keephive.commands.loop import cmd_loop

        cmd_loop(["implement JWT authentication flow"])
        out = capsys.readouterr().out
        assert "CONTEXT:" in out, f"Expected CONTEXT block in first-iter output:\n{out}"
        assert "JWT" in out


# ── TestLoopPromptImprovement ──────────────────────────────────────────────────


class TestLoopPromptImprovement:
    """Quality regression tests for loop prompts.

    If any test here fails after a change to loop.py or stop.py, it signals
    that the prompt quality degraded — a direct signal to improve it.
    """

    def test_continuation_has_enough_words(self, llm_hive_env: Path) -> None:
        """Continuation prompt includes task, iteration counter, and signal path (>8 words)."""
        _make_loop_file(
            llm_hive_env,
            "words-20260224-120000",
            session_id="sess-words",
            task="refactor the database access layer",
        )
        stdout, code = _run_stop_hook(llm_hive_env, "sess-words")
        assert code == 2
        ctx = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
        word_count = len(ctx.split())
        assert word_count > 8, f"Only {word_count} words: {ctx!r}"

    def test_memory_context_included_when_relevant(
        self,
        llm_hive_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """CONTEXT block present in first-iteration output when memory matches task topic."""
        from keephive.storage import memory_file

        memory_file().write_text(
            "# Working Memory\n\n"
            "- Database migrations must run before deployment\n"
            "- Use Alembic for Python database schema changes\n"
        )
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_SESSION_ID", "ctx-match-session-unique")

        from keephive.commands.loop import cmd_loop

        cmd_loop(["run database migrations for the release"])
        out = capsys.readouterr().out
        assert "CONTEXT:" in out

    def test_completion_message_has_review_hint(self, llm_hive_env: Path) -> None:
        """Completion message references 'review' so agent knows next step."""
        _make_loop_file(
            llm_hive_env,
            "review-hint-20260224-120000",
            session_id="sess-review-hint",
            iter=9,
            max_iter=10,
        )
        stdout, code = _run_stop_hook(llm_hive_env, "sess-review-hint")
        assert code == 0
        ctx = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
        assert "review" in ctx.lower()

    def test_first_iteration_has_absolute_signal_path(
        self,
        llm_hive_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """First-iteration output contains an absolute done-signal path."""
        import re

        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sigpath-session-unique")

        from keephive.commands.loop import cmd_loop

        cmd_loop(["test the signal path feature"])
        out = capsys.readouterr().out
        paths = re.findall(r"(/[^\s]+\.loop-done-[^\s]+)", out)
        assert paths, f"No absolute done-signal path found in:\n{out}"

    def test_task_always_appears_in_first_iter(
        self,
        llm_hive_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """TASK: block always present in first-iteration output regardless of memory."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_SESSION_ID", "task-block-session-unique")

        from keephive.commands.loop import cmd_loop

        cmd_loop(["implement the user profile page"])
        out = capsys.readouterr().out
        assert "TASK:" in out
        assert "implement the user profile page" in out
