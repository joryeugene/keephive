"""Tests for the nudge system (counter-based periodic reminders with lifecycle state machine)."""

from __future__ import annotations

import json
from pathlib import Path


class TestCounterPersistence:
    def test_read_empty(self, hive_env):
        """No counter file returns (0, '')."""
        from keephive.nudge import read_counter

        count, session = read_counter("test")
        assert count == 0
        assert session == ""

    def test_write_and_read(self, hive_env):
        """Write counter, read it back."""
        from keephive.nudge import read_counter, write_counter

        write_counter("test", 5, "session-abc")
        count, session = read_counter("test")
        assert count == 5
        assert session == "session-abc"

    def test_counter_file_location(self, hive_env):
        """Counter file lives in hive dir as .{name}-counter."""
        from keephive.nudge import write_counter

        write_counter("prompt", 1, "s1")
        assert (hive_env / ".prompt-counter").exists()

    def test_corrupt_counter(self, hive_env):
        """Corrupt counter file returns defaults."""
        from keephive.nudge import read_counter

        path = hive_env / ".test-counter"
        path.write_text("not json")
        count, session = read_counter("test")
        assert count == 0
        assert session == ""


class TestShouldNudge:
    def test_increments_counter(self, hive_env):
        """Each call increments the counter."""
        from keephive.nudge import read_counter, should_nudge

        should_nudge("test", "s1")
        count, _ = read_counter("test")
        assert count == 1

        should_nudge("test", "s1")
        count, _ = read_counter("test")
        assert count == 2

    def test_fires_at_interval(self, hive_env, monkeypatch):
        """Fires every N calls (default 5)."""
        from keephive.nudge import should_nudge

        monkeypatch.setenv("HIVE_NUDGE_INTERVAL", "5")

        results = []
        for i in range(15):
            fire, count = should_nudge("test", "s1")
            results.append((fire, count))

        # Should fire at count 5, 10, 15
        fires = [count for fire, count in results if fire]
        assert fires == [5, 10, 15]

    def test_stop_uses_own_interval(self, hive_env, monkeypatch):
        """Stop hook uses HIVE_STOP_NUDGE_INTERVAL, not HIVE_NUDGE_INTERVAL."""
        from keephive.nudge import should_nudge

        monkeypatch.setenv("HIVE_NUDGE_INTERVAL", "3")
        monkeypatch.setenv("HIVE_STOP_NUDGE_INTERVAL", "4")

        # "stop" counter uses interval 4
        results = []
        for _ in range(8):
            fire, count = should_nudge("stop", "s1")
            results.append((fire, count))

        fires = [count for fire, count in results if fire]
        assert fires == [4, 8]

        # "prompt" counter uses interval 3
        results = []
        for _ in range(9):
            fire, count = should_nudge("prompt", "s1")
            results.append((fire, count))

        fires = [count for fire, count in results if fire]
        assert fires == [3, 6, 9]

    def test_resets_on_session_change(self, hive_env, monkeypatch):
        """Counter resets when session ID changes."""
        from keephive.nudge import read_counter, should_nudge

        monkeypatch.setenv("HIVE_NUDGE_INTERVAL", "3")

        should_nudge("test", "session-1")
        should_nudge("test", "session-1")
        count, _ = read_counter("test")
        assert count == 2

        # New session resets
        should_nudge("test", "session-2")
        count, session = read_counter("test")
        assert count == 1
        assert session == "session-2"

    def test_custom_interval_env(self, hive_env, monkeypatch):
        """HIVE_NUDGE_INTERVAL env var changes the interval."""
        from keephive.nudge import should_nudge

        monkeypatch.setenv("HIVE_NUDGE_INTERVAL", "2")

        fire1, _ = should_nudge("test", "s1")
        fire2, _ = should_nudge("test", "s1")
        assert fire1 is False
        assert fire2 is True

    def test_separate_counters(self, hive_env):
        """Different counter names are independent."""
        from keephive.nudge import read_counter, should_nudge

        should_nudge("prompt", "s1")
        should_nudge("prompt", "s1")
        should_nudge("tool", "s1")

        prompt_count, _ = read_counter("prompt")
        tool_count, _ = read_counter("tool")
        assert prompt_count == 2
        assert tool_count == 1


class TestLifecycleNudge:
    """Test the priority-based lifecycle nudge state machine."""

    def test_todo_nudge_highest_priority(self, hive_env):
        """Open TODOs produce a specific TODO nudge (priority 1)."""
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        append_to_daily("- [09:00:00] TODO: Deploy the staging environment")

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("prompt")
        assert "Deploy the staging environment" in result
        assert "hive td" in result

    def test_stale_facts_priority_two(self, hive_env):
        """Stale facts trigger verification nudge when no TODOs exist."""
        from keephive.storage import memory_file

        # Memory has a fact with very old verified date (stale)
        mem = memory_file()
        mem.write_text("# Memory\n- Old fact about Python [verified:2020-01-01]\n")

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("prompt")
        assert "unverified" in result
        assert "hive v" in result

    def test_pending_facts_priority_three(self, hive_env):
        """Pending facts trigger review nudge when no stale facts or TODOs."""
        from keephive.storage import hive_dir, memory_file

        # Fresh memory (no stale)
        mem = memory_file()
        mem.write_text("# Memory\n- Fresh fact [verified:2026-02-20]\n")

        # Pending facts exist
        pending = hive_dir() / ".pending-facts.md"
        pending.write_text("- Some pending fact [auto:2026-02-20]\n- Another [auto:2026-02-20]\n")

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("prompt")
        assert "pending review" in result
        assert "hive mem review" in result

    def test_unreflected_logs_priority_four(self, hive_env):
        """7+ daily logs trigger reflect nudge when no higher-priority items."""
        from keephive.storage import daily_dir, memory_file

        # Fresh memory, no pending, no TODOs
        mem = memory_file()
        mem.write_text("# Memory\n- Fresh fact [verified:2026-02-20]\n")

        # Create 8 daily log files
        dd = daily_dir()
        for i in range(8):
            (dd / f"2026-02-{10 + i:02d}.md").write_text(f"# Log {i}\n- entry\n")

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("prompt")
        assert "daily logs" in result
        assert "hive rf" in result

    def test_fallback_context_prompt(self, tmp_path, monkeypatch):
        """With nothing actionable, prompt context gets recall suggestion."""
        hd = tmp_path / "clean_hive"
        hd.mkdir()
        (hd / "working").mkdir()
        (hd / "daily").mkdir()
        (hd / "knowledge" / "guides").mkdir(parents=True)
        (hd / "knowledge" / "prompts").mkdir(parents=True)
        (hd / "working" / "notes").mkdir()
        (hd / "archive").mkdir()
        (hd / "working" / "memory.md").write_text(
            "# Working Memory\n\n- All facts current [verified:2026-02-17]\n"
        )
        monkeypatch.setenv("HIVE_HOME", str(hd))

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("prompt")
        assert "hive_recall" in result

    def test_fallback_context_tool(self, tmp_path, monkeypatch):
        """Tool context gets decision-recording suggestion."""
        hd = tmp_path / "clean_hive"
        hd.mkdir()
        (hd / "working").mkdir()
        (hd / "daily").mkdir()
        (hd / "knowledge" / "guides").mkdir(parents=True)
        (hd / "knowledge" / "prompts").mkdir(parents=True)
        (hd / "working" / "notes").mkdir()
        (hd / "archive").mkdir()
        (hd / "working" / "memory.md").write_text(
            "# Working Memory\n\n- All facts current [verified:2026-02-17]\n"
        )
        monkeypatch.setenv("HIVE_HOME", str(hd))

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("tool")
        assert "DECISION" in result

    def test_fallback_context_stop(self, tmp_path, monkeypatch):
        """Stop context gets end-of-turn capture suggestion."""
        hd = tmp_path / "clean_hive"
        hd.mkdir()
        (hd / "working").mkdir()
        (hd / "daily").mkdir()
        (hd / "knowledge" / "guides").mkdir(parents=True)
        (hd / "knowledge" / "prompts").mkdir(parents=True)
        (hd / "working" / "notes").mkdir()
        (hd / "archive").mkdir()
        (hd / "working" / "memory.md").write_text(
            "# Working Memory\n\n- All facts current [verified:2026-02-17]\n"
        )
        monkeypatch.setenv("HIVE_HOME", str(hd))

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("stop")
        assert "hive_remember" in result

    def test_long_todo_truncated_in_nudge(self, hive_env):
        """TODOs longer than 60 chars are truncated with ellipsis."""
        from keephive.storage import append_to_daily, ensure_daily

        ensure_daily()
        long_todo = "A" * 80
        append_to_daily(f"- [09:00:00] TODO: {long_todo}")

        from keephive.nudge import _lifecycle_nudge

        result = _lifecycle_nudge("prompt")
        assert "..." in result
        assert len(result) < 200


class TestUnreflectedLogCount:
    """Test _unreflected_log_count helper."""

    def test_no_logs(self, hive_env):
        from keephive.nudge import _unreflected_log_count

        assert _unreflected_log_count() == 0

    def test_counts_all_without_reflect_date(self, hive_env):
        """Without .last-reflect-date, counts all daily logs."""
        from keephive.storage import daily_dir

        dd = daily_dir()
        for i in range(3):
            (dd / f"2026-02-{10 + i:02d}.md").write_text("# Log\n")

        from keephive.nudge import _unreflected_log_count

        assert _unreflected_log_count() == 3

    def test_respects_reflect_date(self, hive_env):
        """Only counts logs after the last reflect date."""
        from keephive.storage import daily_dir, hive_dir

        dd = daily_dir()
        (dd / "2026-02-10.md").write_text("# Old\n")
        (dd / "2026-02-15.md").write_text("# Mid\n")
        (dd / "2026-02-20.md").write_text("# New\n")

        # Set last reflect to 2026-02-14
        (hive_dir() / ".last-reflect-date").write_text("2026-02-14")

        from keephive.nudge import _unreflected_log_count

        # Only 2026-02-15 and 2026-02-20 are after the cutoff
        assert _unreflected_log_count() == 2


class TestBuildOutput:
    def test_build_nudge_output_format(self, hive_env):
        """Output is valid JSON with hookSpecificOutput."""
        from keephive.nudge import build_nudge_output

        output = build_nudge_output("test nudge", event_name="PostToolUse")
        data = json.loads(output)
        assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert data["hookSpecificOutput"]["additionalContext"] == "test nudge"

    def test_build_nudge_output_userprompt(self, hive_env):
        """UserPromptSubmit event name is set correctly."""
        from keephive.nudge import build_nudge_output

        output = build_nudge_output("test", event_name="UserPromptSubmit")
        data = json.loads(output)
        assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


class TestHookIntegration:
    def _run_hook(self, args, hive_home, stdin):
        import subprocess
        import sys

        env = {
            "HIVE_SKIP_LLM": "1",
            "HIVE_HOME": str(hive_home),
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin:"
            + (Path.home() / ".local/bin").as_posix(),
        }
        return subprocess.run(
            [sys.executable, "-m", "keephive"] + args,
            capture_output=True,
            text=True,
            env=env,
            input=stdin,
        )

    def test_userpromptsubmit_full_cycle(self, hive_env):
        """Run hook 5 times (default interval), 5th produces output."""
        for i in range(4):
            r = self._run_hook(
                ["hook-userpromptsubmit"],
                hive_env,
                json.dumps({"prompt": f"prompt {i}", "session_id": "cycle-test"}),
            )
            assert r.returncode == 0
            assert r.stdout.strip() == "", f"Call {i + 1} should be silent"

        # 5th call should produce nudge
        r = self._run_hook(
            ["hook-userpromptsubmit"],
            hive_env,
            json.dumps({"prompt": "prompt 4", "session_id": "cycle-test"}),
        )
        assert r.returncode == 0
        assert r.stdout.strip() != "", "5th call should produce nudge"
        data = json.loads(r.stdout)
        assert "additionalContext" in data["hookSpecificOutput"]

    def test_posttooluse_full_cycle(self, hive_env):
        """Run PostToolUse hook 5 times, 5th produces output."""
        for i in range(4):
            r = self._run_hook(
                ["hook-posttooluse"],
                hive_env,
                json.dumps({"session_id": "tool-cycle", "tool_name": "Edit"}),
            )
            assert r.returncode == 0
            assert r.stdout.strip() == "", f"Call {i + 1} should be silent"

        r = self._run_hook(
            ["hook-posttooluse"],
            hive_env,
            json.dumps({"session_id": "tool-cycle", "tool_name": "Edit"}),
        )
        assert r.returncode == 0
        assert r.stdout.strip() != "", "5th call should produce nudge"
        data = json.loads(r.stdout)
        assert "additionalContext" in data["hookSpecificOutput"]

    def test_session_reset_restarts_counter(self, hive_env):
        """New session ID resets counter, so nudge doesn't fire at wrong time."""
        # Run 3 times with session A (not enough for interval 5)
        for i in range(3):
            self._run_hook(
                ["hook-userpromptsubmit"],
                hive_env,
                json.dumps({"prompt": f"p{i}", "session_id": "session-A"}),
            )

        # Switch to session B, counter resets
        r = self._run_hook(
            ["hook-userpromptsubmit"],
            hive_env,
            json.dumps({"prompt": "first in B", "session_id": "session-B"}),
        )
        assert r.returncode == 0
        assert r.stdout.strip() == "", "First prompt in new session should be silent"

    def test_no_session_id_is_silent(self, hive_env):
        """Missing session_id produces no output."""
        r = self._run_hook(
            ["hook-userpromptsubmit"],
            hive_env,
            json.dumps({"prompt": "no session"}),
        )
        assert r.returncode == 0
        assert r.stdout.strip() == ""
