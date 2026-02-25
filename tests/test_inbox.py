"""Tests for hive inbox — KingBee output surfacing + review queue depths."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest

from keephive.commands.inbox import _parse_kingbee_entries, _plural, _queue_depths

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


def _run_inbox(args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    """Run cmd_inbox with a test console and return captured plain-text output."""
    from rich.console import Console

    buf = StringIO()
    test_console = Console(file=buf, no_color=True, width=120)
    monkeypatch.setattr("keephive.output.console", test_console)
    from keephive.commands.inbox import cmd_inbox

    cmd_inbox(args)
    return buf.getvalue()


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

    def test_parse_zero_content_entry(self):
        """KingBee header with no following content lines returns empty lines list."""
        log = "[KingBee 09:00] standup draft\n"
        entries = _parse_kingbee_entries(log)
        assert len(entries) == 1
        assert entries[0]["type"] == "standup draft"
        assert entries[0]["lines"] == []


class TestPlural:
    def test_singular(self):
        """Singular: "1 fact" not "1 facts"."""
        assert _plural(1, "fact") == "1 fact"

    def test_plural(self):
        """Plural: "2 facts"."""
        assert _plural(2, "fact") == "2 facts"

    def test_zero_plural(self):
        """Zero uses plural form."""
        assert _plural(0, "fact") == "0 facts"


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

        cmd_inbox([])

    def test_cmd_inbox_days_flag(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """--days 3 shows entries from exactly 3 days back."""
        from keephive.clock import get_today

        # Write a KingBee entry 3 days ago (at the edge of the window)
        three_days_ago = get_today() - timedelta(days=2)  # delta=2 → 3rd day included
        daily_path = hive_env / "daily" / f"{three_days_ago.isoformat()}.md"
        daily_path.write_text(
            f"# Daily Log: {three_days_ago.isoformat()}\n\n"
            "[KingBee 08:00] standup draft\nYesterday's summary line.\n\n"
        )

        output = _run_inbox(["--days", "3"], monkeypatch)
        assert "standup draft" in output

    def test_days_flag_excludes_beyond_range(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """--days 1 shows today only; entry from yesterday does not appear."""
        from keephive.clock import get_today

        yesterday = get_today() - timedelta(days=1)
        daily_path = hive_env / "daily" / f"{yesterday.isoformat()}.md"
        daily_path.write_text(
            f"# Daily Log: {yesterday.isoformat()}\n\n"
            "[KingBee 10:00] wander\nThis should not appear.\n\n"
        )

        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "This should not appear" not in output


class TestCmdInboxDaysClamping:
    def test_days_clamped_negative(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """--days -1 is clamped to 1: output says 'last 1 day'."""
        output = _run_inbox(["--days", "-1"], monkeypatch)
        assert "last 1 day" in output

    def test_days_clamped_large(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """--days 99 is clamped to 30: output says 'last 30 days'."""
        output = _run_inbox(["--days", "99"], monkeypatch)
        assert "last 30 days" in output

    def test_days_default_is_two_calendar_days(
        self, hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Default --days includes today AND yesterday."""
        from keephive.clock import get_today

        today = get_today()
        yesterday = today - timedelta(days=1)

        today_path = hive_env / "daily" / f"{today.isoformat()}.md"
        today_path.write_text(
            f"# Daily Log: {today.isoformat()}\n\n"
            "[KingBee 09:00] morning briefing\nToday content.\n\n"
        )
        yesterday_path = hive_env / "daily" / f"{yesterday.isoformat()}.md"
        yesterday_path.write_text(
            f"# Daily Log: {yesterday.isoformat()}\n\n"
            "[KingBee 22:00] wander\nYesterday content.\n\n"
        )

        output = _run_inbox([], monkeypatch)
        assert "morning briefing" in output
        assert "wander" in output


class TestCmdInboxContentDisplay:
    def _write_entry(self, hive_env: Path, type_name: str, lines: list[str]) -> None:
        """Write a KingBee entry to today's log."""
        from keephive.clock import get_today

        today = get_today()
        daily_path = hive_env / "daily" / f"{today.isoformat()}.md"
        content_block = "\n".join(lines)
        daily_path.write_text(
            f"# Daily Log: {today.isoformat()}\n\n[KingBee 10:00] {type_name}\n{content_block}\n\n"
        )

    def test_zero_content_entry_shows_placeholder(
        self, hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """KingBee entry with no content lines shows '(no output)' placeholder."""
        from keephive.clock import get_today

        today = get_today()
        daily_path = hive_env / "daily" / f"{today.isoformat()}.md"
        daily_path.write_text(
            f"# Daily Log: {today.isoformat()}\n\n[KingBee 10:00] standup draft\n\n"
        )

        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "(no output)" in output

    def test_six_line_cap_boundary_no_truncation(
        self, hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Entry with exactly 6 content lines: no truncation message."""
        self._write_entry(hive_env, "morning briefing", [f"Line {i}" for i in range(1, 7)])
        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "more line" not in output

    def test_seven_line_cap_shows_truncation(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """Entry with 7 content lines: shows '(1 more line)'."""
        self._write_entry(hive_env, "morning briefing", [f"Line {i}" for i in range(1, 8)])
        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "(1 more line)" in output

    def test_morning_briefing_long_shows_hive_log_hint(
        self, hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Long morning briefing entry (>6 lines) shows '→ hive log' navigation hint."""
        self._write_entry(hive_env, "morning briefing", [f"Detail {i}" for i in range(1, 9)])
        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "hive log" in output

    def test_wander_entry_shows_wander_show_hint(
        self, hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Wander entry always shows '→ hive wander show' hint regardless of length."""
        self._write_entry(hive_env, "wander", ["Seed: test", "Hypothesis: short"])
        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "hive wander show" in output


class TestCmdInboxQueueDisplay:
    def test_plural_grammar_singular(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """'1 fact to review' uses singular grammar, not '1 facts'."""
        from keephive.storage import hive_dir

        (hive_dir() / ".pending-facts.md").write_text("- FACT: Only one fact\n")
        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "1 fact to review" in output
        assert "1 facts" not in output

    def test_plural_grammar_plural(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """'2 facts to review' uses plural grammar."""
        from keephive.storage import hive_dir

        (hive_dir() / ".pending-facts.md").write_text("- FACT: First fact\n- FACT: Second fact\n")
        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "2 facts to review" in output

    def test_queue_alignment_arrows_consistent(
        self, hive_env: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """All queue → arrows appear at consistent column (same indent)."""
        from keephive.storage import hive_dir

        (hive_dir() / ".pending-facts.md").write_text(
            "- FACT: A\n- FACT: B\n- FACT: C\n- FACT: D\n- FACT: E\n"
            "- FACT: F\n- FACT: G\n- FACT: H\n- FACT: I\n- FACT: J\n"
            "- FACT: K\n- FACT: L\n- FACT: M\n- FACT: N\n- FACT: O\n"
            "- FACT: P\n"
        )
        (hive_dir() / ".pending-rules.md").write_text("- rule: Do something\n")
        output = _run_inbox(["--days", "1"], monkeypatch)
        # Both queue lines should contain →
        arrow_lines = [ln for ln in output.splitlines() if "→" in ln and "•" in ln]
        assert len(arrow_lines) == 2, f"Expected 2 queue lines with →, got: {arrow_lines}"
        # Both arrows should be at the same column position
        arrow_positions = [ln.index("→") for ln in arrow_lines]
        assert len(set(arrow_positions)) == 1, f"Arrows not aligned: positions {arrow_positions}"

    def test_combined_queues_and_activity(self, hive_env: Path, monkeypatch: pytest.MonkeyPatch):
        """Both KingBee activity section and queue section appear when both are populated."""
        from keephive.clock import get_today
        from keephive.storage import hive_dir

        today = get_today()
        (hive_env / "daily" / f"{today.isoformat()}.md").write_text(
            f"# Daily Log: {today.isoformat()}\n\n[KingBee 09:00] wander\nSome wander content.\n\n"
        )
        (hive_dir() / ".pending-facts.md").write_text("- FACT: Combined test fact\n")

        output = _run_inbox(["--days", "1"], monkeypatch)
        assert "KingBee Activity" in output
        assert "Needs Your Attention" in output
        assert "wander" in output
        assert "fact to review" in output
