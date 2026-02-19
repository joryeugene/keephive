"""Tests for recurring task system: parse_freq, due_recurring, mark_recurring_done."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest


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

    def test_2d(self):
        from keephive.storage import parse_freq
        assert parse_freq("2d") == 2.0

    def test_12h(self):
        from keephive.storage import parse_freq
        assert parse_freq("12h") == pytest.approx(0.5)

    def test_1h(self):
        from keephive.storage import parse_freq
        assert parse_freq("1h") == pytest.approx(1 / 24)

    def test_invalid_raises(self):
        from keephive.storage import parse_freq
        with pytest.raises(ValueError):
            parse_freq("biweekly")

    def test_invalid_number_only(self):
        from keephive.storage import parse_freq
        with pytest.raises(ValueError):
            parse_freq("7")

    def test_invalid_empty(self):
        from keephive.storage import parse_freq
        with pytest.raises(ValueError):
            parse_freq("")


class TestDueRecurring:
    def _make_recurring(self, hive_env: Path, content: str) -> Path:
        rf = hive_env / "working" / "recurring.md"
        rf.write_text(content)
        return rf

    def test_no_file_returns_empty(self, hive_env):
        from keephive.storage import due_recurring
        result = due_recurring()
        assert result == []

    def test_task_with_no_last_done_is_due(self, hive_env):
        from keephive.storage import due_recurring
        self._make_recurring(hive_env, "# Recurring\n\n- [daily] Check logs\n")
        result = due_recurring()
        assert len(result) == 1
        freq, text, overdue = result[0]
        assert freq == "daily"
        assert text == "Check logs"
        assert isinstance(overdue, int)

    def test_task_done_today_not_due(self, hive_env):
        from keephive.storage import due_recurring
        today_str = date.today().isoformat()
        content = (
            "# Recurring\n\n"
            "- [daily] Check logs\n\n"
            "## Last Completed\n\n"
            f"- Check logs: {today_str}\n"
        )
        self._make_recurring(hive_env, content)
        result = due_recurring()
        assert result == []

    def test_task_done_8_days_ago_weekly_overdue(self, hive_env):
        from keephive.storage import due_recurring
        done_date = (date.today() - timedelta(days=8)).isoformat()
        content = (
            "# Recurring\n\n"
            "- [weekly] Weekly review\n\n"
            "## Last Completed\n\n"
            f"- Weekly review: {done_date}\n"
        )
        self._make_recurring(hive_env, content)
        result = due_recurring()
        assert len(result) == 1
        freq, text, overdue = result[0]
        assert freq == "weekly"
        assert text == "Weekly review"
        assert overdue == 1  # 8 days elapsed - 7 day interval = 1 day overdue

    def test_task_done_5_days_ago_weekly_not_due(self, hive_env):
        from keephive.storage import due_recurring
        done_date = (date.today() - timedelta(days=5)).isoformat()
        content = (
            "# Recurring\n\n"
            "- [weekly] Weekly review\n\n"
            "## Last Completed\n\n"
            f"- Weekly review: {done_date}\n"
        )
        self._make_recurring(hive_env, content)
        result = due_recurring()
        assert result == []

    def test_multiple_tasks_mix(self, hive_env):
        from keephive.storage import due_recurring
        today_str = date.today().isoformat()
        old_date = (date.today() - timedelta(days=10)).isoformat()
        content = (
            "# Recurring\n\n"
            "- [daily] Check logs\n"
            "- [weekly] Weekly review\n\n"
            "## Last Completed\n\n"
            f"- Check logs: {today_str}\n"
            f"- Weekly review: {old_date}\n"
        )
        self._make_recurring(hive_env, content)
        result = due_recurring()
        # Only weekly is due
        assert len(result) == 1
        assert result[0][1] == "Weekly review"


class TestMarkRecurringDone:
    def _make_recurring(self, hive_env: Path, content: str) -> Path:
        rf = hive_env / "working" / "recurring.md"
        rf.write_text(content)
        return rf

    def test_mark_done_by_pattern(self, hive_env):
        from keephive.storage import mark_recurring_done
        self._make_recurring(hive_env, "# Recurring\n\n- [daily] Check logs\n")
        result = mark_recurring_done("Check logs")
        assert result is not None
        task_text, done_str = result
        assert task_text == "Check logs"
        assert done_str == date.today().isoformat()

    def test_mark_done_updates_last_completed(self, hive_env):
        from keephive.storage import mark_recurring_done, recurring_file
        old_date = (date.today() - timedelta(days=3)).isoformat()
        content = (
            "# Recurring\n\n"
            "- [daily] Check logs\n\n"
            "## Last Completed\n\n"
            f"- Check logs: {old_date}\n"
        )
        self._make_recurring(hive_env, content)
        mark_recurring_done("logs")
        updated = recurring_file().read_text()
        assert date.today().isoformat() in updated

    def test_mark_done_hour_task_uses_datetime(self, hive_env):
        from keephive.storage import mark_recurring_done
        self._make_recurring(hive_env, "# Recurring\n\n- [6h] Check metrics\n")
        result = mark_recurring_done("metrics")
        assert result is not None
        _, done_str = result
        assert "T" in done_str  # datetime format

    def test_mark_done_no_match_returns_none(self, hive_env):
        from keephive.storage import mark_recurring_done
        self._make_recurring(hive_env, "# Recurring\n\n- [daily] Check logs\n")
        result = mark_recurring_done("zzznomatch")
        assert result is None

    def test_mark_done_no_file_returns_none(self, hive_env):
        from keephive.storage import mark_recurring_done
        # Recurring file doesn't exist
        result = mark_recurring_done("anything")
        assert result is None

    def test_mark_done_adds_new_entry_when_missing(self, hive_env):
        from keephive.storage import mark_recurring_done, recurring_file
        self._make_recurring(
            hive_env,
            "# Recurring\n\n- [daily] Check logs\n\n## Last Completed\n\n",
        )
        mark_recurring_done("Check logs")
        content = recurring_file().read_text()
        assert "Check logs:" in content
        assert date.today().isoformat() in content
