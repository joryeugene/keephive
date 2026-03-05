"""Tests for growth metrics: trend_metrics, growth_snapshot, CLI, and serve panels."""

from __future__ import annotations

import json
from datetime import date, timedelta

from keephive.storage import growth_snapshot, trend_metrics


class TestTrendMetrics:
    """trend_metrics returns per-day growth data from .stats.json and daily logs."""

    def test_empty_stats_returns_zeroed_days(self, hive_env):
        result = trend_metrics(days=7)
        assert len(result) == 7
        assert all(d["guide_hits"] == 0 for d in result)
        assert all(d["log_entries"] == 0 for d in result)

    def test_stats_json_guide_hits_counted(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        stats_path = hive_env / ".stats.json"
        stats_path.write_text(
            json.dumps(
                {
                    "days": {
                        "2026-03-03": {"guide_hits": {"agent-principles": 5, "testing": 3}},
                        "2026-03-04": {"guide_hits": {"agent-principles": 2}},
                    }
                }
            )
        )
        result = trend_metrics(days=3)
        # 3 days: 2026-03-02, 2026-03-03, 2026-03-04
        assert len(result) == 3
        assert result[0]["guide_hits"] == 0  # 03-02: no data
        assert result[1]["guide_hits"] == 8  # 03-03: 5+3
        assert result[2]["guide_hits"] == 2  # 03-04: 2

    def test_daemon_runs_aggregated(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        stats_path = hive_env / ".stats.json"
        stats_path.write_text(
            json.dumps(
                {
                    "days": {
                        "2026-03-04": {
                            "daemon_tasks": {"soul-update": 1, "stale-check": 1, "wander": 1}
                        }
                    }
                }
            )
        )
        result = trend_metrics(days=1)
        assert result[0]["daemon_runs"] == 3

    def test_daily_log_entries_counted(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        daily_dir = hive_env / "daily"
        log = daily_dir / "2026-03-04.md"
        log.write_text(
            "- [09:00:00] FACT: something learned\n"
            "- [09:01:00] TODO: do something\n"
            "- [09:02:00] DONE: finished task\n"
            "- [09:03:00] CORRECTION: old -> new\n"
            "- random non-entry line\n"
        )
        result = trend_metrics(days=1)
        assert result[0]["log_entries"] == 4
        assert result[0]["todos_done"] == 1
        assert result[0]["corrections"] == 1

    def test_commands_counted(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        stats_path = hive_env / ".stats.json"
        stats_path.write_text(
            json.dumps(
                {"days": {"2026-03-04": {"commands": {"remember": 10, "recall": 5, "verify": 2}}}}
            )
        )
        result = trend_metrics(days=1)
        assert result[0]["commands"] == 17


class TestGrowthSnapshot:
    """growth_snapshot returns aggregate state + trend data."""

    def test_default_state(self, hive_env):
        snap = growth_snapshot()
        # hive_env fixture seeds 3 facts in memory.md
        assert snap["fact_count"] == 3
        assert snap["guide_count"] == 0
        assert snap["recall_rate"] == 0.0
        assert len(snap["trend_30d"]) == 30

    def test_fact_count_from_memory(self, hive_env):
        mem = hive_env / "working" / "memory.md"
        mem.write_text(
            "# Memory\n"
            "- FACT: first thing [verified:2026-03-04]\n"
            "- FACT: second thing [verified:2026-03-04]\n"
            "- FACT: third old [verified:2026-01-01]\n"
        )
        snap = growth_snapshot()
        assert snap["fact_count"] == 3
        assert snap["fact_freshness"] > 0  # at least some are fresh

    def test_guide_count(self, hive_env):
        guides = hive_env / "knowledge" / "guides"
        (guides / "testing.md").write_text("# Testing\nContent")
        (guides / "patterns.md").write_text("# Patterns\nContent")
        snap = growth_snapshot()
        assert snap["guide_count"] == 2

    def test_week_totals_vs_prev(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        stats_path = hive_env / ".stats.json"
        days = {}
        # Previous week: lower activity
        for i in range(7, 14):
            d = (date(2026, 3, 4) - timedelta(days=i)).isoformat()
            days[d] = {"commands": {"status": 5}}
        # This week: higher activity
        for i in range(0, 7):
            d = (date(2026, 3, 4) - timedelta(days=i)).isoformat()
            days[d] = {"commands": {"status": 10}}
        stats_path.write_text(json.dumps({"days": days}))

        snap = growth_snapshot()
        assert snap["week_totals"]["commands"] == 70  # 7 * 10
        assert snap["prev_week_totals"]["commands"] == 35  # 7 * 5

    def test_fact_freshness_all_fresh(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        mem = hive_env / "working" / "memory.md"
        mem.write_text(
            "- FACT: recent [verified:2026-03-01]\n- FACT: also recent [verified:2026-03-03]\n"
        )
        snap = growth_snapshot()
        assert snap["fact_freshness"] == 100.0

    def test_fact_freshness_mixed(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        mem = hive_env / "working" / "memory.md"
        mem.write_text("- FACT: recent [verified:2026-03-01]\n- FACT: old [verified:2025-01-01]\n")
        snap = growth_snapshot()
        assert snap["fact_freshness"] == 50.0  # 1 of 2 fresh


class TestCmdGrowth:
    """CLI command produces output."""

    def test_empty_state_message(self, hive_env, capsys):
        from keephive.commands.growth import cmd_growth

        cmd_growth([])
        out = capsys.readouterr().out
        assert "Not enough data" in out

    def test_json_output(self, hive_env, capsys, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        # Seed some data so JSON has content
        daily = hive_env / "daily"
        (daily / "2026-03-04.md").write_text("- [09:00:00] FACT: something\n")

        from keephive.commands.growth import cmd_growth

        cmd_growth(["--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "fact_count" in data
        assert "trend_30d" in data
        assert len(data["trend_30d"]) == 30

    def test_with_data_shows_trends(self, hive_env, capsys, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        daily = hive_env / "daily"
        (daily / "2026-03-04.md").write_text(
            "- [09:00:00] FACT: something\n- [09:01:00] TODO: do this\n"
        )
        stats_path = hive_env / ".stats.json"
        stats_path.write_text(
            json.dumps(
                {"days": {"2026-03-04": {"guide_hits": {"testing": 3}, "commands": {"status": 5}}}}
            )
        )

        from keephive.commands.growth import cmd_growth

        cmd_growth([])
        out = capsys.readouterr().out
        assert "30-Day Trends" in out
        assert "Growth Story" in out


class TestGrowthPanels:
    """Serve.py growth panels render correctly."""

    def test_trajectory_panel_empty(self, hive_env):
        from keephive.commands.serve import _get_growth_data, _render_growth_trajectory_panel

        data = _get_growth_data()
        html = _render_growth_trajectory_panel(data)
        assert "Not enough data" in html

    def test_trajectory_panel_with_data(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        daily = hive_env / "daily"
        (daily / "2026-03-04.md").write_text("- [09:00:00] FACT: something\n")

        from keephive.commands.serve import _get_growth_data, _render_growth_trajectory_panel

        data = _get_growth_data()
        html = _render_growth_trajectory_panel(data)
        assert "Growth Trajectory" in html
        assert "Log entries" in html

    def test_state_panel_default(self, hive_env):
        from keephive.commands.serve import _get_growth_data, _render_growth_state_panel

        data = _get_growth_data()
        html = _render_growth_state_panel(data)
        # hive_env seeds 3 facts, so panel renders KPI cards
        assert "Knowledge State" in html
        assert "Facts" in html

    def test_state_panel_with_facts(self, hive_env):
        mem = hive_env / "working" / "memory.md"
        mem.write_text("- FACT: something [verified:2026-03-04]\n")

        from keephive.commands.serve import _get_growth_data, _render_growth_state_panel

        data = _get_growth_data()
        html = _render_growth_state_panel(data)
        assert "Knowledge State" in html
        assert "Facts" in html

    def test_delta_panel_no_activity(self, hive_env):
        from keephive.commands.serve import _get_growth_data, _render_growth_delta_panel

        data = _get_growth_data()
        html = _render_growth_delta_panel(data)
        assert "No activity" in html

    def test_delta_panel_with_activity(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        stats_path = hive_env / ".stats.json"
        stats_path.write_text(
            json.dumps(
                {
                    "days": {
                        "2026-03-04": {"commands": {"status": 10}},
                        "2026-02-25": {"commands": {"status": 5}},
                    }
                }
            )
        )

        from keephive.commands.serve import _get_growth_data, _render_growth_delta_panel

        data = _get_growth_data()
        html = _render_growth_delta_panel(data)
        assert "This Week vs Last" in html
        assert "Commands" in html
