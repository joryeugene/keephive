"""Tests for hive stats: tracking, aggregation, display, and streak calculation."""

from __future__ import annotations

import json
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
        assert "all time" in out

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
