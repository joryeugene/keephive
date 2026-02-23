"""Tests for the cognitive restructure metrics engine.

Tests _knowledge_health, _capture_mix, _session_productivity,
_weekly_trends, _most_recalled, _productivity_pulse, and
related storage functions (track_recall_miss, count_log_entries_by_prefix).
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from conftest import make_daily

# ---- Knowledge Health ----


class TestKnowledgeHealth:
    def test_empty_memory_returns_zeros(self, hive_env):
        (hive_env / "working" / "memory.md").write_text("# Working Memory\n")
        from keephive.commands.stats import _knowledge_health

        kh = _knowledge_health()
        assert kh["total_facts"] == 0
        assert kh["fresh"] == 0
        assert kh["aging"] == 0
        assert kh["stale"] == 0
        assert kh["fresh_pct"] == 0.0

    def test_all_fresh_facts(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact A [verified:{today}]\n- Fact B [verified:{today}]\n"
        )
        from keephive.commands.stats import _knowledge_health

        kh = _knowledge_health()
        assert kh["total_facts"] == 2
        assert kh["fresh"] == 2
        assert kh["aging"] == 0
        assert kh["stale"] == 0
        assert kh["fresh_pct"] == 100.0

    def test_stale_facts_bucketed(self, hive_env):
        (hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n- Ancient fact [verified:2020-01-01]\n"
        )
        from keephive.commands.stats import _knowledge_health

        kh = _knowledge_health()
        assert kh["total_facts"] == 1
        assert kh["stale"] == 1
        assert kh["stale_pct"] == 100.0

    def test_capture_recall_ratio(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact A [verified:{today}]\n- Fact B [verified:{today}]\n"
        )
        # Create recall stats: 1 of 2 facts recalled
        recall_file = hive_env / ".recall-stats.json"
        recall_file.write_text(json.dumps({"hash123": {"count": 3, "last": today}}))

        from keephive.commands.stats import _knowledge_health

        kh = _knowledge_health()
        assert kh["total_facts"] == 2
        assert kh["capture_recall_ratio"] == 50.0

    def test_fact_survival_rate(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact X [verified:{today}]\n"
        )
        evidence = {
            "fact_a": {
                "verify_count": 2,
                "correction_count": 0,
                "last_verdict": "VALID",
                "last_date": today,
                "history": [
                    {"verdict": "VALID", "date": today},
                    {"verdict": "VALID", "date": today},
                ],
            },
            "fact_b": {
                "verify_count": 1,
                "correction_count": 1,
                "last_verdict": "STALE",
                "last_date": today,
                "history": [{"verdict": "STALE", "date": today}],
            },
        }
        (hive_env / "working" / "evidence.json").write_text(json.dumps(evidence))

        from keephive.commands.stats import _knowledge_health

        kh = _knowledge_health()
        assert kh["fact_survival_rate"] == 50.0  # 1 of 2 first verdicts VALID

    def test_corrected_this_week(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact [verified:{today}]\n"
        )
        evidence = {
            "fact_x": {
                "verify_count": 1,
                "correction_count": 1,
                "last_verdict": "STALE",
                "last_date": today,
                "history": [{"verdict": "STALE", "date": today}],
            },
        }
        (hive_env / "working" / "evidence.json").write_text(json.dumps(evidence))

        from keephive.commands.stats import _knowledge_health

        kh = _knowledge_health()
        assert kh["corrected_this_week"] == 1


# ---- Capture Mix ----


class TestCaptureMix:
    def test_empty_logs_returns_zeros(self, hive_env):
        from keephive.commands.stats import _capture_mix

        cm = _capture_mix()
        assert cm["total"] == 0
        assert cm["counts"] == {}

    def test_counts_by_category(self, hive_env):
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] FACT: something",
                "- [10:01:00] FACT: another",
                "- [10:02:00] DECISION: chose X",
                "- [10:03:00] TODO: do something",
            ],
        )

        from keephive.commands.stats import _capture_mix

        cm = _capture_mix()
        assert cm["counts"]["FACT"] == 2
        assert cm["counts"]["DECISION"] == 1
        assert cm["counts"]["TODO"] == 1
        assert cm["total"] == 4

    def test_consistency_score(self, hive_env):
        # Create entries for all 14 days (consistency uses 14-day window)
        for i in range(14):
            make_daily(
                hive_env,
                i,
                [
                    "- [10:00:00] FACT: daily fact",
                    "- [10:01:00] TODO: daily task",
                ],
            )

        from keephive.commands.stats import _capture_mix

        cm = _capture_mix()
        # With consistent entries across full window, consistency should be high
        assert cm["consistency"] > 50

    def test_sparkline_present(self, hive_env):
        make_daily(hive_env, 0, ["- [10:00:00] FACT: today"])

        from keephive.commands.stats import _capture_mix

        cm = _capture_mix()
        assert "sparkline_str" in cm


# ---- Session Productivity ----


class TestSessionProductivity:
    def test_empty_sessions(self, hive_env):
        from keephive.commands.stats import _session_productivity

        sp = _session_productivity()
        assert sp["convos_today"] == 0
        assert sp["convos_week"] == 0
        assert sp["prompts_today"] == 0
        assert sp["prompts_week"] == 0
        assert sp["depth_shallow"] == 0
        assert sp["depth_medium"] == 0
        assert sp["depth_deep"] == 0

    def _write_cc(self, session_id, **fields):
        """Helper: write a CC session-meta file."""
        meta_dir = Path(os.environ["HIVE_CC_META_DIR"])
        today = date.today().isoformat()
        data = {
            "session_id": session_id,
            "user_message_count": fields.get("user_message_count", 5),
            "tool_counts": fields.get("tool_counts", {"Read": 2}),
            "duration_minutes": fields.get("duration_minutes", 10),
            "start_time": fields.get("start_time", f"{today}T10:00:00Z"),
            "project_path": fields.get("project_path", "/tmp/test"),
            "lines_added": fields.get("lines_added", 0),
            "lines_removed": fields.get("lines_removed", 0),
            "files_modified": fields.get("files_modified", 0),
            "input_tokens": fields.get("input_tokens", 0),
            "output_tokens": fields.get("output_tokens", 0),
            "git_commits": fields.get("git_commits", 0),
        }
        (meta_dir / f"{session_id}.json").write_text(json.dumps(data))

    def test_session_counts(self, hive_env):
        today = date.today().isoformat()
        self._write_cc("sp-001", user_message_count=2, start_time=f"{today}T09:00:00Z")
        self._write_cc("sp-002", user_message_count=1, start_time=f"{today}T14:00:00Z")

        from keephive.commands.stats import _session_productivity

        sp = _session_productivity()
        assert sp["convos_today"] == 2
        assert sp["prompts_today"] == 3
        assert sp["avg_prompts_per_convo"] == 1.5

    def test_depth_buckets_shallow(self, hive_env):
        # Shallow: <5 user messages
        today = date.today().isoformat()
        self._write_cc(
            "sh-001",
            user_message_count=3,
            tool_counts={"Read": 1},
            start_time=f"{today}T10:00:00Z",
        )

        from keephive.commands.stats import _session_productivity

        sp = _session_productivity()
        assert sp["depth_shallow"] == 1

    def test_tool_distribution(self, hive_env):
        today = date.today().isoformat()
        self._write_cc(
            "td-001",
            user_message_count=5,
            tool_counts={"Edit": 5, "Read": 3},
            start_time=f"{today}T10:00:00Z",
        )

        from keephive.commands.stats import _session_productivity

        sp = _session_productivity()
        tools = sp["tool_distribution"]
        # Should have Edit and Read in distribution
        tool_names = [t[0] for t in tools]
        assert "Edit" in tool_names


# ---- Weekly Trends ----


class TestWeeklyTrends:
    def test_empty_data_returns_zeroes(self, hive_env):
        from keephive.commands.stats import _weekly_trends

        trends = _weekly_trends({})
        # Always returns 6 metric rows, all with zero values
        assert len(trends["metrics"]) == 6
        assert all(m["this"] == 0 for m in trends["metrics"])

    def test_trends_with_data(self, hive_env):
        today = date.today()
        data = {
            "days": {
                today.isoformat(): {
                    "commands": {"status": 10, "remember": 5},
                    "sources": {"terminal": 15},
                },
                (today - timedelta(days=1)).isoformat(): {
                    "commands": {"status": 8},
                    "sources": {"terminal": 8},
                },
            }
        }

        from keephive.commands.stats import _weekly_trends

        trends = _weekly_trends(data)
        assert len(trends["metrics"]) > 0
        # First metric should be Prompts
        assert trends["metrics"][0]["label"] == "Prompts"


# ---- Most Recalled ----


class TestMostRecalled:
    def test_empty_recall_stats(self, hive_env):
        from keephive.commands.stats import _most_recalled

        result = _most_recalled()
        assert result == []

    def test_top_recalled_sorted(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n"
            f"- Auth uses JWT [verified:{today}]\n"
            f"- Tests are important [verified:{today}]\n"
        )

        import hashlib

        recall_data = {}
        for text, count in [
            ("- Auth uses JWT", 10),
            ("- Tests are important", 5),
        ]:
            key = hashlib.sha256(text.strip().encode()).hexdigest()[:16]
            recall_data[key] = {"count": count, "last": today}
        (hive_env / ".recall-stats.json").write_text(json.dumps(recall_data))

        from keephive.commands.stats import _most_recalled

        result = _most_recalled()
        assert len(result) >= 1
        # First should be the most recalled
        assert result[0]["count"] >= result[-1]["count"]


# ---- Productivity Pulse ----


class TestProductivityPulse:
    def test_zero_pulse_with_empty_data(self, hive_env):
        (hive_env / "working" / "memory.md").write_text("# Working Memory\n")

        from keephive.commands.stats import _productivity_pulse

        pulse = _productivity_pulse(data={})
        assert pulse["score"] >= 0
        assert pulse["score"] <= 100

    def test_pulse_score_components(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fresh fact [verified:{today}]\n"
        )
        from keephive.commands.stats import (
            _capture_mix,
            _knowledge_health,
            _productivity_pulse,
            _session_productivity,
        )

        health = _knowledge_health()
        capture = _capture_mix()
        sessions = _session_productivity()
        pulse = _productivity_pulse(health, capture, sessions, {})
        assert "score" in pulse
        assert "delta" in pulse
        assert "components" in pulse

    def test_pulse_bounded(self, hive_env):
        """Pulse score is always between 0 and 100."""
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact [verified:{today}]\n"
        )
        from keephive.commands.stats import _productivity_pulse

        pulse = _productivity_pulse(data={})
        assert 0 <= pulse["score"] <= 100


# ---- Storage: Recall Miss/Hit Tracking ----


class TestRecallTracking:
    def test_track_recall_miss(self, hive_env):
        from keephive.storage import get_recall_hit_rate, track_recall_miss

        track_recall_miss()
        track_recall_miss()
        hits, total = get_recall_hit_rate()
        assert hits == 0
        assert total == 2

    def test_track_recall_hit_meta(self, hive_env):
        from keephive.storage import get_recall_hit_rate, track_recall_hit_meta

        track_recall_hit_meta()
        track_recall_hit_meta()
        track_recall_hit_meta()
        hits, total = get_recall_hit_rate()
        assert hits == 3
        assert total == 3

    def test_mixed_hits_and_misses(self, hive_env):
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

    def test_no_recall_data(self, hive_env):
        from keephive.storage import get_recall_hit_rate

        hits, total = get_recall_hit_rate()
        assert hits == 0
        assert total == 0


# ---- Storage: count_log_entries_by_prefix ----


class TestCountLogEntriesByPrefix:
    def test_empty_logs(self, hive_env):
        from keephive.storage import count_log_entries_by_prefix

        result = count_log_entries_by_prefix(days_back=7)
        assert result == {}

    def test_counts_categories(self, hive_env):
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] FACT: something",
                "- [10:01:00] FACT: another",
                "- [10:02:00] DECISION: chose X",
                "- [10:03:00] INSIGHT: pattern found",
            ],
        )

        from keephive.storage import count_log_entries_by_prefix

        result = count_log_entries_by_prefix(days_back=0)
        assert result["FACT"] == 2
        assert result["DECISION"] == 1
        assert result["INSIGHT"] == 1

    def test_respects_days_back(self, hive_env):
        make_daily(hive_env, 0, ["- [10:00:00] FACT: today"])
        make_daily(hive_env, 10, ["- [10:00:00] FACT: old"])

        from keephive.storage import count_log_entries_by_prefix

        result = count_log_entries_by_prefix(days_back=3)
        assert result.get("FACT", 0) == 1  # Only today's


# ---- Help Restructure ----


class TestHelpRestructure:
    def test_default_help_has_sections(self, hive_env, capsys):
        _ = hive_env  # fixture for env isolation
        from keephive.cli import _help

        _help(show_all=False)
        out = capsys.readouterr().out
        assert "Capture" in out
        assert "Workflows" in out

    def test_help_all_shows_plumbing(self, hive_env, capsys):
        _ = hive_env  # fixture for env isolation
        from keephive.cli import _help

        _help(show_all=True)
        out = capsys.readouterr().out
        # --all should show commands like gc, doctor, setup
        assert "gc" in out or "doctor" in out or "setup" in out


# ---- Pending Rules Injection ----


class TestPendingRulesInjection:
    def test_sessionstart_no_pending_rules(self, hive_env):
        """SessionStart output has no pending rules hint when file absent."""
        from keephive.hooks.sessionstart import build_context

        context = build_context(str(hive_env), "test-project")
        assert "pending rule" not in context

    def test_sessionstart_with_pending_rules(self, hive_env):
        """SessionStart injects pending rules hint when file has content."""
        (hive_env / ".pending-rules.md").write_text(
            "- Always verify before committing\n- Use type hints\n"
        )
        from keephive.hooks.sessionstart import build_context

        context = build_context(str(hive_env), "test-project")
        assert "pending rule" in context
        assert "2" in context  # 2 suggestions


# ---- Command Telemetry: Todo completion rate ----


class TestTodoCompletionRate:
    def test_todo_shows_completion_rate(self, hive_env, capsys):
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        daily.write_text(
            f"# Daily Log: {today}\n\n"
            "- [10:00:00] TODO: task A\n"
            "- [10:01:00] DONE: task A\n"
            "- [10:02:00] TODO: task B\n"
        )

        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        assert "completion rate" in out or "done this week" in out


# ---- Command Telemetry: Log capture mix header ----


class TestLogCaptureMixHeader:
    def test_log_shows_category_counts(self, hive_env, capsys):
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        daily.write_text(
            f"# Daily Log: {today}\n\n"
            "- [10:00:00] FACT: something\n"
            "- [10:01:00] FACT: another\n"
            "- [10:02:00] DECISION: chose X\n"
        )

        from keephive.commands.log import cmd_log

        cmd_log([])
        out = capsys.readouterr().out
        assert "3 entries" in out
        assert "facts" in out


# ---- Command Telemetry: Remember category breakdown ----


class TestRememberCategoryBreakdown:
    def test_remember_shows_category_counts(self, hive_env, capsys):
        # Create existing entries for today
        today = date.today().isoformat()
        daily = hive_env / "daily" / f"{today}.md"
        daily.write_text(
            f"# Daily Log: {today}\n\n- [10:00:00] FACT: first\n- [10:01:00] TODO: task\n"
        )

        from keephive.commands.remember import cmd_remember

        cmd_remember(["FACT: new insight"])
        out = capsys.readouterr().out
        # Should show category breakdown instead of just "N entries today"
        assert "fact" in out.lower()


# ---- Serve Dashboard Panels ----


class TestServePanels:
    def test_pulse_panel_renders(self, hive_env):
        _ = hive_env  # fixture for env isolation
        from keephive.commands.serve import _get_pulse_data, _render_pulse_panel

        data = _get_pulse_data()
        html = _render_pulse_panel(data)
        assert "Productivity Pulse" in html
        assert "/100" in html

    def test_pipeline_panel_renders(self, hive_env):
        _ = hive_env  # fixture for env isolation
        from keephive.commands.serve import _get_pipeline_data, _render_pipeline_panel

        data = _get_pipeline_data()
        html = _render_pipeline_panel(data)
        assert "Pipeline Health" in html

    def test_capture_panel_renders(self, hive_env):
        _ = hive_env  # fixture for env isolation
        from keephive.commands.serve import _get_capture_data, _render_capture_panel

        data = _get_capture_data()
        html = _render_capture_panel(data)
        assert "Capture Signals" in html

    def test_recalled_panel_renders(self, hive_env):
        _ = hive_env  # fixture for env isolation
        from keephive.commands.serve import _get_recalled_data, _render_recalled_panel

        data = _get_recalled_data()
        html = _render_recalled_panel(data)
        assert "Most Recalled" in html

    def test_pipeline_panel_with_facts(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact [verified:{today}]\n"
        )
        from keephive.commands.serve import _get_pipeline_data, _render_pipeline_panel

        data = _get_pipeline_data()
        html = _render_pipeline_panel(data)
        assert "1 facts" in html
        assert "recall" in html

    def test_status_panel_includes_pulse(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact [verified:{today}]\n"
        )
        from keephive.commands.serve import _get_status_data, _render_status_panel

        data = _get_status_data()
        html = _render_status_panel(data)
        assert "Pulse" in html or "fresh" in html

    def test_status_brief_includes_pipeline(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact [verified:{today}]\n"
        )
        from keephive.commands.serve import _get_status_data, _render_status_brief_panel

        data = _get_status_data()
        html = _render_status_brief_panel(data)
        assert "fresh" in html or "recall" in html

    def test_knowledge_tabbed_includes_health(self, hive_env):
        today = date.today().isoformat()
        (hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- Fact [verified:{today}]\n"
        )
        from keephive.commands.serve import (
            _get_knowledge_all_data,
            _render_knowledge_tabbed_panel,
        )

        data = _get_knowledge_all_data()
        html = _render_knowledge_tabbed_panel(data)
        assert "Memory" in html
        # Should have fact count in tab badge
        assert "(1)" in html
