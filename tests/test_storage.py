"""Tests for keephive.storage module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---- parse_date_arg ----


class TestParseDateArg:
    def test_today_keyword(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import parse_date_arg

        assert parse_date_arg("today") == "2026-01-15"

    def test_yesterday_across_month_boundary(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-01")
        from keephive.storage import parse_date_arg

        assert parse_date_arg("yesterday") == "2026-02-28"

    def test_digit_days_ago(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import parse_date_arg

        assert parse_date_arg("3") == "2026-01-12"

    def test_invalid_input_passes_through(self, hive_env):
        """Invalid date format is returned as-is for downstream handling."""
        from keephive.storage import parse_date_arg

        assert parse_date_arg("2026-13-99") == "2026-13-99"
        assert parse_date_arg("not-a-date") == "not-a-date"


# ---- hive_dir ----


class TestHiveDir:
    def test_uses_hive_home_env(self, hive_env, monkeypatch):
        from keephive.storage import hive_dir

        assert hive_dir() == Path(str(hive_env))

    def test_default_without_env(self, monkeypatch):
        monkeypatch.delenv("HIVE_HOME", raising=False)
        # Also ensure no profile file exists
        from keephive.storage import _claude_dir, hive_dir

        pf = _claude_dir() / ".hive-profile"
        if pf.exists():
            pf.unlink()

        result = hive_dir()
        assert result == Path.home() / ".claude" / "hive"


# ---- daily_file ----


class TestDailyFile:
    def test_name_and_parent(self, hive_env):
        from keephive.storage import daily_dir, daily_file

        f = daily_file("2026-05-20")
        assert f.name == "2026-05-20.md"
        assert f.parent == daily_dir()


# ---- ensure_dirs ----


class TestEnsureDirs:
    def test_creates_all_subdirectories_idempotent(self, tmp_path, monkeypatch):
        hive = tmp_path / "fresh-hive"
        monkeypatch.setenv("HIVE_HOME", str(hive))
        from keephive.storage import ensure_dirs

        ensure_dirs()

        assert (hive / "working").is_dir()
        assert (hive / "daily").is_dir()
        assert (hive / "knowledge").is_dir()
        assert (hive / "knowledge" / "guides").is_dir()
        assert (hive / "knowledge" / "prompts").is_dir()
        assert (hive / "archive").is_dir()
        assert (hive / "working" / "notes").is_dir()

        # Idempotent: second call does not error
        ensure_dirs()


# ---- ensure_daily ----


class TestEnsureDaily:
    def test_creates_file_with_header(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import ensure_daily

        path = ensure_daily()
        assert path.exists()
        content = path.read_text()
        assert "# Daily Log: 2026-01-15" in content

    def test_specific_day(self, hive_env):
        from keephive.storage import ensure_daily

        path = ensure_daily("2026-06-01")
        assert path.name == "2026-06-01.md"
        assert "# Daily Log: 2026-06-01" in path.read_text()

    def test_does_not_overwrite_existing(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import ensure_daily

        path = ensure_daily()
        path.write_text("# My custom header\n- entry one\n")

        path2 = ensure_daily()
        assert path2.read_text() == "# My custom header\n- entry one\n"


# ---- safe_read_text / memory_file / rules_file ----


class TestFileIO:
    def test_safe_read_text_existing(self, hive_env):
        from keephive.storage import memory_file, safe_read_text

        content = safe_read_text(memory_file())
        assert "Working Memory" in content

    def test_safe_read_text_missing_file(self, hive_env):
        from keephive.storage import safe_read_text

        with pytest.raises(FileNotFoundError):
            safe_read_text(hive_env / "nonexistent.txt")

    def test_read_memory_returns_content(self, hive_env):
        from keephive.storage import read_memory

        mem = read_memory()
        assert "Python is great" in mem
        assert "keephive uses Pydantic" in mem

    def test_read_memory_missing_file(self, hive_env):
        from keephive.storage import memory_file, read_memory

        memory_file().unlink()
        assert read_memory() == ""

    def test_read_rules_missing_file(self, hive_env):
        from keephive.storage import read_rules, rules_file

        rules_file().unlink()
        assert read_rules() == ""


# ---- backup_and_write ----


class TestBackupAndWrite:
    def test_creates_backup(self, hive_env):
        from keephive.storage import backup_and_write

        target = hive_env / "working" / "test.md"
        target.write_text("original")

        backup_and_write(target, "updated")

        assert target.read_text() == "updated"
        bak = target.with_suffix(".md.bak")
        assert bak.exists()
        assert bak.read_text() == "original"

    def test_writes_when_no_original(self, hive_env):
        from keephive.storage import backup_and_write

        target = hive_env / "working" / "new-file.md"
        backup_and_write(target, "brand new content")

        assert target.read_text() == "brand new content"
        assert not target.with_suffix(".md.bak").exists()

    def test_overwrites_existing_backup(self, hive_env):
        from keephive.storage import backup_and_write

        target = hive_env / "working" / "overwrite.md"
        target.write_text("v1")
        backup_and_write(target, "v2")
        backup_and_write(target, "v3")

        assert target.read_text() == "v3"
        assert target.with_suffix(".md.bak").read_text() == "v2"


# ---- append_to_daily ----


class TestAppendToDaily:
    def test_appends_line(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import append_to_daily

        path = append_to_daily("- [10:00:00] FACT: testing works")
        content = path.read_text()
        assert "FACT: testing works" in content

    def test_creates_file_if_missing(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-06-01")
        from keephive.storage import append_to_daily

        path = append_to_daily("- entry", day="2026-06-01")
        assert path.exists()
        assert "# Daily Log: 2026-06-01" in path.read_text()


# ---- collect_todos / open_todos / _dedup_todos ----


class TestCollectTodos:
    def test_collects_timestamped_todos(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import collect_todos

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] TODO: Refactor storage module\n"
            "- [11:00:00] FACT: something\n"
        )

        todos, dones = collect_todos()
        assert len(todos) == 1
        assert todos[0] == ("2026-01-15", "10:00", "Refactor storage module")
        assert len(dones) == 0

    def test_collects_bare_todos(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import collect_todos

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text("# Daily Log: 2026-01-15\n\n- TODO: Write documentation\n")

        todos, dones = collect_todos()
        assert len(todos) == 1
        assert todos[0] == ("2026-01-15", "", "Write documentation")

    def test_collects_done_entries(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import collect_todos

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] TODO: Add more tests\n"
            "- [12:00:00] DONE: Add more tests\n"
        )

        todos, dones = collect_todos()
        assert len(todos) == 1
        assert "add more tests" in dones

    def test_ignores_old_files_beyond_30_days(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-01")
        from keephive.storage import collect_todos

        # 31 days before 2026-03-01 = 2026-01-29
        old_daily = hive_env / "daily" / "2026-01-28.md"
        old_daily.write_text("# Daily\n\n- [10:00:00] TODO: Old task\n")

        recent = hive_env / "daily" / "2026-02-28.md"
        recent.write_text("# Daily\n\n- [10:00:00] TODO: Recent task\n")

        todos, _ = collect_todos()
        texts = [t[2] for t in todos]
        assert "Recent task" in texts
        assert "Old task" not in texts

    def test_empty_daily_dir(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import collect_todos

        # Remove all daily files
        for f in (hive_env / "daily").glob("*.md"):
            f.unlink()

        todos, dones = collect_todos()
        assert todos == []
        assert dones == set()


class TestOpenTodos:
    def test_filters_completed(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import open_todos

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] TODO: Build authentication system\n"
            "- [11:00:00] TODO: Deploy monitoring infrastructure\n"
            "- [12:00:00] DONE: Build authentication system\n"
        )

        result = open_todos()
        texts = [t[2] for t in result]
        assert "Deploy monitoring infrastructure" in texts
        assert "Build authentication system" not in texts


class TestDedupTodos:
    def test_exact_duplicates_kept_most_recent(self, hive_env):
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-01-10", "09:00", "Migrate database schema"),
            ("2026-01-15", "10:00", "Migrate database schema"),
        ]
        result = _dedup_todos(todos)
        assert len(result) == 1
        assert result[0][0] == "2026-01-15"

    def test_distinct_todos_survive(self, hive_env):
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-01-10", "09:00", "Refactor authentication middleware"),
            ("2026-01-15", "10:00", "Deploy monitoring infrastructure"),
        ]
        result = _dedup_todos(todos)
        assert len(result) == 2

    def test_fuzzy_dedup_threshold(self, hive_env):
        """Similar TODOs (>0.8 ratio) get deduped."""
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-01-10", "09:00", "update deployment configuration settings"),
            ("2026-01-15", "10:00", "update deployment configuration"),
        ]
        result = _dedup_todos(todos)
        # These should match fuzzy dedup (high similarity)
        assert len(result) == 1
        # Most recent kept
        assert result[0][0] == "2026-01-15"

    def test_empty_list(self, hive_env):
        from keephive.storage import _dedup_todos

        assert _dedup_todos([]) == []

    def test_bracketed_prefix_stripped_for_comparison(self, hive_env):
        from keephive.storage import _dedup_todos

        todos = [
            ("2026-01-10", "09:00", "[audit] Implement caching strategy"),
            ("2026-01-15", "10:00", "Implement caching strategy"),
        ]
        result = _dedup_todos(todos)
        # Normalized forms are the same, so should dedup
        assert len(result) == 1


# ---- undo_done ----


class TestUndoDone:
    def test_removes_most_recent_done(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import undo_done

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] DONE: Setup CI pipeline\n"
            "- [11:00:00] DONE: Write integration tests\n"
        )

        result = undo_done()
        assert result == "Write integration tests"
        content = daily.read_text()
        assert "Write integration tests" not in content
        assert "Setup CI pipeline" in content

    def test_undo_with_pattern(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import undo_done

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] DONE: Setup CI pipeline\n"
            "- [11:00:00] DONE: Write integration tests\n"
        )

        result = undo_done("CI")
        assert result == "Setup CI pipeline"

    def test_no_match_returns_none(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import undo_done

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text("# Daily Log: 2026-01-15\n\n- [10:00:00] FACT: nothing\n")

        assert undo_done() is None

    def test_no_daily_dir_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HIVE_HOME", str(tmp_path / "empty"))
        from keephive.storage import undo_done

        assert undo_done() is None

    def test_bare_done_entry(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import undo_done

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text("# Daily Log: 2026-01-15\n\n- DONE: bare done entry\n")

        result = undo_done()
        assert result == "bare done entry"


# ---- Recurring tasks ----


class TestParseFreq:
    def test_daily(self):
        from keephive.storage import parse_freq

        assert parse_freq("daily") == 1.0

    def test_weekly(self):
        from keephive.storage import parse_freq

        assert parse_freq("weekly") == 7.0

    def test_monthly(self):
        from keephive.storage import parse_freq

        assert parse_freq("monthly") == 30.0

    def test_days(self):
        from keephive.storage import parse_freq

        assert parse_freq("3d") == 3.0

    def test_hours(self):
        from keephive.storage import parse_freq

        assert parse_freq("12h") == pytest.approx(0.5)

    def test_invalid_raises(self):
        from keephive.storage import parse_freq

        with pytest.raises(ValueError, match="Invalid frequency"):
            parse_freq("bogus")


class TestIsValidFreq:
    def test_valid(self):
        from keephive.storage import is_valid_freq

        assert is_valid_freq("daily") is True
        assert is_valid_freq("3d") is True
        assert is_valid_freq("12h") is True

    def test_invalid(self):
        from keephive.storage import is_valid_freq

        assert is_valid_freq("nope") is False
        assert is_valid_freq("") is False


class TestMarkRecurringDone:
    def test_marks_day_based_task(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import mark_recurring_done, recurring_file

        rf = recurring_file()
        rf.write_text(
            "# Recurring Tasks\n\n"
            "- [daily] Review pull requests\n"
            "- [weekly] Update documentation\n\n"
            "## Last Completed\n"
        )

        result = mark_recurring_done("pull request")
        assert result is not None
        text, done_str = result
        assert text == "Review pull requests"
        assert done_str == "2026-01-15"

    def test_marks_hour_based_task(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import mark_recurring_done, recurring_file

        rf = recurring_file()
        rf.write_text("# Recurring Tasks\n\n- [12h] Check build status\n\n## Last Completed\n")

        result = mark_recurring_done("build status")
        assert result is not None
        text, done_str = result
        assert text == "Check build status"
        assert "T" in done_str  # datetime format for hour-based

    def test_updates_existing_entry(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-20")
        from keephive.storage import mark_recurring_done, recurring_file

        rf = recurring_file()
        rf.write_text(
            "# Recurring Tasks\n\n"
            "- [daily] Review pull requests\n\n"
            "## Last Completed\n"
            "- Review pull requests: 2026-01-15\n"
        )

        result = mark_recurring_done("pull request")
        assert result is not None
        # Verify the file was updated
        content = rf.read_text()
        assert "2026-01-20" in content
        assert "2026-01-15" not in content

    def test_no_match_returns_none(self, hive_env):
        from keephive.storage import mark_recurring_done, recurring_file

        rf = recurring_file()
        rf.write_text("# Recurring Tasks\n\n- [daily] Something else\n")

        assert mark_recurring_done("nonexistent task") is None

    def test_no_file_returns_none(self, hive_env):
        from keephive.storage import mark_recurring_done

        assert mark_recurring_done("anything") is None


class TestDueRecurring:
    def test_overdue_task(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-20")
        from keephive.storage import due_recurring, recurring_file

        rf = recurring_file()
        rf.write_text(
            "# Recurring Tasks\n\n"
            "- [daily] Review pull requests\n\n"
            "## Last Completed\n"
            "- Review pull requests: 2026-01-15\n"
        )

        due = due_recurring()
        assert len(due) >= 1
        freqs = [d[0] for d in due]
        assert "daily" in freqs

    def test_not_yet_due_excluded(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import due_recurring, recurring_file

        rf = recurring_file()
        rf.write_text(
            "# Recurring Tasks\n\n"
            "- [weekly] Weekly review\n\n"
            "## Last Completed\n"
            "- Weekly review: 2026-01-14\n"
        )

        due = due_recurring()
        texts = [d[1] for d in due]
        assert "Weekly review" not in texts

    def test_never_completed_is_due(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import due_recurring, recurring_file

        rf = recurring_file()
        rf.write_text("# Recurring Tasks\n\n- [daily] Brand new task\n\n## Last Completed\n")

        due = due_recurring()
        texts = [d[1] for d in due]
        assert "Brand new task" in texts

    def test_empty_file(self, hive_env):
        from keephive.storage import due_recurring, recurring_file

        recurring_file().write_text("")
        assert due_recurring() == []


# ---- FTS search ----


class TestFtsSearch:
    def test_returns_empty_on_no_data(self, hive_env):
        from keephive.storage import fts_search

        results = fts_search("nonexistent query")
        assert results == []

    def test_rebuild_and_search(self, hive_env):
        from keephive.storage import fts_search, rebuild_fts_index

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n- [10:00:00] FACT: SQLite supports FTS5 full-text search\n"
        )

        rebuild_fts_index()
        results = fts_search("SQLite FTS5")
        assert len(results) >= 1
        assert any("SQLite" in r["line"] for r in results)

    def test_search_indexes_notes(self, hive_env):
        from keephive.storage import fts_search, rebuild_fts_index, slot_file

        note = slot_file(1)
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Note 1\n\nUnique searchable content xylophone\n")

        rebuild_fts_index()
        results = fts_search("xylophone")
        assert len(results) >= 1
        assert any(r["tier"] == "notes" for r in results)

    def test_rebuild_creates_db(self, hive_env):
        from keephive.storage import fts_db_path, rebuild_fts_index

        rebuild_fts_index()
        assert fts_db_path().exists()

    def test_search_result_structure(self, hive_env):
        from keephive.storage import fts_search, rebuild_fts_index

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text("# Daily\n\n- Unique test entry for structure check\n")

        rebuild_fts_index()
        results = fts_search("structure check")
        if results:
            r = results[0]
            assert "tier" in r
            assert "line" in r
            assert "date" in r
            assert "score" in r


# ---- Stats tracking ----


class TestTrackEvent:
    def test_writes_stats_file(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_event

        track_event("commands", "remember")

        data = read_stats()
        assert "2026-01-15" in data["days"]
        assert data["days"]["2026-01-15"]["commands"]["remember"] == 1

    def test_increments_counter(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_event

        track_event("commands", "remember")
        track_event("commands", "remember")
        track_event("commands", "remember")

        data = read_stats()
        assert data["days"]["2026-01-15"]["commands"]["remember"] == 3

    def test_tracks_source(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_event

        track_event("commands", "remember", source="terminal")

        data = read_stats()
        assert data["days"]["2026-01-15"]["sources"]["terminal"] >= 1

    def test_tracks_hourly(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_event

        track_event("commands", "remember")

        data = read_stats()
        assert "hours" in data["days"]["2026-01-15"]
        assert sum(data["days"]["2026-01-15"]["hours"].values()) >= 1

    def test_tracks_project(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_event

        track_event("commands", "remember", project="/home/dev/myproject")

        data = read_stats()
        day_data = data["days"]["2026-01-15"]
        assert "projects" in day_data
        # Project should exist with command count
        assert any(p["commands"] >= 1 for p in day_data["projects"].values())

    def test_never_raises(self, hive_env, monkeypatch):
        """track_event swallows all exceptions."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import track_event

        # Make stats file unwritable by pointing to invalid path
        monkeypatch.setenv("HIVE_HOME", "/nonexistent/path/hive")
        track_event("commands", "remember")  # Should not raise


class TestReadStats:
    def test_missing_file(self, hive_env):
        from keephive.storage import read_stats

        data = read_stats()
        assert data == {"days": {}}

    def test_corrupt_file(self, hive_env):
        from keephive.storage import read_stats, stats_file

        stats_file().write_text("not json {{{")
        data = read_stats()
        assert data == {"days": {}}

    def test_missing_days_key(self, hive_env):
        from keephive.storage import read_stats, stats_file

        sf = stats_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text('{"version": 1}')

        data = read_stats()
        assert "days" in data


# ---- Session tracking ----


class TestTrackSessionEvent:
    def test_creates_session(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-001", "start", project="/home/dev/project")

        data = read_stats()
        sessions = data["days"]["2026-01-15"]["sessions"]
        assert "sess-001" in sessions
        assert sessions["sess-001"]["prompts"] == 0

    def test_prompt_event_increments(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-001", "start")
        track_session_event("sess-001", "prompt")
        track_session_event("sess-001", "prompt")

        data = read_stats()
        assert data["days"]["2026-01-15"]["sessions"]["sess-001"]["prompts"] == 2

    def test_tool_event_tracks_name(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-001", "start")
        track_session_event("sess-001", "tool", tool_name="Edit")
        track_session_event("sess-001", "tool", tool_name="Edit")
        track_session_event("sess-001", "tool", tool_name="Write")

        data = read_stats()
        tools = data["days"]["2026-01-15"]["sessions"]["sess-001"]["tools"]
        assert tools["Edit"] == 2
        assert tools["Write"] == 1

    def test_compact_event(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-001", "start")
        track_session_event("sess-001", "compact")

        data = read_stats()
        assert data["days"]["2026-01-15"]["sessions"]["sess-001"]["compacted"] is True

    def test_empty_session_id_ignored(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_stats, track_session_event

        track_session_event("", "start")

        data = read_stats()
        assert "sessions" not in data.get("days", {}).get("2026-01-15", {})


class TestSessionMetrics:
    def test_empty_sessions(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import session_metrics

        metrics = session_metrics()
        assert metrics["total_sessions"] == 0
        assert metrics["sessions_today"] == 0
        assert metrics["avg_prompts_per_session"] == 0.0

    def test_computes_from_data(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import session_metrics

        meta_dir = Path(os.environ["HIVE_CC_META_DIR"])
        _write_cc_session(
            meta_dir,
            "sess-001",
            user_message_count=3,
            start_time="2026-01-15T09:00:00Z",
            project_path="/dev/proj",
        )
        _write_cc_session(
            meta_dir,
            "sess-002",
            user_message_count=1,
            start_time="2026-01-15T14:00:00Z",
            project_path="/dev/proj",
        )

        metrics = session_metrics()
        assert metrics["total_sessions"] == 2
        assert metrics["sessions_today"] == 2
        assert metrics["avg_prompts_per_session"] == 2.0

    def test_daily_sessions_has_14_entries(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import session_metrics

        metrics = session_metrics()
        assert len(metrics["daily_sessions"]) == 14

    def test_compaction_rate_zero_for_cc(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import session_metrics

        # CC sessions don't have compacted flag; rate is always 0
        meta_dir = Path(os.environ["HIVE_CC_META_DIR"])
        _write_cc_session(meta_dir, "sess-001", start_time="2026-01-15T09:00:00Z")
        _write_cc_session(meta_dir, "sess-002", start_time="2026-01-15T14:00:00Z")

        metrics = session_metrics()
        assert metrics["compaction_rate"] == 0.0


# ---- Claude Code session-meta (read_cc_sessions) ----


def _write_cc_session(meta_dir, session_id, **fields):
    """Helper: write a fake session-meta JSON file."""
    import json

    data = {
        "session_id": session_id,
        "user_message_count": fields.get("user_message_count", 5),
        "tool_counts": fields.get("tool_counts", {"Read": 10, "Edit": 3, "Bash": 2}),
        "duration_minutes": fields.get("duration_minutes", 15),
        "start_time": fields.get("start_time", "2026-01-15T10:00:00Z"),
        "project_path": fields.get("project_path", "/home/user/my-project"),
        "lines_added": fields.get("lines_added", 50),
        "lines_removed": fields.get("lines_removed", 10),
        "files_modified": fields.get("files_modified", 3),
        "input_tokens": fields.get("input_tokens", 50000),
        "output_tokens": fields.get("output_tokens", 20000),
        "git_commits": fields.get("git_commits", 1),
    }
    (meta_dir / f"{session_id}.json").write_text(json.dumps(data))


class TestReadCcSessions:
    def test_empty_dir(self, hive_env, monkeypatch):
        """Returns empty list when session-meta dir has no files."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_cc_sessions

        result = read_cc_sessions(days_back=30)
        assert result == []

    def test_reads_session_files(self, hive_env, monkeypatch, tmp_path):
        """Reads and normalizes session-meta JSON files."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        meta_dir = tmp_path / "cc-meta"
        meta_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_META_DIR", str(meta_dir))

        _write_cc_session(meta_dir, "sess-001", start_time="2026-01-15T09:00:00Z")
        _write_cc_session(meta_dir, "sess-002", start_time="2026-01-14T14:00:00Z")

        from keephive.storage import read_cc_sessions

        sessions = read_cc_sessions(days_back=7)
        assert len(sessions) == 2
        # Check field normalization
        s = sessions[0]
        assert "user_messages" in s
        assert "tool_counts" in s
        assert "day" in s
        assert s["user_messages"] == 5
        assert "Read" in s["tool_counts"]

    def test_filters_by_date(self, hive_env, monkeypatch, tmp_path):
        """Sessions outside the date range are excluded."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        meta_dir = tmp_path / "cc-meta"
        meta_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_META_DIR", str(meta_dir))

        _write_cc_session(meta_dir, "recent", start_time="2026-01-14T10:00:00Z")
        _write_cc_session(meta_dir, "old", start_time="2025-12-01T10:00:00Z")

        from keephive.storage import read_cc_sessions

        sessions = read_cc_sessions(days_back=7)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "recent"

    def test_skips_ghost_sessions(self, hive_env, monkeypatch, tmp_path):
        """Sessions with 0 user messages are filtered out."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        meta_dir = tmp_path / "cc-meta"
        meta_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_META_DIR", str(meta_dir))

        _write_cc_session(meta_dir, "real", user_message_count=5)
        _write_cc_session(meta_dir, "ghost", user_message_count=0)

        from keephive.storage import read_cc_sessions

        sessions = read_cc_sessions(days_back=30)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "real"

    def test_project_path_normalized(self, hive_env, monkeypatch, tmp_path):
        """Project path gets ~ normalization for home directory."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        meta_dir = tmp_path / "cc-meta"
        meta_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_META_DIR", str(meta_dir))

        from pathlib import Path

        home = str(Path.home())
        _write_cc_session(meta_dir, "sess-home", project_path=f"{home}/my-project")

        from keephive.storage import read_cc_sessions

        sessions = read_cc_sessions(days_back=30)
        assert sessions[0]["project"] == "~/my-project"

    def test_day_field_derived(self, hive_env, monkeypatch, tmp_path):
        """day field is ISO date string from start_time."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        meta_dir = tmp_path / "cc-meta"
        meta_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_META_DIR", str(meta_dir))

        _write_cc_session(meta_dir, "sess-day", start_time="2026-01-15T14:30:00Z")

        from keephive.storage import read_cc_sessions

        sessions = read_cc_sessions(days_back=7)
        assert sessions[0]["day"] == "2026-01-15"

    def test_nonexistent_dir_returns_empty(self, hive_env, monkeypatch, tmp_path):
        """Returns empty list when meta dir doesn't exist."""
        monkeypatch.setenv("HIVE_CC_META_DIR", str(tmp_path / "nonexistent"))
        from keephive.storage import read_cc_sessions

        assert read_cc_sessions(days_back=30) == []


class TestSessionMetricsWithCcData:
    """session_metrics() prefers Claude Code data when available."""

    def test_uses_cc_data_when_available(self, hive_env, monkeypatch, tmp_path):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        meta_dir = tmp_path / "cc-meta"
        meta_dir.mkdir()
        monkeypatch.setenv("HIVE_CC_META_DIR", str(meta_dir))

        _write_cc_session(
            meta_dir,
            "cc-001",
            user_message_count=12,
            tool_counts={"Read": 20, "Edit": 5, "Bash": 3, "Grep": 8},
            start_time="2026-01-15T10:00:00Z",
            lines_added=100,
            lines_removed=30,
            files_modified=5,
            git_commits=2,
            input_tokens=80000,
            output_tokens=30000,
        )

        from keephive.storage import session_metrics

        m = session_metrics(days_back=7)
        assert m["source"] == "claude_code"
        assert m["total_sessions"] == 1
        assert m["avg_prompts_per_session"] == 12.0
        assert m["lines_added_week"] == 100
        assert m["git_commits_week"] == 2
        assert m["total_output_tokens"] == 30000

    def test_returns_zeros_when_no_cc_data(self, hive_env, monkeypatch):
        """When no CC session-meta exists, returns zeros (no fallback to hook data)."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import session_metrics, track_session_event

        # Write keephive hook data that should NOT be used
        track_session_event("kh-001", "start")
        track_session_event("kh-001", "prompt")

        m = session_metrics(days_back=7)
        assert m["source"] == "claude_code"
        assert m["total_sessions"] == 0


# ---- Live sessions ----


class TestLiveSessions:
    """Tests for read_live_sessions() that detects running Claude Code sessions."""

    def _write_jsonl(self, projects_dir, cwd, session_id, records):
        """Write a JSONL file under the projects dir for a given working directory."""
        import json

        encoded = cwd.replace("/", "-")
        proj_dir = projects_dir / encoded
        proj_dir.mkdir(parents=True, exist_ok=True)
        path = proj_dir / f"{session_id}.jsonl"
        lines = [json.dumps(r) for r in records]
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_empty_when_no_active_dirs(self, hive_env):
        """No active dirs returns empty list."""
        from keephive.storage import read_live_sessions

        result = read_live_sessions(active_dirs=[])
        assert result == []

    def test_reads_jsonl_session(self, hive_env, tmp_path, monkeypatch):
        """JSONL file with user messages produces a live session entry."""
        from keephive.storage import read_live_sessions

        projects_dir = tmp_path / "cc-projects"
        monkeypatch.setenv("HIVE_CC_PROJECTS_DIR", str(projects_dir))

        cwd = "/Users/test/myproject"
        sid = "abc-123-live"
        records = [
            {"type": "user", "timestamp": "2026-02-21T10:00:00.000Z", "sessionId": sid},
            {"type": "assistant", "timestamp": "2026-02-21T10:00:05.000Z", "sessionId": sid},
            {"type": "user", "timestamp": "2026-02-21T10:05:00.000Z", "sessionId": sid},
            {"type": "assistant", "timestamp": "2026-02-21T10:05:10.000Z", "sessionId": sid},
            {"type": "user", "timestamp": "2026-02-21T10:10:00.000Z", "sessionId": sid},
        ]
        self._write_jsonl(projects_dir, cwd, sid, records)

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=9999)
        assert len(result) == 1
        s = result[0]
        assert s["session_id"] == sid
        assert s["user_messages"] == 3
        assert s["is_live"] is True
        assert s["duration_minutes"] == 10
        assert s["project"] == cwd  # not normalized since home != /Users/test

    def test_path_encoding(self, hive_env, tmp_path, monkeypatch):
        """Working directory /Users/foo/bar encodes to -Users-foo-bar."""
        from keephive.storage import read_live_sessions

        projects_dir = tmp_path / "cc-projects"
        monkeypatch.setenv("HIVE_CC_PROJECTS_DIR", str(projects_dir))

        cwd = "/Users/foo/bar"
        self._write_jsonl(
            projects_dir,
            cwd,
            "enc-test",
            [{"type": "user", "timestamp": "2026-02-21T10:00:00Z", "sessionId": "enc-test"}],
        )

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=9999)
        assert len(result) == 1
        # Verify it found the file at the encoded path
        assert (projects_dir / "-Users-foo-bar" / "enc-test.jsonl").exists()

    def test_skips_stale_jsonl(self, hive_env, tmp_path, monkeypatch):
        """JSONL files older than recency_minutes are excluded."""
        import time

        from keephive.storage import read_live_sessions

        projects_dir = tmp_path / "cc-projects"
        monkeypatch.setenv("HIVE_CC_PROJECTS_DIR", str(projects_dir))

        cwd = "/Users/test/old"
        path = self._write_jsonl(
            projects_dir,
            cwd,
            "old-session",
            [{"type": "user", "timestamp": "2026-02-20T01:00:00Z", "sessionId": "old-session"}],
        )
        # Set mtime to 2 hours ago
        old_time = time.time() - 7200
        os.utime(path, (old_time, old_time))

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert result == []

    def test_skips_ghost_sessions(self, hive_env, tmp_path, monkeypatch):
        """JSONL with 0 user messages (only assistant/progress records) is excluded."""
        from keephive.storage import read_live_sessions

        projects_dir = tmp_path / "cc-projects"
        monkeypatch.setenv("HIVE_CC_PROJECTS_DIR", str(projects_dir))

        cwd = "/Users/test/ghost"
        self._write_jsonl(
            projects_dir,
            cwd,
            "ghost-session",
            [
                {"type": "progress", "timestamp": "2026-02-21T10:00:00Z"},
                {"type": "assistant", "timestamp": "2026-02-21T10:00:05Z"},
            ],
        )

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=9999)
        assert result == []

    def test_handles_corrupt_jsonl(self, hive_env, tmp_path, monkeypatch):
        """Corrupt JSONL produces empty list, no crash."""
        from keephive.storage import read_live_sessions

        projects_dir = tmp_path / "cc-projects"
        monkeypatch.setenv("HIVE_CC_PROJECTS_DIR", str(projects_dir))

        cwd = "/Users/test/corrupt"
        encoded = cwd.replace("/", "-")
        proj_dir = projects_dir / encoded
        proj_dir.mkdir(parents=True)
        (proj_dir / "bad-session.jsonl").write_text("not json at all\n{broken\n")

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=9999)
        assert result == []


# ---- Memory decay ----


class TestFactCategory:
    def test_fact_prefix(self):
        from keephive.storage import _fact_category

        assert _fact_category("FACT: Python is great") == "FACT"

    def test_decision_prefix(self):
        from keephive.storage import _fact_category

        assert _fact_category("DECISION: Use Pydantic") == "DECISION"

    def test_correction_prefix(self):
        from keephive.storage import _fact_category

        assert _fact_category("CORRECTION: old -> new") == "CORRECTION"

    def test_todo_prefix(self):
        from keephive.storage import _fact_category

        assert _fact_category("TODO: finish this") == "TODO"

    def test_insight_prefix(self):
        from keephive.storage import _fact_category

        assert _fact_category("INSIGHT: patterns emerge") == "INSIGHT"

    def test_default_is_fact(self):
        from keephive.storage import _fact_category

        assert _fact_category("some random text") == "FACT"

    def test_strips_leading_dash(self):
        from keephive.storage import _fact_category

        assert _fact_category("- DECISION: something") == "DECISION"

    def test_case_insensitive(self):
        from keephive.storage import _fact_category

        assert _fact_category("decision: lowercase") == "DECISION"


class TestStaleDaysForFact:
    def test_default_thresholds(self, hive_env, monkeypatch):
        monkeypatch.delenv("HIVE_STALE_DAYS", raising=False)
        from keephive.storage import stale_days_for_fact

        assert stale_days_for_fact("FACT: something") == 30
        assert stale_days_for_fact("DECISION: something") == 90
        assert stale_days_for_fact("CORRECTION: something") == 60
        assert stale_days_for_fact("INSIGHT: something") == 60
        assert stale_days_for_fact("TODO: something") == 7

    def test_env_override(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_STALE_DAYS", "14")
        from keephive.storage import stale_days_for_fact

        # Override applies to all categories
        assert stale_days_for_fact("DECISION: something") == 14
        assert stale_days_for_fact("FACT: something") == 14


class TestScoreFactDecay:
    def test_recent_fact_scores_higher(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import score_fact_decay

        recent = score_fact_decay("FACT: recent", "2026-01-14")
        old = score_fact_decay("FACT: old", "2025-06-01")
        assert recent > old

    def test_invalid_date_gives_zero_recency(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import score_fact_decay

        score = score_fact_decay("FACT: bad date", "not-a-date")
        # Only importance (0.2 * 1.0) + ref_score (0.2 * 0) + recall (0.2 * 0) = 0.2
        assert score == pytest.approx(0.2)

    def test_correction_has_higher_importance(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import score_fact_decay

        correction = score_fact_decay("CORRECTION: old -> new", "2026-01-14")
        fact = score_fact_decay("FACT: plain fact", "2026-01-14")
        # CORRECTION importance=1.5 vs FACT importance=1.0
        assert correction > fact

    def test_score_between_0_and_max(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import score_fact_decay

        score = score_fact_decay("FACT: something", "2026-01-15")
        # Max: recency=0.4 + ref=0.2 + importance=0.2*1.0 + recall=0.2 = 1.0
        # CORRECTION can push importance to 1.5 -> 0.4+0.2+0.3+0.2 = 1.1 max
        assert 0.0 <= score <= 1.2


class TestCountStaleFacts:
    def test_counts_stale_facts(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import count_stale_facts

        # memory.md from fixture has:
        # "Python is great [verified:2020-01-01]" => stale (6 years old)
        # "keephive uses Pydantic [verified:2026-02-15]" => fresh (6 days)
        # "Tests are important [verified:2026-02-14]" => fresh (7 days)
        count = count_stale_facts()
        assert count >= 1  # At least the 2020 entry

    def test_no_memory_file(self, hive_env):
        from keephive.storage import count_stale_facts, memory_file

        memory_file().unlink()
        assert count_stale_facts() == 0

    def test_all_fresh(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-02-15")
        from keephive.storage import count_stale_facts, memory_file

        memory_file().write_text("# Memory\n\n- FACT: recent [verified:2026-02-14]\n")
        assert count_stale_facts() == 0


class TestGetStaleFacts:
    def test_returns_tuples(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import get_stale_facts

        results = get_stale_facts()
        assert len(results) >= 1
        line_num, fact_text, raw_line = results[0]
        assert isinstance(line_num, int)
        assert isinstance(fact_text, str)
        assert isinstance(raw_line, str)

    def test_empty_memory(self, hive_env):
        from keephive.storage import get_stale_facts, memory_file

        memory_file().unlink()
        assert get_stale_facts() == []

    def test_respects_category_thresholds(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-01")
        monkeypatch.delenv("HIVE_STALE_DAYS", raising=False)
        from keephive.storage import get_stale_facts, memory_file

        # DECISION has 90-day threshold, FACT has 30-day threshold
        # 40 days ago = 2026-01-20
        memory_file().write_text(
            "# Memory\n\n"
            "- DECISION: Use Python [verified:2026-01-20]\n"
            "- FACT: Python is great [verified:2026-01-20]\n"
        )

        results = get_stale_facts()
        facts = [r[1] for r in results]
        # FACT at 40 days old > 30 threshold => stale
        assert any("Python is great" in f for f in facts)
        # DECISION at 40 days old < 90 threshold => not stale
        assert not any("Use Python" in f for f in facts)


# ---- Profile functions ----


class TestActiveProfile:
    def test_hive_home_bypasses_profiles(self, hive_env):
        from keephive.storage import active_profile

        # hive_env sets HIVE_HOME, so profiles are bypassed
        assert active_profile() is None

    def test_no_profile_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HIVE_HOME", raising=False)
        from keephive.storage import _claude_dir, active_profile

        # Ensure no profile file
        pf = _claude_dir() / ".hive-profile"
        if pf.exists():
            pf.unlink()

        assert active_profile() is None

    def test_empty_profile_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HIVE_HOME", raising=False)
        from keephive.storage import _claude_dir, active_profile

        pf = _claude_dir() / ".hive-profile"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("")

        result = active_profile()
        assert result is None


class TestSetActiveProfile:
    def test_set_and_read(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HIVE_HOME", raising=False)
        from keephive.storage import _claude_dir, set_active_profile

        pf = _claude_dir() / ".hive-profile"
        pf.parent.mkdir(parents=True, exist_ok=True)

        set_active_profile("work")
        assert pf.read_text() == "work"

    def test_clear_profile(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HIVE_HOME", raising=False)
        from keephive.storage import _claude_dir, set_active_profile

        pf = _claude_dir() / ".hive-profile"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text("work")

        set_active_profile(None)
        assert not pf.exists()


class TestProfileDir:
    def test_default_profile(self):
        from keephive.storage import profile_dir

        assert profile_dir("default") == Path.home() / ".claude" / "hive"

    def test_named_profile(self):
        from keephive.storage import profile_dir

        assert profile_dir("work") == Path.home() / ".claude" / "hive-work"


class TestListProfiles:
    def test_always_includes_default(self, hive_env, monkeypatch):
        from keephive.storage import list_profiles

        profiles = list_profiles()
        names = [p["name"] for p in profiles]
        assert "default" in names

    def test_finds_named_profiles(self, hive_env, monkeypatch):
        from keephive.storage import _claude_dir, list_profiles

        cd = _claude_dir()
        cd.mkdir(parents=True, exist_ok=True)
        (cd / "hive-testprofile-alpha").mkdir(exist_ok=True)
        (cd / "hive-testprofile-beta").mkdir(exist_ok=True)

        try:
            profiles = list_profiles()
            names = [p["name"] for p in profiles]
            assert "testprofile-alpha" in names
            assert "testprofile-beta" in names
        finally:
            # Clean up test directories
            (cd / "hive-testprofile-alpha").rmdir()
            (cd / "hive-testprofile-beta").rmdir()


# ---- Entry counting ----


class TestCountDailyEntries:
    def test_counts_timestamped_entries(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import count_daily_entries

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] FACT: entry one\n"
            "- [11:00:00] DECISION: entry two\n"
            "- [12:00:00] session started\n"  # noise, excluded
        )

        count = count_daily_entries("2026-01-15")
        assert count == 2  # session excluded

    def test_excludes_noise(self, hive_env):
        from keephive.storage import count_daily_entries

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily\n\n"
            "- [10:00:00] SESSION started\n"
            "- [10:01:00] COMPACTED from X\n"
            "- [10:02:00] FACT: real entry\n"
        )

        assert count_daily_entries("2026-01-15") == 1

    def test_missing_file(self, hive_env):
        from keephive.storage import count_daily_entries

        assert count_daily_entries("2099-01-01") == 0


class TestGetMeaningfulEntries:
    def test_extracts_categorized_entries(self, hive_env):
        from keephive.storage import get_meaningful_entries

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] FACT: Python is great\n"
            "- [10:05:00] session [keephive] /dev\n"
            "- [10:10:00] DECISION: Use Pydantic\n"
        )

        entries = get_meaningful_entries("2026-01-15")
        assert len(entries) == 2
        # Categorized entries get ~ prefix
        assert any("~" in e for e in entries)

    def test_respects_limit(self, hive_env):
        from keephive.storage import get_meaningful_entries

        daily = hive_env / "daily" / "2026-01-15.md"
        lines = ["# Daily\n"] + [f"- [10:{i:02d}:00] FACT: fact number {i}\n" for i in range(20)]
        daily.write_text("".join(lines))

        entries = get_meaningful_entries("2026-01-15", limit=5)
        assert len(entries) == 5

    def test_missing_file(self, hive_env):
        from keephive.storage import get_meaningful_entries

        assert get_meaningful_entries("2099-01-01") == []

    def test_truncates_long_entries(self, hive_env):
        from keephive.storage import get_meaningful_entries

        daily = hive_env / "daily" / "2026-01-15.md"
        long_text = "A" * 200
        daily.write_text(f"# Daily\n\n- [10:00:00] FACT: {long_text}\n")

        entries = get_meaningful_entries("2026-01-15")
        assert len(entries) == 1
        assert entries[0].endswith("...")
        assert len(entries[0]) <= 123  # 120 + "..."


class TestCountLogEntriesWithPrefix:
    def test_counts_matching_prefix(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import count_log_entries_with_prefix

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily\n\n"
            "- [10:00:00] FACT: one\n"
            "- [10:01:00] FACT: two\n"
            "- [10:02:00] DECISION: three\n"
        )

        assert count_log_entries_with_prefix("FACT:") == 2

    def test_no_daily_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HIVE_HOME", str(tmp_path / "empty"))
        from keephive.storage import count_log_entries_with_prefix

        assert count_log_entries_with_prefix("FACT:") == 0


class TestCountLogEntriesByPrefix:
    def test_groups_by_category(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import count_log_entries_by_prefix

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily\n\n"
            "- [10:00:00] FACT: one\n"
            "- [10:01:00] FACT: two\n"
            "- [10:02:00] DECISION: three\n"
            "- TODO: bare todo\n"
        )

        counts = count_log_entries_by_prefix(days_back=30)
        assert counts.get("FACT", 0) == 2
        assert counts.get("DECISION", 0) == 1
        assert counts.get("TODO", 0) == 1

    def test_empty_daily_dir(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import count_log_entries_by_prefix

        for f in (hive_env / "daily").glob("*.md"):
            f.unlink()

        assert count_log_entries_by_prefix() == {}

    def test_no_daily_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HIVE_HOME", str(tmp_path / "empty"))
        from keephive.storage import count_log_entries_by_prefix

        assert count_log_entries_by_prefix() == {}


class TestGetKeyEntriesPastDays:
    def test_excludes_today(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import get_key_entries_past_days

        today = hive_env / "daily" / "2026-01-15.md"
        today.write_text("# Daily\n\n- [10:00:00] FACT: today entry\n")

        yesterday = hive_env / "daily" / "2026-01-14.md"
        yesterday.write_text("# Daily\n\n- [10:00:00] FACT: yesterday entry\n")

        results = get_key_entries_past_days(days=7)
        texts = " ".join(r[1] for r in results)
        assert "yesterday" in texts
        assert "today" not in texts

    def test_excludes_todo_entries(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import get_key_entries_past_days

        daily = hive_env / "daily" / "2026-01-14.md"
        daily.write_text("# Daily\n\n- [10:00:00] FACT: keep this\n- [10:01:00] TODO: skip this\n")

        results = get_key_entries_past_days(days=7)
        texts = " ".join(r[1] for r in results)
        assert "keep this" in texts
        assert "skip this" not in texts

    def test_respects_limit(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import get_key_entries_past_days

        daily = hive_env / "daily" / "2026-01-14.md"
        lines = ["# Daily\n"] + [f"- [10:{i:02d}:00] FACT: fact {i}\n" for i in range(20)]
        daily.write_text("".join(lines))

        results = get_key_entries_past_days(days=7, limit=3)
        assert len(results) == 3


# ---- Recall tracking ----


class TestTrackRecallHit:
    def test_creates_file_and_increments(self, hive_env):
        from keephive.storage import get_recall_count, track_recall_hit

        track_recall_hit("FACT: Python is great")
        assert get_recall_count("FACT: Python is great") == 1

        track_recall_hit("FACT: Python is great")
        assert get_recall_count("FACT: Python is great") == 2

    def test_different_facts_tracked_separately(self, hive_env):
        from keephive.storage import get_recall_count, track_recall_hit

        track_recall_hit("FACT: alpha")
        track_recall_hit("FACT: beta")

        assert get_recall_count("FACT: alpha") == 1
        assert get_recall_count("FACT: beta") == 1


class TestGetRecallCount:
    def test_unknown_fact_returns_zero(self, hive_env):
        from keephive.storage import get_recall_count

        assert get_recall_count("FACT: never recalled") == 0

    def test_missing_file_returns_zero(self, hive_env):
        from keephive.storage import get_recall_count, recall_stats_file

        sf = recall_stats_file()
        if sf.exists():
            sf.unlink()
        assert get_recall_count("anything") == 0

    def test_corrupt_file_returns_zero(self, hive_env):
        from keephive.storage import get_recall_count, recall_stats_file

        recall_stats_file().write_text("not json")
        assert get_recall_count("anything") == 0


class TestTrackRecallMiss:
    def test_increments_miss_counter(self, hive_env):
        from keephive.storage import get_recall_hit_rate, track_recall_miss

        track_recall_miss()
        track_recall_miss()

        hits, total = get_recall_hit_rate()
        assert hits == 0
        assert total == 2


class TestTrackRecallHitMeta:
    def test_increments_hit_counter(self, hive_env):
        from keephive.storage import get_recall_hit_rate, track_recall_hit_meta

        track_recall_hit_meta()
        track_recall_hit_meta()
        track_recall_hit_meta()

        hits, total = get_recall_hit_rate()
        assert hits == 3
        assert total == 3


class TestGetRecallHitRate:
    def test_missing_file(self, hive_env):
        from keephive.storage import get_recall_hit_rate

        assert get_recall_hit_rate() == (0, 0)

    def test_combined_hits_and_misses(self, hive_env):
        from keephive.storage import (
            get_recall_hit_rate,
            track_recall_hit_meta,
            track_recall_miss,
        )

        track_recall_hit_meta()
        track_recall_hit_meta()
        track_recall_miss()

        hits, total = get_recall_hit_rate()
        assert hits == 2
        assert total == 3

    def test_corrupt_file(self, hive_env):
        from keephive.storage import get_recall_hit_rate, recall_stats_file

        recall_stats_file().write_text("bad json {{")
        assert get_recall_hit_rate() == (0, 0)


# ---- Evidence storage ----


class TestEvidenceStorage:
    def test_store_and_read(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import get_evidence_for_fact, store_evidence

        store_evidence("FACT: Python is great", "CONFIRMED", "found in docs")

        evidence = get_evidence_for_fact("FACT: Python is great")
        assert evidence is not None
        assert evidence["last_verdict"] == "CONFIRMED"
        assert evidence["verify_count"] == 1

    def test_no_evidence_returns_none(self, hive_env):
        from keephive.storage import get_evidence_for_fact

        assert get_evidence_for_fact("FACT: unknown") is None

    def test_correction_increments_count(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import get_evidence_for_fact, store_evidence

        store_evidence("FACT: old info", "STALE", "outdated", correction="new info")

        evidence = get_evidence_for_fact("FACT: old info")
        assert evidence["correction_count"] == 1

    def test_history_capped_at_5(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import get_evidence_for_fact, store_evidence

        for i in range(8):
            store_evidence("FACT: test", "CONFIRMED", f"reason {i}")

        evidence = get_evidence_for_fact("FACT: test")
        assert len(evidence["history"]) == 5
        assert evidence["verify_count"] == 8

    def test_extracts_source_locations(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import get_evidence_for_fact, store_evidence

        store_evidence("FACT: test", "CONFIRMED", "found in storage.py:42 and cli.py:10")

        evidence = get_evidence_for_fact("FACT: test")
        assert "source_locations" in evidence
        assert "storage.py:42" in evidence["source_locations"]


# ---- normalize_memory ----


class TestNormalizeMemory:
    def test_fixes_double_verified_tags(self, hive_env):
        from keephive.storage import memory_file, normalize_memory

        mf = memory_file()
        mf.write_text(
            "# Memory\n\n- FACT: double tag [verified:2026-01-01] [verified:2026-01-15]\n"
        )

        stats = normalize_memory(mf)
        assert stats["double_tags"] == 1
        content = mf.read_text()
        # Should have exactly one tag, the last date
        assert content.count("[verified:") == 1
        assert "[verified:2026-01-15]" in content

    def test_removes_resolved_todos(self, hive_env):
        from keephive.storage import memory_file, normalize_memory

        mf = memory_file()
        mf.write_text(
            "# Memory\n\n- TODO (RESOLVED): old task\n- FACT: keep this [verified:2026-01-15]\n"
        )

        stats = normalize_memory(mf)
        assert stats["resolved_todos"] == 1
        content = mf.read_text()
        assert "RESOLVED" not in content
        assert "keep this" in content

    def test_fixes_malformed_prefix(self, hive_env):
        from keephive.storage import memory_file, normalize_memory

        mf = memory_file()
        mf.write_text("# Memory\n\n- - FACT: double dash [verified:2026-01-15]\n")

        stats = normalize_memory(mf)
        assert stats["malformed_prefix"] == 1
        content = mf.read_text()
        assert "- - " not in content
        assert "- FACT: double dash" in content

    def test_deduplicates_lines(self, hive_env):
        from keephive.storage import memory_file, normalize_memory

        mf = memory_file()
        mf.write_text(
            "# Memory\n\n"
            "- FACT: duplicate line [verified:2026-01-15]\n"
            "- FACT: duplicate line [verified:2026-01-10]\n"
        )

        stats = normalize_memory(mf)
        assert stats["deduped"] == 1

    def test_missing_file(self, hive_env):
        from keephive.storage import normalize_memory

        result = normalize_memory(hive_env / "nonexistent.md")
        assert result == {
            "double_tags": 0,
            "resolved_todos": 0,
            "malformed_prefix": 0,
            "deduped": 0,
        }


# ---- strip_verified_tags ----


class TestStripVerifiedTags:
    def test_strips_single_tag(self):
        from keephive.storage import _strip_verified_tags

        result = _strip_verified_tags("FACT: test [verified:2026-01-15]")
        assert result == "FACT: test"

    def test_strips_multiple_tags(self):
        from keephive.storage import _strip_verified_tags

        result = _strip_verified_tags("FACT: test [verified:2026-01-01] [verified:2026-01-15]")
        assert result == "FACT: test"

    def test_no_tag_unchanged(self):
        from keephive.storage import _strip_verified_tags

        result = _strip_verified_tags("FACT: no tag here")
        assert result == "FACT: no tag here"


# ---- Note slots ----


class TestNoteSlots:
    def test_default_active_slot(self, hive_env):
        from keephive.storage import active_slot

        assert active_slot() == 1

    def test_set_and_read_active_slot(self, hive_env):
        from keephive.storage import active_slot, set_active_slot

        set_active_slot(5)
        assert active_slot() == 5

    def test_set_invalid_slot_raises(self, hive_env):
        from keephive.storage import set_active_slot

        with pytest.raises(ValueError):
            set_active_slot(0)

        with pytest.raises(ValueError):
            set_active_slot(11)


# ---- recent_dones ----


class TestRecentDones:
    def test_finds_done_entries(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import recent_dones

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily\n\n- [10:00:00] DONE: Completed task alpha\n- DONE: Completed task beta\n"
        )

        dones = recent_dones(days=3)
        texts = [d[1] for d in dones]
        assert "Completed task alpha" in texts
        assert "Completed task beta" in texts

    def test_respects_days_window(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import recent_dones

        old = hive_env / "daily" / "2026-01-10.md"
        old.write_text("# Daily\n\n- [10:00:00] DONE: Old task\n")

        recent = hive_env / "daily" / "2026-01-14.md"
        recent.write_text("# Daily\n\n- [10:00:00] DONE: Recent task\n")

        dones = recent_dones(days=3)
        texts = [d[1] for d in dones]
        assert "Recent task" in texts
        assert "Old task" not in texts


# ---- UI queue path ----


class TestUiQueuePath:
    def test_global_queue(self, hive_env):
        from keephive.storage import ui_queue_path

        path = ui_queue_path()
        assert path.name == ".ui-queue"

    def test_project_scoped_queue(self, hive_env):
        from keephive.storage import ui_queue_path

        path = ui_queue_path(project="myproject")
        assert path.name == ".ui-queue-myproject"


# ---- recent_daily_files ----


class TestRecentDailyFiles:
    def test_returns_most_recent_first(self, hive_env):
        from keephive.storage import recent_daily_files

        dd = hive_env / "daily"
        (dd / "2026-01-10.md").write_text("# Daily\n")
        (dd / "2026-01-11.md").write_text("# Daily\n")
        (dd / "2026-01-12.md").write_text("# Daily\n")

        files = recent_daily_files(days=3)
        assert len(files) == 3
        # Most recent first
        assert files[0].stem > files[-1].stem

    def test_respects_limit(self, hive_env):
        from keephive.storage import recent_daily_files

        dd = hive_env / "daily"
        for i in range(10):
            (dd / f"2026-01-{i + 1:02d}.md").write_text("# Daily\n")

        files = recent_daily_files(days=3)
        assert len(files) == 3


# ---- last_log_entry_with_prefix ----


class TestLastLogEntryWithPrefix:
    def test_finds_most_recent(self, hive_env):
        from keephive.storage import last_log_entry_with_prefix

        dd = hive_env / "daily"
        (dd / "2026-01-10.md").write_text("# Daily\n\n- [10:00:00] FACT: old entry\n")
        (dd / "2026-01-15.md").write_text("# Daily\n\n- [10:00:00] FACT: newest entry\n")

        result = last_log_entry_with_prefix("FACT:")
        assert "newest entry" in result

    def test_no_match(self, hive_env):
        from keephive.storage import last_log_entry_with_prefix

        assert last_log_entry_with_prefix("NONEXISTENT:") == ""

    def test_no_daily_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HIVE_HOME", str(tmp_path / "empty"))
        from keephive.storage import last_log_entry_with_prefix

        assert last_log_entry_with_prefix("FACT:") == ""


# ---- get_all_verified_facts ----


class TestGetAllVerifiedFacts:
    def test_returns_all_verified(self, hive_env):
        from keephive.storage import get_all_verified_facts

        results = get_all_verified_facts()
        # hive_env has 3 verified facts in memory.md
        assert len(results) == 3

    def test_empty_memory(self, hive_env):
        from keephive.storage import get_all_verified_facts, memory_file

        memory_file().unlink()
        assert get_all_verified_facts() == []

    def test_tuple_structure(self, hive_env):
        from keephive.storage import get_all_verified_facts

        results = get_all_verified_facts()
        for line_num, fact_text, raw_line in results:
            assert isinstance(line_num, int)
            assert line_num >= 1
            assert isinstance(fact_text, str)
            assert len(fact_text) > 0
            assert isinstance(raw_line, str)


# ---- _normalize_todo_text ----


class TestNormalizeTodoText:
    def test_strips_brackets(self):
        from keephive.storage import _normalize_todo_text

        assert _normalize_todo_text("[audit] Do something") == "do something"

    def test_strips_timestamps(self):
        from keephive.storage import _normalize_todo_text

        assert _normalize_todo_text("10:30 Do something") == "do something"

    def test_normalizes_whitespace(self):
        from keephive.storage import _normalize_todo_text

        assert _normalize_todo_text("  lots   of   spaces  ") == "lots of spaces"

    def test_strips_trailing_punctuation(self):
        from keephive.storage import _normalize_todo_text

        assert _normalize_todo_text("Do something!") == "do something"
        assert _normalize_todo_text("Do something.") == "do something"


# ---- stale_days ----


class TestStaleDays:
    def test_default(self, hive_env, monkeypatch):
        monkeypatch.delenv("HIVE_STALE_DAYS", raising=False)
        from keephive.storage import stale_days

        assert stale_days() == 30

    def test_env_override(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_STALE_DAYS", "14")
        from keephive.storage import stale_days

        assert stale_days() == 14


# ---- capture_budget ----


class TestCaptureBudget:
    def test_default(self, hive_env, monkeypatch):
        monkeypatch.delenv("HIVE_CAPTURE_BUDGET", raising=False)
        from keephive.storage import capture_budget

        assert capture_budget() == 4000

    def test_env_override(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_CAPTURE_BUDGET", "2000")
        from keephive.storage import capture_budget

        assert capture_budget() == 2000


# ---- all_recurring ----


class TestAllRecurring:
    def test_sorted_most_overdue_first(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-20")
        from keephive.storage import all_recurring, recurring_file

        rf = recurring_file()
        rf.write_text(
            "# Recurring Tasks\n\n"
            "- [daily] Daily check\n"
            "- [weekly] Weekly review\n\n"
            "## Last Completed\n"
            "- Daily check: 2026-01-10\n"
            "- Weekly review: 2026-01-18\n"
        )

        result = all_recurring()
        assert len(result) == 2
        # Most overdue first (daily check is 10 days overdue with 1-day interval = 9)
        assert result[0][2] >= result[1][2]

    def test_no_file(self, hive_env):
        from keephive.storage import all_recurring

        assert all_recurring() == []


# ---- read_sessions ----


class TestReadSessions:
    def test_reads_sessions(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_sessions, track_session_event

        track_session_event("sess-001", "start", project="/dev/proj")
        track_session_event("sess-002", "start", project="/dev/other")

        sessions = read_sessions()
        assert len(sessions) == 2

    def test_sorted_by_started(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import read_sessions, track_session_event

        track_session_event("aaa", "start")
        track_session_event("zzz", "start")

        sessions = read_sessions()
        assert sessions[0]["started"] <= sessions[1]["started"]

    def test_empty_stats(self, hive_env):
        from keephive.storage import read_sessions

        assert read_sessions() == []


# ---- count_log_entries_by_prefix_daily ----


class TestCountLogEntriesByPrefixDaily:
    def test_returns_correct_days(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import count_log_entries_by_prefix_daily

        result = count_log_entries_by_prefix_daily(days_back=7)
        assert len(result) == 7
        # Each entry is (date_str, count)
        for day_str, count in result:
            assert isinstance(day_str, str)
            assert isinstance(count, int)
            assert count >= 0


# ---- Edge-case tests: count_stale_facts ----


class TestCountStaleFactsEdgeCases:
    def test_malformed_verified_tag(self, hive_env, monkeypatch):
        """Entries with malformed [verified:] tags should not crash."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import count_stale_facts, memory_file

        memory_file().write_text(
            "# Memory\n\n"
            "- FACT: bad date [verified:not-a-date]\n"
            "- FACT: good entry [verified:2020-01-01]\n"
        )
        count = count_stale_facts()
        # The good entry is stale (6 years old), bad date should not crash
        assert count >= 1

    def test_missing_verified_tag_not_counted(self, hive_env, monkeypatch):
        """Facts without any [verified:] tag are not counted as stale."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import count_stale_facts, memory_file

        memory_file().write_text("# Memory\n\n- FACT: no verified tag at all\n")
        count = count_stale_facts()
        assert count == 0

    def test_mixed_valid_invalid_entries(self, hive_env, monkeypatch):
        """Mix of valid stale, valid fresh, and malformed entries."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import count_stale_facts, memory_file

        memory_file().write_text(
            "# Memory\n\n"
            "- FACT: ancient [verified:2020-01-01]\n"
            "- FACT: fresh [verified:2026-02-20]\n"
            "- FACT: broken [verified:]\n"
            "- Not a bullet line\n"
        )
        count = count_stale_facts()
        assert count == 1  # only "ancient" is stale


# ---- Edge-case tests: get_stale_facts ----


class TestGetStaleFactsEdgeCases:
    def test_partial_verified_date(self, hive_env, monkeypatch):
        """Partial date like [verified:2026-01] should not crash."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import get_stale_facts, memory_file

        memory_file().write_text("# Memory\n\n- FACT: partial date [verified:2026-01]\n")
        # Should not raise
        results = get_stale_facts()
        assert isinstance(results, list)

    def test_facts_parsed_regardless_of_dash_prefix(self, hive_env, monkeypatch):
        """Any line with [verified:date] is parsed; dash prefix is stripped if present."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import get_stale_facts, memory_file

        memory_file().write_text(
            "# Memory\n\n"
            "FACT: no dash prefix [verified:2020-01-01]\n"
            "- FACT: with dash [verified:2020-01-01]\n"
        )
        results = get_stale_facts()
        facts = [r[1] for r in results]
        # get_stale_facts uses regex on all lines; dash prefix is not required
        assert any("with dash" in f for f in facts)
        assert any("no dash prefix" in f for f in facts)

    def test_empty_verified_tag(self, hive_env, monkeypatch):
        """Empty verified tag [verified:] should not crash."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-21")
        from keephive.storage import get_stale_facts, memory_file

        memory_file().write_text("# Memory\n\n- FACT: empty tag [verified:]\n")
        results = get_stale_facts()
        assert isinstance(results, list)


# ---- Edge-case tests: get_recall_count ----


class TestGetRecallCountEdgeCases:
    def test_special_chars_in_fact_text(self, hive_env):
        """Fact text with quotes and special chars doesn't corrupt JSON."""
        from keephive.storage import get_recall_count, track_recall_hit

        fact = 'FACT: He said "hello" & <goodbye>'
        track_recall_hit(fact)
        assert get_recall_count(fact) == 1

    def test_empty_string_key(self, hive_env):
        """Empty string as fact text doesn't crash."""
        from keephive.storage import get_recall_count, track_recall_hit

        track_recall_hit("")
        assert get_recall_count("") == 1

    def test_very_long_fact_text(self, hive_env):
        """Very long fact text doesn't crash."""
        from keephive.storage import get_recall_count, track_recall_hit

        long_fact = "FACT: " + "x" * 10000
        track_recall_hit(long_fact)
        assert get_recall_count(long_fact) == 1


# ---- Edge-case tests: score_fact_decay ----


class TestScoreFactDecayEdgeCases:
    def test_exact_threshold_boundary(self, hive_env, monkeypatch):
        """Fact exactly at stale threshold gets recency=0.5 (half of 2*threshold)."""
        monkeypatch.setenv("HIVE_DATE", "2026-02-28")
        from keephive.storage import score_fact_decay

        # FACT threshold=30, today=2026-02-28, verified=2026-01-29 => 30 days ago
        score = score_fact_decay("FACT: at threshold", "2026-01-29")
        # recency = 1.0 - 30/(30*2) = 1.0 - 0.5 = 0.5
        # Total: 0.5*0.4 + 0*0.2 + 1.0*0.2 + 0*0.2 = 0.2 + 0.2 = 0.4
        assert score == pytest.approx(0.4)

    def test_future_verified_date(self, hive_env, monkeypatch):
        """Future verified date gives recency > 1.0 but score still bounded."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import score_fact_decay

        score = score_fact_decay("FACT: future", "2026-02-15")
        # days_old is negative, recency = max(0, 1.0 - (-31)/60) > 1.0
        # Score should still be reasonable
        assert 0.0 <= score <= 1.2

    def test_correction_max_score_within_bound(self, hive_env, monkeypatch):
        """CORRECTION (importance=1.5) at max recency stays within 1.2."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import score_fact_decay

        score = score_fact_decay("CORRECTION: recent fix", "2026-01-15")
        # recency=1.0 -> 0.4, refs=0 -> 0, importance=1.5 -> 0.3, recall=0 -> 0
        # Total: 0.4 + 0 + 0.3 + 0 = 0.7
        assert 0.0 <= score <= 1.2


# ---- Edge-case tests: undo_done ----


class TestUndoDoneEdgeCases:
    def test_pattern_matches_multiple_entries(self, hive_env, monkeypatch):
        """When pattern matches multiple DONE entries, the most recent is undone."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import undo_done

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] DONE: Fix authentication bug in login\n"
            "- [11:00:00] DONE: Fix authentication bug in signup\n"
        )

        result = undo_done("authentication")
        # Should undo the most recent match (last in file)
        assert result is not None
        assert "signup" in result

    def test_undo_preserves_non_done_entries(self, hive_env, monkeypatch):
        """Undoing a DONE entry preserves all non-DONE entries."""
        monkeypatch.setenv("HIVE_DATE", "2026-01-15")
        from keephive.storage import undo_done

        daily = hive_env / "daily" / "2026-01-15.md"
        daily.write_text(
            "# Daily Log: 2026-01-15\n\n"
            "- [10:00:00] FACT: important fact\n"
            "- [11:00:00] DONE: completed task\n"
            "- [12:00:00] TODO: pending task\n"
        )

        undo_done()
        content = daily.read_text()
        assert "important fact" in content
        assert "pending task" in content
        assert "completed task" not in content


# ---- Concurrent _write_stats (BUG-1 regression) ----


class TestConcurrentWriteStats:
    def test_no_corruption_under_concurrency(self, hive_env):
        """BUG-1 regression: concurrent _write_stats must not corrupt the file.

        Before the fix: open("w") truncates before lock, so two threads could
        both truncate and overwrite each other's data, producing corrupt JSON.
        After the fix: separate lock file + os.replace() ensures atomic writes.

        Note: track_event has an inherent read-modify-write race (read_stats
        then _write_stats are separate calls), so exact count equality is not
        guaranteed. What IS guaranteed is: no exceptions, valid JSON on disk,
        and count > 0.
        """
        import threading

        from keephive.clock import get_today
        from keephive.storage import read_stats, track_event

        errors: list[Exception] = []

        def worker() -> None:
            for _ in range(20):
                try:
                    track_event("commands", "concurrent_test", source="test")
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised exceptions: {errors}"

        day = get_today().isoformat()
        data = read_stats()
        assert isinstance(data, dict), "Stats file is not valid JSON dict"
        assert "days" in data, "Stats missing 'days' key"
        count = (
            data.get("days", {})
            .get(day, {})
            .get("commands", {})
            .get("concurrent_test", 0)
        )
        assert count > 0, "No increments recorded at all"

    def test_no_exceptions_concurrent(self, hive_env):
        """No thread raises; file is always valid JSON after concurrent writes."""
        import threading

        from keephive.storage import read_stats, track_event

        errors: list[Exception] = []

        def worker() -> None:
            for _ in range(10):
                try:
                    track_event("hooks", "concurrent_hook", source="test")
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        data = read_stats()
        assert isinstance(data, dict)
        assert "days" in data


# ---- backup_and_write (BUG-2 regression) ----


class TestBackupAndWriteRegression:
    def test_bak_created_with_old_content(self, hive_env):
        from keephive.storage import backup_and_write

        path = hive_env / "working" / "test-baw.md"
        path.write_text("original")
        backup_and_write(path, "new content")

        bak = path.with_suffix(".md.bak")
        assert bak.exists()
        assert bak.read_text() == "original"
        assert path.read_text() == "new content"

    def test_no_tmp_remains_after_write(self, hive_env):
        from keephive.storage import backup_and_write

        path = hive_env / "working" / "test-notmp.md"
        backup_and_write(path, "content here")
        assert not path.with_suffix(".md.tmp").exists()

    def test_second_write_bak_is_first_content(self, hive_env):
        from keephive.storage import backup_and_write

        path = hive_env / "working" / "test-bak2.md"
        backup_and_write(path, "first write")
        backup_and_write(path, "second write")

        bak = path.with_suffix(".md.bak")
        assert bak.read_text() == "first write"
        assert path.read_text() == "second write"

    def test_new_file_no_bak(self, hive_env):
        from keephive.storage import backup_and_write

        path = hive_env / "working" / "test-newfile.md"
        assert not path.exists()
        backup_and_write(path, "brand new")
        assert not path.with_suffix(".md.bak").exists()
        assert path.read_text() == "brand new"

    def test_backup_failure_raises(self, hive_env, monkeypatch):
        """BUG-2 regression: backup failure must raise OSError, not silently continue."""
        import shutil

        from keephive.storage import backup_and_write

        path = hive_env / "working" / "test-bakfail.md"
        path.write_text("original")

        def raise_oserror(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(shutil, "copy2", raise_oserror)

        with pytest.raises(OSError, match="Backup failed"):
            backup_and_write(path, "should not be written")

        assert path.read_text() == "original"

    def test_content_integrity(self, hive_env):
        from keephive.storage import backup_and_write

        path = hive_env / "working" / "test-integrity.md"
        content = "# Test\n\n" + "line\n" * 100
        backup_and_write(path, content)
        assert path.read_text() == content
