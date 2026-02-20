"""Tests for the nudge system (counter-based periodic reminders)."""

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
        """Fires every N calls (default 8)."""
        from keephive.nudge import should_nudge

        monkeypatch.setenv("HIVE_NUDGE_INTERVAL", "4")

        results = []
        for i in range(12):
            fire, count = should_nudge("test", "s1")
            results.append((fire, count))

        # Should fire at count 4, 8, 12
        fires = [count for fire, count in results if fire]
        assert fires == [4, 8, 12]

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


class TestNudgeRotation:
    def test_prompt_nudge_cycles(self, hive_env, monkeypatch):
        """Prompt nudges rotate through different messages."""
        from keephive.nudge import get_prompt_nudge

        monkeypatch.setenv("HIVE_NUDGE_INTERVAL", "8")

        # Each multiple of interval gets a different nudge
        nudge_8 = get_prompt_nudge(8)
        nudge_16 = get_prompt_nudge(16)
        nudge_24 = get_prompt_nudge(24)

        # All nudges should be non-empty strings
        for nudge in [nudge_8, nudge_16, nudge_24]:
            assert isinstance(nudge, str)
            assert len(nudge) > 10

        # Nudges should rotate (not all the same)
        nudges = {nudge_8, nudge_16, nudge_24}
        assert len(nudges) >= 2, "Nudges should rotate"

    def test_tool_nudge_cycles(self, hive_env, monkeypatch):
        """Tool nudges rotate through different messages."""
        from keephive.nudge import get_tool_nudge

        monkeypatch.setenv("HIVE_NUDGE_INTERVAL", "8")

        nudge_8 = get_tool_nudge(8)
        nudge_16 = get_tool_nudge(16)
        nudge_24 = get_tool_nudge(24)

        for nudge in [nudge_8, nudge_16, nudge_24]:
            assert isinstance(nudge, str)
            assert len(nudge) > 10

        nudges = {nudge_8, nudge_16, nudge_24}
        assert len(nudges) >= 2, "Nudges should rotate"

    def test_status_aware_nudge_fallback(self, tmp_path, monkeypatch):
        """Status-aware slot falls back when nothing actionable."""
        # Use a clean hive dir with no stale facts
        hd = tmp_path / "clean_hive"
        hd.mkdir()
        (hd / "working").mkdir()
        (hd / "daily").mkdir()
        (hd / "knowledge" / "guides").mkdir(parents=True)
        (hd / "knowledge" / "prompts").mkdir(parents=True)
        (hd / "working" / "notes").mkdir()
        (hd / "archive").mkdir()
        # Memory with no stale facts
        (hd / "working" / "memory.md").write_text(
            "# Working Memory\n\n- All facts current [verified:2026-02-17]\n"
        )
        monkeypatch.setenv("HIVE_HOME", str(hd))

        from keephive.nudge import _status_nudge

        result = _status_nudge()
        assert result is None  # Nothing actionable


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
        """Run hook 8 times (default interval), 8th produces output."""
        for i in range(7):
            r = self._run_hook(
                ["hook-userpromptsubmit"],
                hive_env,
                json.dumps({"prompt": f"prompt {i}", "session_id": "cycle-test"}),
            )
            assert r.returncode == 0
            assert r.stdout.strip() == "", f"Call {i + 1} should be silent"

        # 8th call should produce nudge
        r = self._run_hook(
            ["hook-userpromptsubmit"],
            hive_env,
            json.dumps({"prompt": "prompt 7", "session_id": "cycle-test"}),
        )
        assert r.returncode == 0
        assert r.stdout.strip() != "", "8th call should produce nudge"
        data = json.loads(r.stdout)
        assert "additionalContext" in data["hookSpecificOutput"]

    def test_posttooluse_full_cycle(self, hive_env):
        """Run PostToolUse hook 8 times, 8th produces output."""
        for i in range(7):
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
        assert r.stdout.strip() != "", "8th call should produce nudge"
        data = json.loads(r.stdout)
        assert "additionalContext" in data["hookSpecificOutput"]

    def test_session_reset_restarts_counter(self, hive_env):
        """New session ID resets counter, so nudge doesn't fire at wrong time."""
        # Run 6 times with session A
        for i in range(6):
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
