"""Tests for hive inbox — KingBee output surfacing + review queue depths."""

from __future__ import annotations

from pathlib import Path

from keephive.commands.inbox import _parse_kingbee_entries, _queue_depths

# ---- Sample log text fixtures ----

_WANDER_LOG = """\
# Daily Log: 2026-02-24

- [00:46:18] DONE: Some prior task

[KingBee 01:13] wander
Seed: reinstall test seed [user-queued]
Hypothesis: Global installs without documented verification state create knowledge loss.
Question: Should hive track tool versions across reinstalls?

- [01:22:10] DONE: Part 1A: Follow-up work
"""

_BRIEFING_LOG = """\
# Daily Log: 2026-02-24

[🐝 KingBee 07:06] morning briefing
Wander shipped. 29 tests pass, _enable_task handles upgrades cleanly.
The claude -p socket conflict is confirmed. External terminal only.

- [08:27:24] DONE: Part 3: Tests + lint verification
"""

_EMPTY_LOG = """\
# Daily Log: 2026-02-24

- [10:00:00] FACT: Python is fast
- [10:05:00] TODO: Write more tests
- [10:10:00] DECISION: Use uv for everything
"""


class TestParseKingbeeEntries:
    def test_parse_kingbee_entries_wander(self):
        """Wander block parsed: correct time, type, seed/hypothesis/question lines."""
        entries = _parse_kingbee_entries(_WANDER_LOG)
        assert len(entries) == 1
        e = entries[0]
        assert e["time"] == "01:13"
        assert e["type"] == "wander"
        assert any("Seed" in line for line in e["lines"])
        assert any("Hypothesis" in line for line in e["lines"])
        assert any("Question" in line for line in e["lines"])

    def test_parse_kingbee_entries_briefing(self):
        """Emoji-prefix morning briefing block parsed: multi-line content captured."""
        entries = _parse_kingbee_entries(_BRIEFING_LOG)
        assert len(entries) == 1
        e = entries[0]
        assert e["time"] == "07:06"
        assert e["type"] == "morning briefing"
        assert len(e["lines"]) >= 2
        assert any("Wander shipped" in line for line in e["lines"])
        assert any("socket conflict" in line for line in e["lines"])

    def test_parse_kingbee_entries_empty(self):
        """Log with no KingBee entries returns empty list."""
        entries = _parse_kingbee_entries(_EMPTY_LOG)
        assert entries == []

    def test_multiple_entries_in_same_log(self):
        """Both a wander block and a briefing block in the same log are parsed."""
        combined = _WANDER_LOG + _BRIEFING_LOG
        entries = _parse_kingbee_entries(combined)
        assert len(entries) == 2
        types = {e["type"] for e in entries}
        assert "wander" in types
        assert "morning briefing" in types

    def test_content_ends_at_excerpt_comment(self):
        """Content block stops at the <!-- excerpt-hash marker."""
        log = "[KingBee 03:00] soul-update\nSome insight here.\n<!-- excerpt-hash:abc123 -->\n"
        entries = _parse_kingbee_entries(log)
        assert len(entries) == 1
        assert entries[0]["lines"] == ["Some insight here."]


class TestQueueDepths:
    def test_queue_depths(self, hive_env: Path):
        """Counts .pending-facts.md bullet lines correctly."""
        from keephive.storage import hive_dir

        pf = hive_dir() / ".pending-facts.md"
        pf.write_text(
            "# Pending Facts\n\n"
            "- FACT: Redis is fast\n"
            "- FACT: PostgreSQL uses MVCC\n"
            "- FACT: uv is better than pip\n"
        )
        depths = _queue_depths()
        assert depths["facts"] == 3

    def test_queue_depths_empty(self, hive_env: Path):
        """All queues report zero when no pending files exist."""
        depths = _queue_depths()
        assert depths["facts"] == 0
        assert depths["rules"] == 0
        assert depths["improvements"] == 0
        assert depths["todos"] == 0


class TestCmdInboxNoData:
    def test_cmd_inbox_no_data(self, hive_env: Path):
        """cmd_inbox([]) runs without error when no daily log exists."""
        from keephive.commands.inbox import cmd_inbox

        # Should not raise, daily dir is empty in hive_env fixture
        cmd_inbox([])

    def test_cmd_inbox_days_flag(self, hive_env: Path):
        """--days N argument is accepted without error."""
        from keephive.commands.inbox import cmd_inbox

        cmd_inbox(["--days", "3"])
