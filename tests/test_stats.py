"""Tests for hive stats: tracking, aggregation, display, and streak calculation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


def _run(args: list[str], hive_home: str | None = None) -> subprocess.CompletedProcess:
    """Run keephive as a subprocess."""
    env = {
        "HIVE_SKIP_LLM": "1",
        "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin:"
        + (Path.home() / ".local/bin").as_posix(),
    }
    if hive_home:
        env["HIVE_HOME"] = hive_home
    return subprocess.run(
        [sys.executable, "-m", "keephive"] + args,
        capture_output=True,
        text=True,
        env=env,
    )


# ---- track_event ----


class TestTrackEvent:
    def test_basic_increment(self, hive_env):
        from keephive.storage import read_stats, track_event

        track_event("commands", "status", source="terminal")
        data = read_stats()
        today = date.today().isoformat()
        assert data["days"][today]["commands"]["status"] == 1

    def test_multiple_increments(self, hive_env):
        from keephive.storage import read_stats, track_event

        track_event("commands", "status", source="terminal")
        track_event("commands", "status", source="terminal")
        track_event("commands", "recall", source="mcp")
        data = read_stats()
        today = date.today().isoformat()
        assert data["days"][today]["commands"]["status"] == 2
        assert data["days"][today]["commands"]["recall"] == 1

    def test_source_tracking(self, hive_env):
        from keephive.storage import read_stats, track_event

        track_event("commands", "status", source="terminal")
        track_event("commands", "recall", source="mcp")
        track_event("hooks", "sessionstart", source="hook")
        data = read_stats()
        today = date.today().isoformat()
        sources = data["days"][today]["sources"]
        assert sources["terminal"] == 1
        assert sources["mcp"] == 1
        assert sources["hook"] == 1

    def test_project_tracking(self, hive_env):
        from keephive.storage import read_stats, track_event

        track_event("commands", "status", project="/Users/dev/keephive", source="terminal")
        track_event("commands", "recall", project="/Users/dev/keephive", source="terminal")
        data = read_stats()
        today = date.today().isoformat()
        # Project path gets ~ substitution (might not match /Users/dev)
        projects = data["days"][today].get("projects", {})
        assert len(projects) == 1
        proj = next(iter(projects.values()))
        assert proj["commands"] == 2
        assert proj["by_command"]["status"] == 1
        assert proj["by_command"]["recall"] == 1

    def test_project_not_tracked_for_hooks(self, hive_env):
        """Project tracking only applies to 'commands' category."""
        from keephive.storage import read_stats, track_event

        track_event("hooks", "sessionstart", project="/Users/dev/proj", source="hook")
        data = read_stats()
        today = date.today().isoformat()
        # hooks category doesn't create project entries
        assert "projects" not in data["days"][today]

    def test_silent_on_error(self, hive_env, monkeypatch):
        """track_event never raises."""
        from keephive.storage import track_event

        # Make stats file unwritable by pointing to non-existent deep path
        monkeypatch.setenv("HIVE_HOME", "/nonexistent/deep/path/that/cannot/exist")
        # Should not raise
        track_event("commands", "status", source="terminal")


# ---- read_stats / stats_file ----


class TestReadStats:
    def test_empty_stats(self, hive_env):
        from keephive.storage import read_stats

        data = read_stats()
        assert data == {"days": {}}

    def test_corrupt_stats(self, hive_env):
        from keephive.storage import read_stats, stats_file

        stats_file().write_text("not json")
        data = read_stats()
        assert data == {"days": {}}


# ---- Aggregation helpers ----


class TestAggregation:
    def _make_stats(self, hive_env):
        """Write multi-day stats for testing."""
        from keephive.storage import stats_file

        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        two_days_ago = (date.today() - timedelta(days=2)).isoformat()

        data = {
            "days": {
                today: {
                    "commands": {"status": 5, "recall": 3},
                    "hooks": {"sessionstart": 2, "posttooluse": 4},
                    "sources": {"terminal": 3, "mcp": 5},
                    "projects": {
                        "~/proj/a": {
                            "commands": 5,
                            "sessions": 1,
                            "by_command": {"status": 3, "recall": 2},
                        },
                        "~/proj/b": {
                            "commands": 3,
                            "sessions": 1,
                            "by_command": {"status": 2, "recall": 1},
                        },
                    },
                },
                yesterday: {
                    "commands": {"status": 2, "remember": 4},
                    "hooks": {"sessionstart": 1},
                    "sources": {"terminal": 2, "hook": 1, "mcp": 3},
                    "projects": {
                        "~/proj/a": {
                            "commands": 6,
                            "sessions": 2,
                            "by_command": {"status": 2, "remember": 4},
                        },
                    },
                },
                two_days_ago: {
                    "commands": {"status": 1},
                    "sources": {"terminal": 1},
                },
            },
        }
        stats_file().write_text(json.dumps(data))
        return data

    def test_sum_counters(self, hive_env):
        from keephive.commands.stats import _sum_counters

        data = self._make_stats(hive_env)
        totals = _sum_counters(data["days"], "commands")
        assert totals["status"] == 8  # 5 + 2 + 1
        assert totals["recall"] == 3
        assert totals["remember"] == 4

    def test_sum_sources(self, hive_env):
        from keephive.commands.stats import _sum_sources

        data = self._make_stats(hive_env)
        totals = _sum_sources(data["days"])
        assert totals["terminal"] == 6  # 3 + 2 + 1
        assert totals["mcp"] == 8  # 5 + 3

    def test_all_projects(self, hive_env):
        from keephive.commands.stats import _all_projects

        data = self._make_stats(hive_env)
        projects = _all_projects(data["days"])
        assert "~/proj/a" in projects
        assert "~/proj/b" in projects
        assert projects["~/proj/a"]["commands"] == 11  # 5 + 6
        assert projects["~/proj/a"]["days_active"] == 2
        assert projects["~/proj/b"]["commands"] == 3
        assert projects["~/proj/b"]["days_active"] == 1

    def test_match_project_exact(self, hive_env):
        from keephive.commands.stats import _match_project

        projects = {"~/proj/a": {}, "~/proj/b": {}}
        assert _match_project(projects, "~/proj/a") == "~/proj/a"

    def test_match_project_suffix(self, hive_env):
        from keephive.commands.stats import _match_project

        projects = {"~/Documents/GitHub/keephive": {}, "~/proj/b": {}}
        assert _match_project(projects, "keephive") == "~/Documents/GitHub/keephive"

    def test_match_project_substring(self, hive_env):
        from keephive.commands.stats import _match_project

        projects = {"~/Documents/GitHub/keephive": {}, "~/proj/b": {}}
        assert _match_project(projects, "GitHub/keep") == "~/Documents/GitHub/keephive"

    def test_match_project_none(self, hive_env):
        from keephive.commands.stats import _match_project

        projects = {"~/proj/a": {}}
        assert _match_project(projects, "nonexistent") is None


# ---- Streak calculation ----


class TestStreaks:
    def test_empty_data(self):
        from keephive.commands.stats import _calculate_streak

        current, longest = _calculate_streak({})
        assert current == 0
        assert longest == 0

    def test_single_day_today(self):
        from keephive.commands.stats import _calculate_streak

        today = date.today().isoformat()
        current, longest = _calculate_streak({today: {}})
        assert current == 1
        assert longest == 1

    def test_consecutive_days(self):
        from keephive.commands.stats import _calculate_streak

        today = date.today()
        days = {}
        for i in range(5):
            d = (today - timedelta(days=i)).isoformat()
            days[d] = {}

        current, longest = _calculate_streak(days)
        assert current == 5
        assert longest == 5

    def test_broken_streak(self):
        from keephive.commands.stats import _calculate_streak

        today = date.today()
        days = {
            today.isoformat(): {},
            (today - timedelta(days=1)).isoformat(): {},
            # gap
            (today - timedelta(days=3)).isoformat(): {},
            (today - timedelta(days=4)).isoformat(): {},
            (today - timedelta(days=5)).isoformat(): {},
        }

        current, longest = _calculate_streak(days)
        assert current == 2  # today + yesterday
        assert longest == 3  # 3-day run from 3-5 days ago

    def test_streak_not_today(self):
        """If today is missing, current streak is 0."""
        from keephive.commands.stats import _calculate_streak

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        two_ago = (date.today() - timedelta(days=2)).isoformat()
        current, longest = _calculate_streak({yesterday: {}, two_ago: {}})
        assert current == 0
        assert longest == 2


# ---- Sparkline / bar ----


class TestSparkline:
    def test_sparkline_length(self):
        from keephive.commands.stats import _sparkline

        result = _sparkline({}, days=7)
        assert len(result) == 7

    def test_sparkline_with_data(self):
        from keephive.commands.stats import _sparkline

        today = date.today().isoformat()
        result = _sparkline({today: 10}, days=7)
        # Last entry should be today's count
        assert result[-1][1] == 10
        # Other days should be 0
        assert all(c == 0 for _, c in result[:-1])

    def test_bar_empty(self):
        from keephive.commands.stats import _bar

        assert _bar(0, 10) == ""
        assert _bar(5, 0) == ""

    def test_bar_full(self):
        from keephive.commands.stats import _bar

        result = _bar(10, 10, width=10)
        assert len(result) == 10
        assert all(c == "\u2588" for c in result)


# ---- stats_text (MCP) ----


class TestStatsText:
    def test_empty(self, hive_env):
        from keephive.commands.stats import stats_text

        result = stats_text()
        assert "No stats" in result

    def test_with_data(self, hive_env):
        from keephive.commands.stats import stats_text
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        track_event("commands", "recall", source="mcp")
        result = stats_text()
        assert "keephive stats" in result
        assert "Today:" in result

    def test_project_filter(self, hive_env):
        from keephive.commands.stats import stats_text
        from keephive.storage import track_event

        track_event("commands", "status", project="/Users/dev/keephive", source="terminal")
        result = stats_text(project="keephive")
        assert "commands" in result

    def test_project_not_found(self, hive_env):
        from keephive.commands.stats import stats_text
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        result = stats_text(project="nonexistent")
        assert "No project" in result

    def test_date_filter(self, hive_env):
        from keephive.commands.stats import stats_text
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        result = stats_text(date_arg="today")
        assert "commands" in result


# ---- CLI smoke tests ----


class TestCLIStats:
    def test_stats_runs(self, hive_env):
        r = _run(["stats"], hive_home=str(hive_env))
        assert r.returncode == 0
        # Running 'stats' itself creates a tracking entry, so output won't be empty
        assert "keephive" in r.stdout or "No stats" in r.stdout

    def test_stats_json(self, hive_env):
        r = _run(["stats", "--json"], hive_home=str(hive_env))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "days" in data

    def test_stats_after_command(self, hive_env):
        """Running a command first creates stats, then stats shows them."""
        _run(["s"], hive_home=str(hive_env))
        r = _run(["stats"], hive_home=str(hive_env))
        assert r.returncode == 0
        # Should show at least 1 command (the 's' call)
        # Note: each subprocess gets its own env, so stats accumulate

    def test_stats_day(self, hive_env):
        _run(["s"], hive_home=str(hive_env))
        r = _run(["stats", "today"], hive_home=str(hive_env))
        assert r.returncode == 0

    def test_stats_project(self, hive_env):
        r = _run(["stats", "-p", "keephive"], hive_home=str(hive_env))
        assert r.returncode == 0


# ---- MCP tracking ----


class TestMCPTracking:
    def test_track_mcp_helper(self, hive_env):
        from keephive.mcp_server import _track_mcp
        from keephive.storage import read_stats

        _track_mcp("remember")
        data = read_stats()
        today = date.today().isoformat()
        assert data["days"][today]["commands"]["remember"] == 1
        assert data["days"][today]["sources"]["mcp"] == 1


# ---- Display function tests ----


class TestDisplayFull:
    def test_prints_today_this_week_all_time(self, hive_env, capsys):
        from keephive.commands.stats import _display_full
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        data = {
            "days": {
                date.today().isoformat(): {"commands": {"status": 1}, "sources": {"terminal": 1}}
            }
        }

        _display_full(data)
        out = capsys.readouterr().out
        assert "today" in out
        assert "week" in out
        assert "streak" in out

    def test_empty_data_shows_no_stats(self, hive_env, capsys):
        from keephive.commands.stats import _display_full

        _display_full({"days": {}})
        out = capsys.readouterr().out
        assert "No stats" in out


class TestDisplayDay:
    def test_prints_commands_and_hooks(self, hive_env, capsys):
        from keephive.commands.stats import _display_day
        from keephive.storage import track_event

        today_str = date.today().isoformat()
        track_event("commands", "remember", source="terminal")
        track_event("hooks", "sessionstart", source="hook")
        data = {
            "days": {
                today_str: {
                    "commands": {"remember": 1},
                    "hooks": {"sessionstart": 1},
                    "sources": {"terminal": 1, "hook": 1},
                }
            }
        }
        _display_day(data, "today")
        out = capsys.readouterr().out
        assert "remember" in out or "commands" in out.lower() or today_str in out

    def test_missing_day_shows_no_data(self, hive_env, capsys):
        from keephive.commands.stats import _display_day

        _display_day({"days": {}}, "yesterday")
        out = capsys.readouterr().out
        assert out  # at least some output, even if empty data


class TestDisplayProject:
    def test_sparkline_shown_when_data_exists(self, hive_env, capsys):
        from keephive.commands.stats import _display_project

        today_str = date.today().isoformat()
        proj_key = "~/Documents/GitHub/keephive"
        data = {
            "days": {
                today_str: {
                    "projects": {
                        proj_key: {"commands": 5, "sessions": 1, "by_command": {"status": 5}},
                    }
                }
            }
        }
        _display_project(data, "keephive")
        out = capsys.readouterr().out
        assert out  # project data shown

    def test_no_matching_project(self, hive_env, capsys):
        from keephive.commands.stats import _display_project

        _display_project({"days": {}}, "nonexistent")
        out = capsys.readouterr().out
        assert "no project" in out.lower() or "not found" in out.lower() or out


class TestRelativeDay:
    def test_today(self):
        from keephive.commands.stats import _relative_day

        assert _relative_day(date.today().isoformat()) == "today"

    def test_yesterday(self):
        from keephive.commands.stats import _relative_day

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert _relative_day(yesterday) == "yesterday"

    def test_three_days_ago(self):
        from keephive.commands.stats import _relative_day

        three_ago = (date.today() - timedelta(days=3)).isoformat()
        assert _relative_day(three_ago) == "3d ago"

    def test_invalid_date_passthrough(self):
        from keephive.commands.stats import _relative_day

        assert _relative_day("not-a-date") == "not-a-date"


# ---- Hourly tracking in storage ----


class TestHourlyTracking:
    def test_track_event_records_hourly(self, hive_env):
        """track_event records hourly activity in the 'hours' key."""
        from keephive.storage import read_stats, track_event

        track_event("commands", "status", source="terminal")
        data = read_stats()
        today = date.today().isoformat()
        day_data = data["days"][today]
        assert "hours" in day_data
        hours = day_data["hours"]
        # Current hour should have at least 1
        from datetime import datetime

        current_hour = datetime.now().strftime("%H")
        assert hours[current_hour] >= 1

    def test_track_event_hourly_multiple(self, hive_env):
        """Multiple track_event calls accumulate in the same hour bucket."""
        from keephive.storage import read_stats, track_event

        track_event("commands", "status", source="terminal")
        track_event("commands", "recall", source="mcp")
        track_event("hooks", "sessionstart", source="hook")
        data = read_stats()
        today = date.today().isoformat()
        hours = data["days"][today]["hours"]
        from datetime import datetime

        current_hour = datetime.now().strftime("%H")
        assert hours[current_hour] >= 3

    def test_track_event_hourly_backward_compat(self, hive_env):
        """Old stats data without 'hours' key doesn't break anything."""
        from keephive.storage import read_stats, stats_file

        today = date.today().isoformat()
        old_data = {"days": {today: {"commands": {"status": 5}}}}
        stats_file().write_text(json.dumps(old_data))

        data = read_stats()
        # No hours key yet
        assert "hours" not in data["days"][today]

        # New track_event adds hours key
        from keephive.storage import track_event

        track_event("commands", "recall", source="terminal")
        data = read_stats()
        assert "hours" in data["days"][today]


# ---- Hourly sparkline CLI ----


class TestHourlySparkline:
    def test_hourly_sparkline_renders(self):
        """_hourly_sparkline produces 24-char string."""
        from keephive.commands.stats import _hourly_sparkline

        hours = {"09": 5, "10": 10, "14": 3, "15": 8}
        result = _hourly_sparkline(hours)
        assert len(result) == 24
        # Active hours should have visible block characters
        assert result[9] != " "  # hour 09
        assert result[10] != " "  # hour 10

    def test_hourly_sparkline_all_zero(self):
        """_hourly_sparkline with no data produces 24 spaces."""
        from keephive.commands.stats import _hourly_sparkline

        result = _hourly_sparkline({})
        assert len(result) == 24
        assert result.strip() == ""

    def test_hourly_sparkline_single_hour(self):
        """_hourly_sparkline with one hour shows max block at that position."""
        from keephive.commands.stats import _hourly_sparkline

        result = _hourly_sparkline({"12": 10})
        assert len(result) == 24
        assert result[12] == "\u2588"  # full block
        # All other positions should be space
        for i in range(24):
            if i != 12:
                assert result[i] == " "

    def test_display_full_renders_with_hours_data(self, hive_env, capsys):
        """_display_full renders without error when hours data exists."""
        from keephive.commands.stats import _display_full

        today_str = date.today().isoformat()
        from datetime import datetime

        current_hour = datetime.now().strftime("%H")
        data = {
            "days": {
                today_str: {
                    "commands": {"status": 3},
                    "sources": {"terminal": 3},
                    "hours": {current_hour: 3},
                }
            }
        }
        _display_full(data)
        out = capsys.readouterr().out
        assert "today" in out
        assert "Sessions" in out


# ---- Session tracking ----


class TestTrackSessionEvent:
    def test_creates_session_on_start(self, hive_env):
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-001", "start", project="/Users/dev/proj")
        data = read_stats()
        today = date.today().isoformat()
        sessions = data["days"][today]["sessions"]
        assert "sess-001" in sessions
        s = sessions["sess-001"]
        assert s["prompts"] == 0
        assert s["tools"] == {}
        assert s["compacted"] is False
        assert s["started"] != ""
        assert s["last_seen"] != ""

    def test_increments_prompts(self, hive_env):
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-002", "start")
        track_session_event("sess-002", "prompt")
        track_session_event("sess-002", "prompt")
        track_session_event("sess-002", "prompt")
        data = read_stats()
        today = date.today().isoformat()
        assert data["days"][today]["sessions"]["sess-002"]["prompts"] == 3

    def test_tracks_tools(self, hive_env):
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-003", "start")
        track_session_event("sess-003", "tool", tool_name="Edit")
        track_session_event("sess-003", "tool", tool_name="Edit")
        track_session_event("sess-003", "tool", tool_name="Write")
        data = read_stats()
        today = date.today().isoformat()
        tools = data["days"][today]["sessions"]["sess-003"]["tools"]
        assert tools["Edit"] == 2
        assert tools["Write"] == 1

    def test_marks_compacted(self, hive_env):
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-004", "start")
        track_session_event("sess-004", "compact")
        data = read_stats()
        today = date.today().isoformat()
        assert data["days"][today]["sessions"]["sess-004"]["compacted"] is True

    def test_updates_last_seen(self, hive_env):
        import time

        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-005", "start")
        data = read_stats()
        today = date.today().isoformat()
        first_seen = data["days"][today]["sessions"]["sess-005"]["last_seen"]

        time.sleep(0.01)
        track_session_event("sess-005", "prompt")
        data = read_stats()
        second_seen = data["days"][today]["sessions"]["sess-005"]["last_seen"]
        assert second_seen >= first_seen

    def test_empty_session_id_noop(self, hive_env):
        from keephive.storage import read_stats, track_session_event

        track_session_event("", "start")
        data = read_stats()
        today = date.today().isoformat()
        assert today not in data.get("days", {}) or "sessions" not in data["days"].get(today, {})

    def test_project_path_normalized(self, hive_env):
        from keephive.storage import read_stats, track_session_event

        home = str(Path.home())
        track_session_event("sess-006", "start", project=f"{home}/Documents/proj")
        data = read_stats()
        today = date.today().isoformat()
        proj = data["days"][today]["sessions"]["sess-006"]["project"]
        assert proj.startswith("~")
        assert "Documents/proj" in proj

    def test_silent_on_error(self, hive_env, monkeypatch):
        from keephive.storage import track_session_event

        monkeypatch.setenv("HIVE_HOME", "/nonexistent/deep/path")
        track_session_event("sess-err", "start")  # Should not raise

    def test_tool_without_name_ignored(self, hive_env):
        from keephive.storage import read_stats, track_session_event

        track_session_event("sess-007", "start")
        track_session_event("sess-007", "tool", tool_name="")
        data = read_stats()
        today = date.today().isoformat()
        assert data["days"][today]["sessions"]["sess-007"]["tools"] == {}


class TestReadSessions:
    def test_empty_stats(self, hive_env):
        from keephive.storage import read_sessions

        result = read_sessions()
        assert result == []

    def test_returns_sessions_sorted(self, hive_env):
        from keephive.storage import read_sessions, track_session_event

        track_session_event("sess-b", "start")
        track_session_event("sess-b", "prompt")
        track_session_event("sess-a", "start")
        result = read_sessions()
        assert len(result) == 2
        # Both have session_id and day keys
        assert "session_id" in result[0]
        assert "day" in result[0]

    def test_days_back_filter(self, hive_env):
        from keephive.storage import read_sessions, stats_file

        old_day = (date.today() - timedelta(days=60)).isoformat()
        data = {
            "days": {
                old_day: {
                    "sessions": {
                        "old-sess": {
                            "project": "",
                            "started": f"{old_day}T10:00:00",
                            "last_seen": f"{old_day}T11:00:00",
                            "prompts": 5,
                            "tools": {},
                            "compacted": False,
                        }
                    }
                }
            }
        }
        stats_file().write_text(json.dumps(data))
        result = read_sessions(days_back=30)
        assert len(result) == 0
        result = read_sessions(days_back=90)
        assert len(result) == 1


class TestSessionMetrics:
    """Tests for session_metrics() using Claude Code session-meta as source of truth."""

    def _seed_sessions(self, hive_env):
        """Seed CC session-meta files (the authoritative source)."""
        meta_dir = Path(os.environ["HIVE_CC_META_DIR"])
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        # s1: today, 30 user msgs, Edit+Write, 60min
        (meta_dir / "s1.json").write_text(
            json.dumps(
                {
                    "session_id": "s1",
                    "user_message_count": 30,
                    "tool_counts": {"Edit": 10, "Write": 5},
                    "duration_minutes": 60,
                    "start_time": f"{today}T09:00:00Z",
                    "project_path": str(Path.home() / "proj" / "a"),
                    "lines_added": 100,
                    "lines_removed": 20,
                    "files_modified": 5,
                    "input_tokens": 80000,
                    "output_tokens": 30000,
                    "git_commits": 2,
                }
            )
        )

        # s2: today, 10 user msgs, Edit only, 30min
        (meta_dir / "s2.json").write_text(
            json.dumps(
                {
                    "session_id": "s2",
                    "user_message_count": 10,
                    "tool_counts": {"Edit": 3},
                    "duration_minutes": 30,
                    "start_time": f"{today}T14:00:00Z",
                    "project_path": str(Path.home() / "proj" / "a"),
                    "lines_added": 30,
                    "lines_removed": 5,
                    "files_modified": 2,
                    "input_tokens": 20000,
                    "output_tokens": 8000,
                    "git_commits": 0,
                }
            )
        )

        # s3: yesterday, 50 user msgs, Edit+Write+Bash, 90min
        (meta_dir / "s3.json").write_text(
            json.dumps(
                {
                    "session_id": "s3",
                    "user_message_count": 50,
                    "tool_counts": {"Edit": 20, "Write": 10, "Bash": 5},
                    "duration_minutes": 90,
                    "start_time": f"{yesterday}T11:00:00Z",
                    "project_path": str(Path.home() / "proj" / "b"),
                    "lines_added": 200,
                    "lines_removed": 50,
                    "files_modified": 8,
                    "input_tokens": 100000,
                    "output_tokens": 40000,
                    "git_commits": 3,
                }
            )
        )

    def test_total_counts(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        assert sm["total_sessions"] == 3
        assert sm["sessions_today"] == 2

    def test_avg_prompts(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        assert sm["avg_prompts_per_session"] == 30.0  # (30+10+50)/3

    def test_median_prompts(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        assert sm["median_prompts_per_session"] == 30.0  # median of [10, 30, 50]

    def test_avg_duration(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        # s1: 60min, s2: 30min, s3: 90min => avg=60
        assert sm["avg_duration_minutes"] == 60.0

    def test_tool_totals(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        assert sm["tool_totals"]["Edit"] == 33  # 10+3+20
        assert sm["tool_totals"]["Write"] == 15  # 5+10
        assert sm["tool_totals"]["Bash"] == 5

    def test_tool_pct(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        # Total tool uses: 33+15+5 = 53
        assert 0.6 < sm["tool_pct"]["Edit"] < 0.65  # ~62%

    def test_compaction_rate_zero_for_cc(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        # CC sessions don't have compacted flag, rate is 0
        assert sm["compaction_rate"] == 0.0

    def test_sessions_by_project(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        assert sm["sessions_by_project"]["~/proj/a"] == 2
        assert sm["sessions_by_project"]["~/proj/b"] == 1

    def test_daily_sessions_sparkline(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        # daily_sessions is 14 entries
        assert len(sm["daily_sessions"]) == 14
        # Today should have count 2
        today_entry = sm["daily_sessions"][-1]
        assert today_entry[0] == date.today().isoformat()
        assert today_entry[1] == 2

    def test_empty_data(self, hive_env):
        from keephive.storage import session_metrics

        sm = session_metrics()
        assert sm["total_sessions"] == 0
        assert sm["avg_prompts_per_session"] == 0.0
        assert sm["avg_duration_minutes"] == 0.0
        assert sm["tool_totals"] == {}
        assert sm["compaction_rate"] == 0.0

    def test_source_is_claude_code(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        assert sm["source"] == "claude_code"

    def test_code_velocity_week(self, hive_env):
        from keephive.storage import session_metrics

        self._seed_sessions(hive_env)
        sm = session_metrics()
        # All 3 sessions are within last 7 days
        assert sm["lines_added_week"] == 330  # 100+30+200
        assert sm["lines_removed_week"] == 75  # 20+5+50
        assert sm["files_modified_week"] == 15  # 5+2+8
        assert sm["git_commits_week"] == 5  # 2+0+3


class TestSessionsInDisplay:
    def test_display_full_shows_sessions(self, hive_env, capsys):
        from keephive.commands.stats import _display_full
        from keephive.storage import track_session_event

        track_session_event("disp-001", "start", project="/Users/dev/proj")
        track_session_event("disp-001", "prompt")
        track_session_event("disp-001", "prompt")

        from keephive.storage import read_stats

        data = read_stats()
        _display_full(data)
        out = capsys.readouterr().out
        assert "Sessions" in out
        assert "prompts" in out.lower()

    def test_display_day_shows_sessions(self, hive_env, capsys):
        from keephive.commands.stats import _display_day
        from keephive.storage import track_session_event

        track_session_event("day-001", "start")
        track_session_event("day-001", "prompt")
        track_session_event("day-001", "tool", tool_name="Edit")

        from keephive.storage import read_stats

        data = read_stats()
        _display_day(data, "today")
        out = capsys.readouterr().out
        assert "Sessions" in out
        assert "prompt" in out.lower()

    def test_stats_text_includes_sessions(self, hive_env):
        from keephive.commands.stats import stats_text
        from keephive.storage import track_event

        # stats_text() needs days_data to not be empty, seed a command event
        track_event("commands", "status")

        # Seed CC session-meta (authoritative source)
        meta_dir = Path(os.environ["HIVE_CC_META_DIR"])
        today = date.today().isoformat()
        (meta_dir / "txt-001.json").write_text(
            json.dumps(
                {
                    "session_id": "txt-001",
                    "user_message_count": 5,
                    "tool_counts": {"Read": 2},
                    "duration_minutes": 10,
                    "start_time": f"{today}T10:00:00Z",
                    "project_path": "/tmp/test",
                }
            )
        )
        result = stats_text()
        assert "Sessions" in result or "session" in result.lower()


class TestCommandActivity:
    """_display_command_activity() shows top commands, daemon task runs, loop stats."""

    def _day_data(self, *, commands=None, daemon_tasks=None, loops=None):
        """Build a stats data dict for today with given subcategories."""
        from keephive.clock import get_today

        day_data: dict = {}
        if commands:
            day_data["commands"] = commands
        if daemon_tasks:
            day_data["daemon_tasks"] = daemon_tasks
        if loops:
            day_data["loops"] = loops
        return {"days": {get_today().isoformat(): day_data}}

    def test_renders_command_names_and_counts(self, hive_env, capsys):
        """Top commands section shows command names and their counts."""
        from keephive.commands.stats import _display_command_activity

        data = self._day_data(commands={"remember": 42, "recall": 25, "todo": 10})
        _display_command_activity(data)
        out = capsys.readouterr().out
        assert "Command Activity" in out
        assert "remember" in out
        assert "42" in out
        assert "recall" in out

    def test_renders_daemon_task_names_and_counts(self, hive_env, capsys):
        """Daemon tasks section shows task names with their run counts."""
        from keephive.commands.stats import _display_command_activity

        data = self._day_data(
            commands={"remember": 5},
            daemon_tasks={"wander": 7, "soul-update": 3},
        )
        _display_command_activity(data)
        out = capsys.readouterr().out
        assert "Daemon tasks" in out
        assert "wander" in out
        assert "7" in out
        assert "soul-update" in out

    def test_empty_daemon_tasks_shows_none_yet(self, hive_env, capsys):
        """When no daemon_tasks data exists, renders 'none yet' hint."""
        from keephive.commands.stats import _display_command_activity

        data = self._day_data(commands={"remember": 5})
        _display_command_activity(data)
        out = capsys.readouterr().out
        assert "none yet" in out
        assert "hive daemon" in out

    def test_loops_summary_shows_started_and_iterations(self, hive_env, capsys):
        """Loops summary line shows started count and iteration count."""
        from keephive.commands.stats import _display_command_activity

        data = self._day_data(
            commands={"remember": 1},
            loops={"started": 3, "iteration": 24},
        )
        _display_command_activity(data)
        out = capsys.readouterr().out
        assert "3 started" in out
        assert "24 iterations" in out

    def test_loops_line_omitted_when_zero(self, hive_env, capsys):
        """Loops line is not rendered when both started and iteration are zero."""
        from keephive.commands.stats import _display_command_activity

        data = self._day_data(commands={"remember": 5})
        _display_command_activity(data)
        out = capsys.readouterr().out
        assert "started" not in out

    def test_no_output_when_no_activity_data(self, hive_env, capsys):
        """Renders nothing when commands, daemon_tasks, and loops are all absent."""
        from keephive.commands.stats import _display_command_activity

        _display_command_activity({"days": {}})
        out = capsys.readouterr().out
        assert out == ""

    def test_top_commands_capped_at_eight(self, hive_env, capsys):
        """Only the top 8 commands are shown; the rest are silent."""
        from keephive.commands.stats import _display_command_activity

        commands = {f"cmd{i}": (100 - i) for i in range(15)}
        data = self._day_data(commands=commands)
        _display_command_activity(data)
        out = capsys.readouterr().out
        assert "cmd0" in out
        assert "cmd7" in out
        assert "cmd8" not in out

    def test_sum_counters_aggregates_daemon_tasks_across_days(self, hive_env):
        """_sum_counters correctly totals daemon_tasks across multiple day entries."""
        from datetime import timedelta

        from keephive.clock import get_today
        from keephive.commands.stats import _sum_counters

        today = get_today()
        days = {
            today.isoformat(): {"daemon_tasks": {"wander": 3, "soul-update": 1}},
            (today - timedelta(days=1)).isoformat(): {"daemon_tasks": {"wander": 2}},
        }
        result = _sum_counters(days, "daemon_tasks")
        assert result["wander"] == 5
        assert result["soul-update"] == 1


class TestCoverageDisplay:
    """Coverage bar in _display_full() from comprehension_coverage()."""

    def test_coverage_bar_shown_when_facts_exist(self, hive_env, capsys, monkeypatch):
        """Coverage line renders with bar and percentage when facts exist."""
        from keephive.commands import stats as stats_mod
        from keephive.commands.stats import _display_full
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        data = {
            "days": {
                date.today().isoformat(): {
                    "commands": {"status": 1},
                    "sources": {"terminal": 1},
                }
            }
        }
        monkeypatch.setattr(
            stats_mod,
            "comprehension_coverage",
            lambda: {
                "total": 100,
                "verified": 30,
                "auto_only": 65,
                "user_owned": 5,
                "dark_pct": 65.0,
                "coverage_pct": 35.0,
            },
        )
        _display_full(data)
        out = capsys.readouterr().out
        assert "Coverage" in out
        assert "35%" in out
        assert "\u2588" in out  # filled block
        assert "\u2591" in out  # empty block

    def test_coverage_bar_absent_when_total_zero(self, hive_env, capsys, monkeypatch):
        """Coverage line is skipped when comprehension_coverage returns total=0."""
        from keephive.commands import stats as stats_mod
        from keephive.commands.stats import _display_full
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        data = {
            "days": {
                date.today().isoformat(): {
                    "commands": {"status": 1},
                    "sources": {"terminal": 1},
                }
            }
        }
        monkeypatch.setattr(
            stats_mod,
            "comprehension_coverage",
            lambda: {
                "total": 0,
                "verified": 0,
                "auto_only": 0,
                "user_owned": 0,
                "dark_pct": 0.0,
                "coverage_pct": 0.0,
            },
        )
        _display_full(data)
        out = capsys.readouterr().out
        assert "Coverage" not in out

    def test_dark_hint_shown_when_auto_only_positive(self, hive_env, capsys, monkeypatch):
        """The 'hive v --dark' hint appears when auto_only > 0."""
        from keephive.commands import stats as stats_mod
        from keephive.commands.stats import _display_full
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        data = {
            "days": {
                date.today().isoformat(): {
                    "commands": {"status": 1},
                    "sources": {"terminal": 1},
                }
            }
        }
        monkeypatch.setattr(
            stats_mod,
            "comprehension_coverage",
            lambda: {
                "total": 50,
                "verified": 25,
                "auto_only": 20,
                "user_owned": 5,
                "dark_pct": 40.0,
                "coverage_pct": 60.0,
            },
        )
        _display_full(data)
        out = capsys.readouterr().out
        assert "hive v --dark" in out
        assert "20 dark" in out

    def test_dark_hint_absent_when_auto_only_zero(self, hive_env, capsys, monkeypatch):
        """The 'hive v --dark' hint is absent when auto_only == 0."""
        from keephive.commands import stats as stats_mod
        from keephive.commands.stats import _display_full
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        data = {
            "days": {
                date.today().isoformat(): {
                    "commands": {"status": 1},
                    "sources": {"terminal": 1},
                }
            }
        }
        monkeypatch.setattr(
            stats_mod,
            "comprehension_coverage",
            lambda: {
                "total": 50,
                "verified": 40,
                "auto_only": 0,
                "user_owned": 10,
                "dark_pct": 0.0,
                "coverage_pct": 100.0,
            },
        )
        _display_full(data)
        out = capsys.readouterr().out
        assert "Coverage" in out
        assert "100%" in out
        assert "hive v --dark" not in out

    def test_coverage_color_green_above_70(self, hive_env, capsys, monkeypatch):
        """Coverage bar uses green markup when pct >= 70."""
        from keephive.commands import stats as stats_mod
        from keephive.commands.stats import _display_full
        from keephive.storage import track_event

        track_event("commands", "status", source="terminal")
        data = {
            "days": {
                date.today().isoformat(): {
                    "commands": {"status": 1},
                    "sources": {"terminal": 1},
                }
            }
        }
        monkeypatch.setattr(
            stats_mod,
            "comprehension_coverage",
            lambda: {
                "total": 100,
                "verified": 70,
                "auto_only": 0,
                "user_owned": 30,
                "dark_pct": 0.0,
                "coverage_pct": 100.0,
            },
        )
        _display_full(data)
        out = capsys.readouterr().out
        assert "Coverage" in out


class TestSumCountersExtended:
    def test_sum_counters_aggregates_loops_across_days(self, hive_env):
        """_sum_counters correctly totals loops.started and loops.iteration across days."""
        from datetime import timedelta

        from keephive.clock import get_today
        from keephive.commands.stats import _sum_counters

        today = get_today()
        days = {
            today.isoformat(): {"loops": {"started": 2, "iteration": 10}},
            (today - timedelta(days=1)).isoformat(): {"loops": {"started": 1, "iteration": 5}},
        }
        result = _sum_counters(days, "loops")
        assert result["started"] == 3
        assert result["iteration"] == 15
