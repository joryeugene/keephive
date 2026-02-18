"""Quality tests: verify output quality, not just exit codes.

These tests prove the fixes for bugs A1-C8 found during deep stress testing.
Each test verifies that keephive produces useful, correct output.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest


# ---------- A1: Memory line merge on append ----------


class TestMemAppend:
    """Verify appending to memory/rules doesn't corrupt existing content."""

    def test_mem_append_no_trailing_newline(self, hive_env):
        """A1: When memory.md lacks trailing newline, new fact starts on its own line."""
        from keephive.commands.memory import cmd_mem
        from keephive.storage import memory_file

        mem = memory_file()
        # Write without trailing newline
        mem.write_text("# Working Memory\n\n- old fact [verified:2026-01-01]")

        cmd_mem(["new fact"])

        lines = mem.read_text().splitlines()
        # Each fact must be on its own line
        fact_lines = [l for l in lines if l.startswith("- ")]
        assert len(fact_lines) == 2, f"Expected 2 fact lines, got {fact_lines}"
        assert "old fact" in fact_lines[0]
        assert "new fact" in fact_lines[1]

    def test_mem_append_normal(self, hive_env):
        """Normal append works when file has trailing newline."""
        from keephive.commands.memory import cmd_mem
        from keephive.storage import memory_file

        mem = memory_file()
        mem.write_text("# Working Memory\n\n- old fact [verified:2026-01-01]\n")

        cmd_mem(["another fact"])

        lines = mem.read_text().splitlines()
        fact_lines = [l for l in lines if l.startswith("- ")]
        assert len(fact_lines) == 2
        assert "old fact" in fact_lines[0]
        assert "another fact" in fact_lines[1]

    def test_rule_append_no_trailing_newline(self, hive_env):
        """A1 for rules: same fix applies to cmd_rule."""
        from keephive.commands.memory import cmd_rule
        from keephive.storage import rules_file

        rf = rules_file()
        rf.write_text("# Working Rules\n\n- old rule")

        cmd_rule(["new rule"])

        lines = rf.read_text().splitlines()
        rule_lines = [l for l in lines if l.startswith("- ")]
        assert len(rule_lines) == 2, f"Expected 2 rule lines, got {rule_lines}"
        assert "old rule" in rule_lines[0]
        assert "new rule" in rule_lines[1]


# ---------- A2: UnicodeDecodeError on non-UTF-8 files ----------


class TestEncoding:
    """Verify non-UTF-8 bytes don't crash reads."""

    def test_daily_with_bad_encoding(self, hive_env):
        """A2: Non-UTF-8 bytes in daily log don't crash status."""
        from keephive.storage import count_daily_entries, get_meaningful_entries

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        # Write bytes with invalid UTF-8 sequences
        content = b"# Daily Log\n\n- [10:00:00] FACT: good entry\n- [10:05:00] bad: \xff\xfe bytes here\n"
        daily.write_bytes(content)

        # Should not crash
        count = count_daily_entries()
        assert count >= 1
        entries = get_meaningful_entries()
        assert len(entries) >= 1

    def test_safe_read_text(self, tmp_path):
        """safe_read_text replaces bad bytes instead of raising."""
        from keephive.storage import safe_read_text

        p = tmp_path / "bad_encoding.md"
        p.write_bytes(b"hello \xff\xfe world\n")
        text = safe_read_text(p)
        assert "hello" in text
        assert "world" in text


# ---------- A3: _extract_cmds missing separator ----------


class TestExtractCmds:
    """Verify command extraction works for all hook formats."""

    def test_extract_cmds_flat(self):
        """Flat format: single command key."""
        from keephive.commands.setup import _extract_cmds

        entry = {"command": "keephive hook-sessionstart"}
        result = _extract_cmds(entry)
        assert "keephive hook-sessionstart" in result

    def test_extract_cmds_grouped(self):
        """Grouped format: matcher + hooks array."""
        from keephive.commands.setup import _extract_cmds

        entry = {
            "matcher": "*",
            "hooks": [{"type": "command", "command": "keephive hook-sessionstart"}],
        }
        result = _extract_cmds(entry)
        assert "keephive hook-sessionstart" in result

    def test_extract_cmds_mixed_format(self):
        """A3: Mixed format with both top-level command and nested hooks."""
        from keephive.commands.setup import _extract_cmds

        entry = {
            "command": "echo starting",
            "hooks": [{"type": "command", "command": "keephive hook-sessionstart"}],
        }
        result = _extract_cmds(entry)
        # Must have space between parts, not "echo startingkeephive..."
        assert "echo starting" in result
        assert "keephive hook-sessionstart" in result
        assert "echo startingkeephive" not in result


# ---------- B4: Session entries are useless noise ----------


class TestSessionNoise:
    """Verify session starts don't pollute daily logs."""

    def test_sessionstart_no_session_noise(self, hive_env):
        """B4: hook-sessionstart does not write session entries to daily log."""
        import subprocess
        import sys

        env = {
            "HIVE_SKIP_LLM": "1",
            "HIVE_HOME": str(hive_env),
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
        }
        subprocess.run(
            [sys.executable, "-m", "keephive", "hook-sessionstart"],
            capture_output=True,
            text=True,
            env=env,
            input='{"source":"test","cwd":"/test/project"}',
        )

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        if daily.exists():
            content = daily.read_text()
            assert "session [" not in content, f"Found session noise in daily log: {content}"


# ---------- B5: Duplicate AI summaries ----------


class TestPrecompactDedup:
    """Verify near-duplicate insights are not written twice."""

    def test_is_duplicate_insight(self, hive_env):
        """B5: _is_duplicate_insight catches similar text."""
        from keephive.hooks.precompact import _is_duplicate_insight

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] FACT: Python uses uv for package management\n"
        )

        # Very similar text should be caught
        assert _is_duplicate_insight(daily, "Python uses uv for package management")
        # Slightly different but similar should also be caught
        assert _is_duplicate_insight(daily, "Python uses uv for managing packages")
        # Completely different text should not be caught
        assert not _is_duplicate_insight(daily, "Rust compile times are fast")

    def test_precompact_no_noise_entries(self, hive_env):
        """C8: Compaction events don't appear in daily log."""
        import subprocess
        import sys

        env = {
            "HIVE_SKIP_LLM": "1",
            "HIVE_HOME": str(hive_env),
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
        }
        subprocess.run(
            [sys.executable, "-m", "keephive", "hook-precompact"],
            capture_output=True,
            text=True,
            env=env,
            input='{"trigger":"test","transcript_path":""}',
        )

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        if daily.exists():
            content = daily.read_text()
            assert "compacted (" not in content, f"Found compaction noise: {content}"


# ---------- B6: Duplicate TODOs ----------


class TestTodoDedup:
    """Verify near-duplicate TODOs are collapsed."""

    def test_todos_dedup(self, hive_env):
        """B6: Near-identical TODOs across days resolve to one."""
        from keephive.storage import open_todos

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] TODO: Research portable context standards\n"
            "- [10:05:00] TODO: Research portable context standards for agents\n"
            "- [10:10:00] TODO: Research portable context standards for memory\n"
        )

        todos = open_todos()
        # All three are near-duplicates (>0.8 similarity), should collapse to 1
        matching = [t for _, _, t in todos if "portable context" in t.lower()]
        assert len(matching) == 1, f"Expected 1 deduped TODO, got {len(matching)}: {matching}"

    def test_todos_different_kept(self, hive_env):
        """Distinct TODOs are preserved."""
        from keephive.storage import open_todos

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] TODO: Fix authentication bug\n"
            "- [10:05:00] TODO: Write documentation for API\n"
            "- [10:10:00] TODO: Deploy to production\n"
        )

        todos = open_todos()
        assert len(todos) == 3


# ---------- C7: Noise filter ----------


class TestNoiseFilter:
    """Verify process narration is filtered from excerpts."""

    def _make_transcript(self, tmp_path: Path, assistant_texts: list[str]) -> str:
        """Create a fake transcript with assistant messages."""
        transcript = tmp_path / "transcript.jsonl"
        lines = []
        for text in assistant_texts:
            lines.append(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]},
            }))
        transcript.write_text("\n".join(lines))
        return str(transcript)

    def test_noise_filter_catches_narration(self, tmp_path):
        """C7: Process narration strings are filtered out."""
        from keephive.hooks.precompact import _extract_excerpts

        narration = [
            "The test file shows that all assertions pass correctly and the module loads without errors.",
            "This confirms that the refactoring worked as expected and no regressions were introduced.",
            "Based on the output above, the function handles edge cases properly in all scenarios tested.",
            "After reading the file, I can see that the implementation follows the expected pattern closely.",
            "It looks like the configuration is set up correctly and all the required fields are present.",
            "That makes sense given the architecture decisions we discussed in the previous session today.",
        ]
        transcript = self._make_transcript(tmp_path, narration)
        excerpts = _extract_excerpts(transcript, 4000)
        # All narration should be filtered
        assert excerpts == "", f"Expected empty excerpts, got: {excerpts}"

    def test_noise_filter_keeps_substance(self, tmp_path):
        """Substantive assistant messages are kept."""
        from keephive.hooks.precompact import _extract_excerpts

        substance = [
            "The authentication middleware validates JWT tokens by checking the signature against the public key stored in the environment variable AUTH_PUBLIC_KEY.",
        ]
        transcript = self._make_transcript(tmp_path, substance)
        excerpts = _extract_excerpts(transcript, 4000)
        assert "authentication" in excerpts.lower()

    def test_short_assistant_messages_filtered(self, tmp_path):
        """Assistant messages under 80 chars are filtered (likely process narration)."""
        from keephive.hooks.precompact import _extract_excerpts

        short_msgs = [
            "I found the bug in the login handler function.",  # 49 chars
            "Updating the configuration file now for you.",  # 45 chars
        ]
        transcript = self._make_transcript(tmp_path, short_msgs)
        excerpts = _extract_excerpts(transcript, 4000)
        assert excerpts == ""


# ---------- Entry quality ----------


class TestEntryQuality:
    """Verify meaningful entries exclude noise."""

    def test_meaningful_entries_no_noise(self, hive_env):
        """get_meaningful_entries returns only signal, not session/compaction noise."""
        from keephive.storage import get_meaningful_entries

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] FACT: Python 3.12 supports type param syntax\n"
            "- [10:05:00] session [keephive] /home/dev/keephive\n"
            "- [10:10:00] compacted (auto_compact)\n"
            "- [10:15:00] DECISION: Use Pydantic for validation\n"
            "- [10:20:00] session [project] /some/path\n"
        )

        entries = get_meaningful_entries()
        entry_text = "\n".join(entries)
        assert "FACT" in entry_text
        assert "DECISION" in entry_text
        assert "session" not in entry_text.lower()
        assert "compacted" not in entry_text.lower()

    def test_status_entry_count_accurate(self, hive_env):
        """count_daily_entries counts only meaningful entries."""
        from keephive.storage import count_daily_entries

        today_str = date.today().isoformat()
        daily = hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] FACT: real entry 1\n"
            "- [10:05:00] session [keephive] /path\n"
            "- [10:10:00] compacted (auto_compact)\n"
            "- [10:15:00] DECISION: real entry 2\n"
            "- [10:20:00] TODO: real entry 3\n"
        )

        count = count_daily_entries()
        assert count == 3, f"Expected 3 meaningful entries, got {count}"
