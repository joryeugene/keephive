"""Tests for hive ps command."""

from __future__ import annotations

import subprocess
from datetime import date
from unittest.mock import MagicMock, patch

from keephive.commands.ps import (
    _count_claude_processes,
    _get_active_session_dirs,
    _git_info,
    _last_entry_age,
    _project_name,
    _recent_projects,
    _same_path,
    _today_cmd_count,
)


class TestCountClaudeProcesses:
    def test_counts_claude_lines(self):
        fake_output = (
            "user 123 0.0 0.1 /usr/bin/python3\n"
            "user 456 0.0 0.2 claude -p --output-format json\n"  # excluded: has -p flag
            "user 789 0.0 0.1 claude --model haiku\n"  # counted: interactive
            "user 999 0.0 0.0 grep claude\n"  # excluded: grep
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
            count = _count_claude_processes()
        assert count == 1

    def test_excludes_grep_lines(self):
        fake_output = "user 999 0.0 0.0 grep claude something\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
            count = _count_claude_processes()
        assert count == 0

    def test_excludes_p_flag(self):
        fake_output = "user 456 0.0 0.2 claude -p --output-format json\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
            count = _count_claude_processes()
        assert count == 0

    def test_excludes_electron_helpers(self):
        fake_output = (
            "user 100 0.0 0.1 /Applications/Claude.app/Contents/MacOS/Claude Helper\n"
            "user 101 0.0 0.1 /Applications/Claude.app/Contents/MacOS/Claude Helper (Renderer)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=fake_output, returncode=0)
            count = _count_claude_processes()
        assert count == 0

    def test_returns_zero_on_timeout(self):
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
        stats = {"days": {today: {"projects": {"/proj": {"commands": 1}}}}}
        assert _last_entry_age(stats, "/proj") == "today"

    def test_returns_never_for_missing_project(self):
        stats = {"days": {}}
        assert _last_entry_age(stats, "/proj") == "never"

    def test_returns_days_ago(self):
        from datetime import timedelta

        two_days_ago = (date.today() - timedelta(days=2)).isoformat()
        stats = {"days": {two_days_ago: {"projects": {"/proj": {"commands": 1}}}}}
        assert _last_entry_age(stats, "/proj") == "2d ago"


class TestSamePath:
    def test_same_path_identical(self, tmp_path):
        assert _same_path(str(tmp_path), str(tmp_path)) is True

    def test_same_path_nonexistent_returns_false(self):
        assert _same_path("/nonexistent/path/xyz123", "/another/nonexistent") is False

    def test_same_path_tilde_expansion(self):
        import os

        home = os.path.expanduser("~")
        assert _same_path("~", home) is True


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
        result = _recent_projects(stats, cwd)
        assert len(result) == 2
        assert result[0]["is_current"] is True
        assert result[0]["key"] == cwd

    def test_includes_old_projects(self):
        from datetime import timedelta

        old_day = (date.today() - timedelta(days=30)).isoformat()
        cwd = "/current"
        stats = {"days": {old_day: {"projects": {"/old/project": {"commands": 5}}}}}
        result = _recent_projects(stats, cwd)
        assert any(p["key"] == "/old/project" for p in result)

    def test_empty_stats_returns_empty(self):
        result = _recent_projects({"days": {}}, "/some/path")
        assert result == []


class TestGetActiveSessionDirs:
    def test_returns_cwd_for_interactive_sessions(self, tmp_path):
        fake_ps = "user 123 0.0 0.1 /usr/bin/python3\nuser 456 0.0 0.1 claude --model sonnet\n"
        fake_lsof = f"p456\nn{tmp_path}\n"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=fake_ps, returncode=0),  # ps aux
                MagicMock(stdout=fake_lsof, returncode=0),  # lsof
            ]
            dirs = _get_active_session_dirs()
        assert str(tmp_path) in dirs

    def test_returns_empty_on_failure(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            dirs = _get_active_session_dirs()
        assert dirs == []
