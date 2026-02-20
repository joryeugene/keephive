"""Tests for hive ps command."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from keephive.commands.ps import (
    _count_claude_processes,
    _git_info,
    _project_name,
    _today_cmd_count,
    _last_entry_age,
    _recent_projects,
)


class TestCountClaudeProcesses:
    def test_counts_claude_lines(self):
        fake_output = (
            "user 123 0.0 0.1 /usr/bin/python3\n"
            "user 456 0.0 0.2 claude -p --output-format json\n"
            "user 789 0.0 0.1 claude --model haiku\n"
            "user 999 0.0 0.0 grep claude\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
            count = _count_claude_processes()
        assert count == 2

    def test_excludes_grep_lines(self):
        fake_output = "user 999 0.0 0.0 grep claude something\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
            count = _count_claude_processes()
        assert count == 0

    def test_returns_zero_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            count = _count_claude_processes()
        assert count == 0

    def test_returns_zero_on_file_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            count = _count_claude_processes()
        assert count == 0


class TestGitInfo:
    def test_returns_branch_and_worktree_count(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="main\n"),  # branch
                MagicMock(returncode=0, stdout="path1\npath2\n"),  # worktree list
            ]
            result = _git_info(str(tmp_path))
        assert result == {"branch": "main", "worktrees": 2}

    def test_returns_none_outside_git_repo(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = _git_info(str(tmp_path))
        assert result is None

    def test_returns_none_on_empty_branch(self, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="\n")
            result = _git_info(str(tmp_path))
        assert result is None

    def test_returns_none_on_timeout(self, tmp_path):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = _git_info(str(tmp_path))
        assert result is None


class TestProjectName:
    def test_extracts_last_component(self):
        assert _project_name("/Users/jory/Documents/GitHub/keephive") == "keephive"

    def test_handles_trailing_slash(self):
        assert _project_name("/Users/jory/Projects/myapp/") == "myapp"

    def test_handles_short_path(self):
        assert _project_name("/keephive") == "keephive"


class TestTodayCmdCount:
    def test_returns_count_for_project(self):
        today = date.today().isoformat()
        stats = {
            "days": {
                today: {
                    "projects": {
                        "/my/project": {"commands": 5},
                    }
                }
            }
        }
        assert _today_cmd_count(stats, "/my/project") == 5

    def test_returns_zero_for_missing_project(self):
        stats = {"days": {}}
        assert _today_cmd_count(stats, "/my/project") == 0


class TestLastEntryAge:
    def test_returns_today_for_todays_activity(self):
        today = date.today().isoformat()
        stats = {
            "days": {
                today: {"projects": {"/proj": {"commands": 1}}}
            }
        }
        assert _last_entry_age(stats, "/proj") == "today"

    def test_returns_never_for_missing_project(self):
        stats = {"days": {}}
        assert _last_entry_age(stats, "/proj") == "never"

    def test_returns_days_ago(self):
        from datetime import timedelta
        two_days_ago = (date.today() - timedelta(days=2)).isoformat()
        stats = {
            "days": {
                two_days_ago: {"projects": {"/proj": {"commands": 1}}}
            }
        }
        assert _last_entry_age(stats, "/proj") == "2d ago"


class TestRecentProjects:
    def test_current_project_first(self):
        today = date.today().isoformat()
        cwd = "/current/project"
        stats = {
            "days": {
                today: {
                    "projects": {
                        cwd: {"commands": 3},
                        "/other/project": {"commands": 1},
                    }
                }
            }
        }
        result = _recent_projects(stats, cwd, days=7)
        assert len(result) == 2
        assert result[0]["is_current"] is True
        assert result[0]["key"] == cwd

    def test_excludes_projects_outside_window(self):
        from datetime import timedelta
        old_day = (date.today() - timedelta(days=10)).isoformat()
        cwd = "/current"
        stats = {
            "days": {
                old_day: {
                    "projects": {"/old/project": {"commands": 5}}
                }
            }
        }
        result = _recent_projects(stats, cwd, days=7)
        assert all(p["key"] != "/old/project" for p in result)

    def test_empty_stats_returns_empty(self):
        result = _recent_projects({"days": {}}, "/some/path", days=7)
        assert result == []
