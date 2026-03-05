"""Tests for Phase 4: closed-loop automation — daemon hints + effectiveness tracking."""

from __future__ import annotations

from keephive.storage import (
    append_improvement_history,
    daemon_hints_file,
    improvement_history_file,
    read_daemon_hints,
    read_improvement_history,
    write_daemon_hints,
)


class TestDaemonHintsStorage:
    def test_empty_hints(self, hive_env):
        assert read_daemon_hints() == {}

    def test_write_and_read(self, hive_env):
        hints = {
            "priority_boost": {"stale-check": 1.5, "soul-update": 2.0},
            "reason": "test reason",
            "expires": "2026-03-11",
        }
        write_daemon_hints(hints)
        result = read_daemon_hints()
        assert result["priority_boost"]["stale-check"] == 1.5
        assert result["reason"] == "test reason"

    def test_corrupt_json_returns_empty(self, hive_env):
        daemon_hints_file().write_text("{bad json")
        assert read_daemon_hints() == {}


class TestImprovementHistoryStorage:
    def test_empty_history(self, hive_env):
        assert read_improvement_history() == []

    def test_append_and_read(self, hive_env):
        append_improvement_history({"type": "skill", "name": "test-guide", "applied_at": "2026-03-04T12:00:00"})
        history = read_improvement_history()
        assert len(history) == 1
        assert history[0]["name"] == "test-guide"

    def test_rolling_cap(self, hive_env):
        for i in range(205):
            append_improvement_history({"type": "rule", "name": f"rule-{i}"})
        history = read_improvement_history()
        assert len(history) == 200
        assert history[0]["name"] == "rule-5"
        assert history[-1]["name"] == "rule-204"

    def test_corrupt_json_returns_empty(self, hive_env):
        improvement_history_file().write_text("not json")
        assert read_improvement_history() == []


class TestHasPriorityBoost:
    def test_no_hints_file(self, hive_env):
        from keephive.commands.daemon import _has_priority_boost

        assert _has_priority_boost("stale-check") is False

    def test_boost_present_and_active(self, hive_env, monkeypatch):
        from keephive.commands.daemon import _has_priority_boost

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        write_daemon_hints({
            "priority_boost": {"stale-check": 1.5},
            "expires": "2026-03-11",
        })
        assert _has_priority_boost("stale-check") is True
        assert _has_priority_boost("soul-update") is False

    def test_expired_hints_ignored(self, hive_env, monkeypatch):
        from keephive.commands.daemon import _has_priority_boost

        monkeypatch.setenv("HIVE_DATE", "2026-03-15")
        write_daemon_hints({
            "priority_boost": {"stale-check": 1.5},
            "expires": "2026-03-11",
        })
        assert _has_priority_boost("stale-check") is False

    def test_boost_of_one_not_a_boost(self, hive_env, monkeypatch):
        from keephive.commands.daemon import _has_priority_boost

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        write_daemon_hints({
            "priority_boost": {"stale-check": 1.0},
            "expires": "2026-03-11",
        })
        assert _has_priority_boost("stale-check") is False

    def test_no_expiry_field_still_works(self, hive_env):
        from keephive.commands.daemon import _has_priority_boost

        write_daemon_hints({"priority_boost": {"soul-update": 2.0}})
        assert _has_priority_boost("soul-update") is True


class TestIsTaskDueWithBoost:
    """Verify that priority boost overrides day-of-week constraints."""

    def test_wrong_day_without_boost_rejected(self, hive_env, monkeypatch):
        from datetime import datetime

        from keephive.commands.daemon import _is_task_due

        # Wednesday, but task is for Monday
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        now = datetime(2026, 3, 4, 10, 0, 0)  # Wednesday
        config = {"day": "monday", "time": "08:00"}
        state = {}
        assert _is_task_due("stale-check", config, state, now) is False

    def test_wrong_day_with_boost_accepted(self, hive_env, monkeypatch):
        from datetime import datetime

        from keephive.commands.daemon import _is_task_due

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        write_daemon_hints({
            "priority_boost": {"stale-check": 1.5},
            "expires": "2026-03-11",
        })
        now = datetime(2026, 3, 4, 10, 0, 0)  # Wednesday
        config = {"day": "monday", "time": "08:00"}
        state = {}
        assert _is_task_due("stale-check", config, state, now) is True

    def test_already_ran_today_not_boosted(self, hive_env, monkeypatch):
        from datetime import datetime

        from keephive.commands.daemon import _is_task_due

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        write_daemon_hints({
            "priority_boost": {"stale-check": 2.0},
            "expires": "2026-03-11",
        })
        now = datetime(2026, 3, 4, 10, 0, 0)
        config = {"day": "monday", "time": "08:00"}
        state = {"stale-check": {"last_run": "2026-03-04T09:00:00"}}
        assert _is_task_due("stale-check", config, state, now) is False


class TestWriteReflectHints:
    def test_writes_hints_file(self, hive_env, monkeypatch):
        from keephive.commands.daemon import _write_reflect_hints

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        _write_reflect_hints("testing", 12)
        hints = read_daemon_hints()
        assert hints["priority_boost"]["stale-check"] == 1.5
        assert hints["priority_boost"]["soul-update"] == 1.5
        assert hints["expires"] == "2026-03-11"
        assert "testing" in hints["reason"]


class TestRecordApplied:
    def test_records_skill_improvement(self, hive_env, monkeypatch):
        from keephive.commands.improve import _record_applied

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        _record_applied({"type": "skill", "name": "test-guide", "rationale": "Makes things better"})
        history = read_improvement_history()
        assert len(history) == 1
        assert history[0]["type"] == "skill"
        assert history[0]["name"] == "test-guide"
        assert "2026-03-04" in history[0]["applied_at"]

    def test_records_rule_improvement(self, hive_env, monkeypatch):
        from keephive.commands.improve import _record_applied

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        _record_applied({"type": "rule", "rule": "Always run tests first", "rationale": "Fewer bugs"})
        history = read_improvement_history()
        assert len(history) == 1
        assert history[0]["name"] == "Always run tests first"

    def test_truncates_long_fields(self, hive_env):
        from keephive.commands.improve import _record_applied

        _record_applied({"type": "skill", "name": "x" * 200, "rationale": "y" * 200})
        history = read_improvement_history()
        assert len(history[0]["name"]) == 80
        assert len(history[0]["rationale"]) == 120


class TestApplyImprovementRecordsHistory:
    """Integration test: _apply_improvement writes to improvement history."""

    def test_skill_install_records_history(self, hive_env):
        from keephive.commands.improve import _apply_improvement

        item = {"type": "skill", "name": "test-guide", "content": "# Test\n\nContent here.", "rationale": "helpful"}
        _apply_improvement(item)
        history = read_improvement_history()
        assert len(history) == 1
        assert history[0]["type"] == "skill"

    def test_rule_queue_records_history(self, hive_env):
        from keephive.commands.improve import _apply_improvement

        item = {"type": "rule", "rule": "Always verify", "rationale": "safety"}
        _apply_improvement(item)
        history = read_improvement_history()
        assert len(history) == 1
        assert history[0]["type"] == "rule"
