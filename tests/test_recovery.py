"""Tests for state corruption recovery in keephive.storage.

Verifies that storage functions handle corrupted, missing, or invalid data
gracefully without crashing. Each test simulates a specific corruption scenario
and checks that the function recovers to a sane default state.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestTruncatedStatsJson:
    """Truncated .stats.json should return empty structure, not crash."""

    def test_corrupt_data_returns_default(self, hive_env: Path):
        """Truncated JSON, empty file, and missing file all return empty days."""
        from keephive.storage import read_stats, stats_file

        sf = stats_file()
        sf.parent.mkdir(parents=True, exist_ok=True)

        # Truncated JSON
        sf.write_text('{"days": {"2026-02-01": {"commands":')
        assert read_stats() == {"days": {}}

        # Empty file
        sf.write_text("")
        assert read_stats() == {"days": {}}

        # Missing file
        sf.unlink()
        assert read_stats() == {"days": {}}

    def test_valid_json_missing_days_key(self, hive_env: Path):
        from keephive.storage import read_stats, stats_file

        sf = stats_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text('{"version": 1}')
        result = read_stats()
        assert "days" in result


class TestEmptyMemory:
    """Empty memory.md should show 0 facts."""

    def test_empty_memory_returns_empty_string(self, hive_env: Path):
        from keephive.storage import memory_file, read_memory

        memory_file().write_text("")
        assert read_memory() == ""

    def test_missing_memory_returns_empty_string(self, hive_env: Path):
        from keephive.storage import memory_file, read_memory

        memory_file().unlink(missing_ok=True)
        assert read_memory() == ""

    def test_header_only_memory_has_no_facts(self, hive_env: Path):
        from keephive.storage import count_stale_facts, memory_file

        memory_file().write_text("# Working Memory\n\n")
        assert count_stale_facts() == 0


class TestMissingDailyDir:
    """Commands should create daily/ dir when missing."""

    def test_ensure_daily_creates_dir(self, hive_env: Path):
        from keephive.storage import ensure_daily

        daily = hive_env / "daily"
        if daily.exists():
            import shutil

            shutil.rmtree(daily)
        assert not daily.exists()

        path = ensure_daily("2026-01-15")
        assert path.exists()
        assert daily.exists()
        assert "Daily Log: 2026-01-15" in path.read_text()

    def test_ensure_dirs_creates_all_subdirs(self, hive_env: Path):
        import shutil

        from keephive.storage import ensure_dirs

        # Remove everything
        for child in hive_env.iterdir():
            if child.is_dir():
                shutil.rmtree(child)

        ensure_dirs()

        assert (hive_env / "working").is_dir()
        assert (hive_env / "daily").is_dir()
        assert (hive_env / "knowledge" / "guides").is_dir()
        assert (hive_env / "knowledge" / "prompts").is_dir()
        assert (hive_env / "archive").is_dir()
        assert (hive_env / "working" / "notes").is_dir()


class TestProfileCorruption:
    """Profile file pointing to deleted profile should fall back."""

    def test_active_profile_with_hive_home_ignores_profile_file(self, hive_env: Path):
        from keephive.storage import active_profile

        # HIVE_HOME is set by hive_env fixture, so profiles are bypassed
        result = active_profile()
        assert result is None

    def test_hive_dir_returns_default_with_no_profile(self, hive_env: Path):
        from keephive.storage import hive_dir

        # With HIVE_HOME set, hive_dir uses it directly
        assert hive_dir() == hive_env


class TestRecurringCorruption:
    """Invalid recurring.md entries should be skipped."""

    def test_absent_or_empty_returns_no_tasks(self, hive_env: Path):
        """Missing file and empty file both return empty list."""
        from keephive.storage import due_recurring

        # Missing file
        assert due_recurring() == []

        # Empty file
        rf = hive_env / "working" / "recurring.md"
        rf.write_text("")
        assert due_recurring() == []

    def test_invalid_data_still_returns_valid_tasks(self, hive_env: Path, monkeypatch):
        """Invalid freq lines and malformed dates are skipped; valid tasks survive."""
        from keephive.storage import due_recurring

        monkeypatch.setenv("HIVE_DATE", "2026-02-20")
        rf = hive_env / "working" / "recurring.md"
        rf.write_text(
            "# Recurring Tasks\n\n"
            "- [daily] Valid task\n"
            "- [notafreq] Invalid frequency entry\n"
            "- [weekly] Another valid task\n\n"
            "## Completed\n\n"
            "- Valid task: not-a-date\n"
        )
        result = due_recurring()
        texts = [t for _, t, _ in result]
        assert "Valid task" in texts
        assert "Another valid task" in texts
        assert "Invalid frequency entry" not in texts


class TestEvidenceCorruption:
    """Corrupt evidence.json should recover gracefully."""

    def test_corrupt_or_missing_recall_stats_returns_zero(self, hive_env: Path):
        """Corrupt and missing recall stats both return zero counts."""
        from keephive.storage import get_recall_count

        # Missing file
        assert get_recall_count("- some fact line") == 0

        # Corrupt file
        sf = hive_env / ".recall-stats.json"
        sf.write_text("not json at all{{{")
        assert get_recall_count("- some fact line") == 0

    def test_corrupt_recall_stats_hit_rate(self, hive_env: Path):
        from keephive.storage import get_recall_hit_rate

        sf = hive_env / ".recall-stats.json"
        sf.write_text("broken")
        assert get_recall_hit_rate() == (0, 0)


class TestSafeReadText:
    """safe_read_text handles all edge cases."""

    def test_existing_file(self, hive_env: Path):
        from keephive.storage import safe_read_text

        f = hive_env / "test.txt"
        f.write_text("hello")
        assert safe_read_text(f) == "hello"

    def test_missing_file_raises(self, hive_env: Path):
        from keephive.storage import safe_read_text

        f = hive_env / "nonexistent.txt"
        with pytest.raises(FileNotFoundError):
            safe_read_text(f)

    def test_binary_content_replaced(self, hive_env: Path):
        from keephive.storage import safe_read_text

        f = hive_env / "binary.txt"
        f.write_bytes(b"hello \xff\xfe world")
        result = safe_read_text(f)
        assert "hello" in result
        assert "world" in result

    def test_empty_file(self, hive_env: Path):
        from keephive.storage import safe_read_text

        f = hive_env / "empty.txt"
        f.write_text("")
        assert safe_read_text(f) == ""


class TestBackupAndWrite:
    """Atomic write with backup."""

    def test_creates_bak(self, hive_env: Path):
        from keephive.storage import backup_and_write

        target = hive_env / "test.md"
        target.write_text("original")
        backup_and_write(target, "updated")
        assert target.read_text() == "updated"
        assert target.with_suffix(".md.bak").read_text() == "original"

    def test_new_file_no_bak(self, hive_env: Path):
        from keephive.storage import backup_and_write

        target = hive_env / "new.md"
        backup_and_write(target, "fresh content")
        assert target.read_text() == "fresh content"
        assert not target.with_suffix(".md.bak").exists()


class TestDebugLog:
    """_debug_log only writes when HIVE_DEBUG is set."""

    def test_silent_without_debug(self, hive_env: Path, monkeypatch, capsys):
        # Reimport to pick up env change
        monkeypatch.delenv("HIVE_DEBUG", raising=False)
        import importlib

        import keephive.storage as mod

        importlib.reload(mod)
        mod._debug_log("should not appear")
        captured = capsys.readouterr()
        assert "should not appear" not in captured.err

    def test_logs_with_debug(self, hive_env: Path, monkeypatch, capsys):
        monkeypatch.setenv("HIVE_DEBUG", "1")
        import importlib

        import keephive.storage as mod

        importlib.reload(mod)
        mod._debug_log("test debug message")
        captured = capsys.readouterr()
        assert "test debug message" in captured.err


class TestActiveSlotCorruption:
    """Corrupt note slot marker should fall back to default."""

    def test_corrupt_slot_file_returns_default(self, hive_env: Path):
        """Non-numeric, empty, and missing slot files all fall back to 1."""
        from keephive.storage import active_slot

        marker = hive_env / "working" / ".note-active"

        # Non-numeric content
        marker.write_text("abc")
        assert active_slot() == 1

        # Empty file
        marker.write_text("")
        assert active_slot() == 1

        # Missing file
        marker.unlink()
        assert active_slot() == 1

    def test_out_of_range_slot_returns_default(self, hive_env: Path):
        from keephive.storage import active_slot

        marker = hive_env / "working" / ".note-active"
        marker.write_text("99")
        assert active_slot() == 1
