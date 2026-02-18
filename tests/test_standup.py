"""Standup command tests: PR-centric data gathering, Slack formatting, weekend awareness."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from conftest import make_daily
from keephive.models import StandupResponse


def _mock_standup_response():
    return StandupResponse(
        yesterday=["Merged engagement metrics PR #359 https://github.com/org/repo/pull/359",
                    "Completed audit rewrite"],
        today=["Get #360 reviewed and merged",
               "Continue adoption goals #311 toward ready-for-review"],
        blockers=[],
    )


# ---------------------------------------------------------------------------
# TestWeekendAwareCutoff
# ---------------------------------------------------------------------------

class TestWeekendAwareCutoff:
    def test_monday_returns_friday(self, hive_env):
        """On Monday, cutoff should be Friday (3 days back)."""
        from keephive.commands.standup import _weekend_aware_cutoff

        # Patch date.today to return a Monday
        monday = date(2026, 2, 16)  # This is a Monday
        assert monday.weekday() == 0
        with patch("keephive.commands.standup.date") as mock_date:
            mock_date.today.return_value = monday
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = _weekend_aware_cutoff()
        assert result == date(2026, 2, 13)  # Friday

    def test_tuesday_returns_monday(self, hive_env):
        """On Tuesday, cutoff should be Monday (1 day back)."""
        from keephive.commands.standup import _weekend_aware_cutoff

        tuesday = date(2026, 2, 17)  # Tuesday
        assert tuesday.weekday() == 1
        with patch("keephive.commands.standup.date") as mock_date:
            mock_date.today.return_value = tuesday
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = _weekend_aware_cutoff()
        assert result == date(2026, 2, 16)  # Monday

    def test_wednesday_returns_tuesday(self, hive_env):
        """On Wednesday, cutoff should be Tuesday."""
        from keephive.commands.standup import _weekend_aware_cutoff

        wednesday = date(2026, 2, 18)  # Wednesday
        assert wednesday.weekday() == 2
        with patch("keephive.commands.standup.date") as mock_date:
            mock_date.today.return_value = wednesday
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = _weekend_aware_cutoff()
        assert result == date(2026, 2, 17)  # Tuesday


# ---------------------------------------------------------------------------
# TestGatherPrData
# ---------------------------------------------------------------------------

class TestGatherPrData:
    def test_gather_pr_data_with_mocked_gh(self, hive_env, monkeypatch):
        """_gather_pr_data returns open, merged, closed PRs from mocked gh."""
        import subprocess
        from keephive.commands.standup import _gather_pr_data

        call_count = []
        def fake_run(cmd, **kwargs):
            call_count.append(cmd)
            if "--state" in cmd and "open" in cmd:
                stdout = '[{"number":1,"title":"Add feature","isDraft":false,"url":"https://github.com/o/r/pull/1","updatedAt":"2026-02-18","headRefName":"feat","createdAt":"2026-02-17"}]'
            elif "--state" in cmd and "merged" in cmd:
                stdout = '[{"number":2,"title":"Fix bug","url":"https://github.com/o/r/pull/2","mergedAt":"2026-02-17"}]'
            elif "--state" in cmd and "closed" in cmd:
                stdout = '[]'
            else:
                stdout = '[]'
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        result = _gather_pr_data()

        assert len(result["open_prs"]) == 1
        assert result["open_prs"][0]["number"] == 1
        assert len(result["merged_prs"]) == 1
        assert result["merged_prs"][0]["number"] == 2
        assert result["closed_prs"] == []
        assert len(call_count) == 3  # 3 parallel calls

    def test_gather_pr_data_gh_not_found(self, hive_env, monkeypatch):
        """_gather_pr_data returns empty lists when gh is not installed."""
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("gh not found")

        monkeypatch.setattr("subprocess.run", fake_run)
        from keephive.commands.standup import _gather_pr_data
        result = _gather_pr_data()
        assert result == {"open_prs": [], "merged_prs": [], "closed_prs": []}


# ---------------------------------------------------------------------------
# TestStandupDataGathering
# ---------------------------------------------------------------------------

class TestStandupDataGathering:
    def test_gathers_recent_dones(self, hive_env, monkeypatch):
        """Data gathering finds recent DONEs."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        make_daily(hive_env, 0, [
            "- [10:00:00] TODO: Write tests",
            "- [10:05:00] DONE: Write tests",
        ])
        from keephive.commands.standup import _gather_raw_data
        data = _gather_raw_data()
        assert len(data["recent_done"]) >= 1
        assert any("Write tests" in text for _, text in data["recent_done"])

    def test_gathers_open_todos(self, hive_env, monkeypatch):
        """Data gathering finds open TODOs."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        make_daily(hive_env, 0, [
            "- [10:00:00] TODO: Fix the bug",
            "- [10:05:00] TODO: Add more tests",
        ])
        from keephive.commands.standup import _gather_raw_data
        data = _gather_raw_data()
        assert len(data["open_todos"]) >= 2

    def test_gathers_insights(self, hive_env, monkeypatch):
        """Data gathering finds insight entries."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        make_daily(hive_env, 0, [
            "- [10:00:00] DECISION: Use Pydantic for validation",
            "- [10:05:00] FACT: SequenceMatcher has a 0.7 threshold",
            "- [10:10:00] session [keephive] /some/path",
        ])
        from keephive.commands.standup import _gather_raw_data
        data = _gather_raw_data()
        assert len(data["insights"]) >= 1

    def test_empty_log_returns_empty_data(self, hive_env, monkeypatch):
        """Empty daily log returns empty data."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        from keephive.commands.standup import _gather_raw_data
        data = _gather_raw_data()
        assert data["recent_done"] == []
        assert data["open_todos"] == []

    def test_old_dones_not_included(self, hive_env, monkeypatch):
        """DONEs from 3+ days ago are not in recent_done."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        make_daily(hive_env, 3, [
            "- [10:00:00] DONE: Old task",
        ])
        from keephive.commands.standup import _gather_raw_data
        data = _gather_raw_data()
        assert len(data["recent_done"]) == 0

    def test_has_pr_keys(self, hive_env, monkeypatch):
        """Data dict includes merged_prs and closed_prs keys."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        from keephive.commands.standup import _gather_raw_data
        data = _gather_raw_data()
        assert "merged_prs" in data
        assert "closed_prs" in data
        assert "open_prs" in data


# ---------------------------------------------------------------------------
# TestStandupDisplay: Direct calls to extracted display functions
# ---------------------------------------------------------------------------

class TestStandupDisplay:
    def test_display_standup_renders_all_sections(self, hive_env, capsys):
        """display_standup renders yesterday, today, blockers."""
        from keephive.commands.standup import display_standup

        response = _mock_standup_response()
        display_standup(response)

        out = capsys.readouterr().out
        assert "Yesterday:" in out
        assert "Merged engagement metrics" in out
        assert "Completed audit rewrite" in out
        assert "Today:" in out
        assert "Get #360 reviewed" in out
        assert "Continue adoption goals" in out
        assert "Blockers:" in out
        assert "None" in out

    def test_display_standup_with_blockers(self, hive_env, capsys):
        """display_standup shows blockers when present."""
        from keephive.commands.standup import display_standup

        response = StandupResponse(
            yesterday=["Merged feature #1"],
            today=[],
            blockers=["Waiting on API team for auth endpoint"],
        )
        display_standup(response)

        out = capsys.readouterr().out
        assert "Waiting on API team" in out
        assert "None" not in out

    def test_display_standup_empty_yesterday(self, hive_env, capsys):
        """display_standup handles empty yesterday gracefully."""
        from keephive.commands.standup import display_standup

        response = StandupResponse(
            yesterday=[],
            today=["Write tests"],
            blockers=[],
        )
        display_standup(response)

        out = capsys.readouterr().out
        assert "Yesterday:" not in out
        assert "Today:" in out
        assert "Blockers:" in out

    def test_format_standup_slack(self, hive_env):
        """format_standup_slack produces Slack-formatted text."""
        from keephive.commands.standup import format_standup_slack

        response = _mock_standup_response()
        text = format_standup_slack(response)

        assert "*Yesterday:*" in text
        assert "- Merged engagement metrics" in text
        assert "*Today:*" in text
        assert "- Get #360 reviewed" in text
        assert "*Blockers:*" in text
        assert "- None" in text

    def test_format_standup_slack_with_blockers(self, hive_env):
        """format_standup_slack includes real blockers."""
        from keephive.commands.standup import format_standup_slack

        response = StandupResponse(
            yesterday=["Shipped feature"],
            today=[],
            blockers=["CI pipeline broken"],
        )
        text = format_standup_slack(response)

        assert "*Blockers:*" in text
        assert "- CI pipeline broken" in text
        assert "- None" not in text

    def test_format_standup_slack_empty_yesterday(self, hive_env):
        """format_standup_slack shows placeholder for empty yesterday."""
        from keephive.commands.standup import format_standup_slack

        response = StandupResponse(
            yesterday=[],
            today=["Work on feature"],
            blockers=[],
        )
        text = format_standup_slack(response)
        assert "*Yesterday:*" in text
        assert "(no activity)" in text


# ---------------------------------------------------------------------------
# TestStandupSkipLLM
# ---------------------------------------------------------------------------

class TestStandupSkipLLM:
    def test_skip_llm_shows_raw_data(self, hive_env, capsys, monkeypatch):
        """HIVE_SKIP_LLM shows deterministic output with Yesterday/Today/Blockers."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        make_daily(hive_env, 0, [
            "- [10:00:00] DONE: Fixed the bug",
            "- [10:05:00] TODO: Add tests",
        ])
        from keephive.commands.standup import cmd_standup
        cmd_standup([])

        out = capsys.readouterr().out
        assert len(out) > 50, f"Standup output too short ({len(out)} chars)"
        assert "Standup" in out
        assert "Fixed the bug" in out
        assert "Add tests" in out

    def test_empty_standup_shows_help(self, hive_env, capsys, monkeypatch):
        """Empty standup shows getting-started help."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        from keephive.commands.standup import cmd_standup
        cmd_standup([])

        out = capsys.readouterr().out
        assert "No recent activity" in out

    def test_deterministic_yesterday_today_format(self, hive_env, capsys, monkeypatch):
        """Deterministic output uses Yesterday/Today/Blockers sections."""
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))
        make_daily(hive_env, 0, [
            "- [10:00:00] DONE: Shipped feature",
            "- [10:05:00] TODO: Write docs",
        ])
        from keephive.commands.standup import cmd_standup
        cmd_standup([])

        out = capsys.readouterr().out
        assert "Yesterday:" in out
        assert "Today:" in out
        assert "Blockers:" in out

    def test_pr_only_standup_not_empty(self, hive_env, capsys, monkeypatch):
        """PR data alone is enough to generate a standup (no hive data needed)."""
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and "gh" in cmd:
                if "--state" in cmd and "open" in cmd:
                    return sp.CompletedProcess(cmd, 0, stdout='[{"number":42,"title":"Add metrics","isDraft":false,"url":"https://github.com/o/r/pull/42","updatedAt":"2026-02-18","headRefName":"feat","createdAt":"2026-02-17"}]', stderr="")
                return sp.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            raise FileNotFoundError

        monkeypatch.setattr("subprocess.run", fake_run)
        from keephive.commands.standup import cmd_standup
        cmd_standup([])

        out = capsys.readouterr().out
        assert "No recent activity" not in out
        assert "#42" in out


# ---------------------------------------------------------------------------
# TestStandupHallucinationGuard
# ---------------------------------------------------------------------------

class TestStandupHallucinationGuard:
    def test_all_empty_skips_llm(self, hive_env, monkeypatch):
        """When all data sections are empty, _display_llm skips LLM and falls back to deterministic."""
        from keephive.commands.standup import _display_llm

        data = {
            "recent_done": [],
            "open_todos": [],
            "insights": [],
            "open_prs": [],
            "merged_prs": [],
            "closed_prs": [],
            "daily_text": "",
        }
        result = _display_llm(data)
        assert isinstance(result, str)
        assert len(result) > 0, "Fallback output should not be empty"

    def test_empty_markers_use_descriptive_text(self):
        """Verify empty markers start with '(no ' so the guard works."""
        done_text = "\n".join([]) or "(no completed items in logs)"
        todo_text = "\n".join([]) or "(no open TODOs found)"
        merged_text = "\n".join([]) or "(no merged PRs)"
        closed_text = "\n".join([]) or "(no closed PRs)"
        pr_text = "\n".join([]) or "(no open PRs)"
        insight_text = "\n".join([]) or "(no insights recorded)"

        for marker in (done_text, todo_text, merged_text, closed_text, pr_text, insight_text):
            assert marker.startswith("(no "), f"Marker {marker!r} doesn't start with '(no '"

    def test_partial_data_still_calls_llm(self, hive_env, monkeypatch, capsys):
        """When some data exists, _display_llm should attempt LLM (not short-circuit)."""
        from keephive.commands.standup import _display_llm

        data = {
            "recent_done": [("2026-02-18", "Fixed a bug")],
            "open_todos": [],
            "insights": [],
            "open_prs": [],
            "merged_prs": [],
            "closed_prs": [],
            "daily_text": "",
        }
        called = []

        def fake_pipe(*_a, **_kw):
            called.append(True)
            return StandupResponse(yesterday=["Completed: Fixed a bug"], today=[], blockers=[])

        monkeypatch.setattr("keephive.claude.run_claude_pipe", fake_pipe)
        _display_llm(data)
        assert called, "LLM should be called when data is present"
        out = capsys.readouterr().out
        assert "Fixed a bug" in out, f"LLM standup should render yesterday items. Output:\n{out}"


# ---------------------------------------------------------------------------
# TestStandupLLMPath
# ---------------------------------------------------------------------------

class TestStandupLLMPath:
    """Tests that exercise the LLM code path via monkeypatched run_claude_pipe."""

    def test_display_llm_renders_response(self, hive_env, monkeypatch, capsys):
        """Mock pipe returns StandupResponse; verify it renders to stdout."""
        monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))

        make_daily(hive_env, 0, [
            "- [10:00:00] DONE: Shipped the feature",
            "- [10:05:00] TODO: Write docs",
        ])

        def fake_pipe(*_a, **_kw):
            return StandupResponse(
                yesterday=["Shipped the feature"],
                today=["Write docs"],
                blockers=[],
            )

        monkeypatch.setattr("keephive.claude.run_claude_pipe", fake_pipe)

        from keephive.commands.standup import _display_llm, _gather_raw_data
        data = _gather_raw_data()
        _display_llm(data)

        out = capsys.readouterr().out
        assert len(out) > 50, f"LLM standup output too short ({len(out)} chars)"
        assert "Shipped the feature" in out
        assert "Write docs" in out

    def test_display_llm_error_visible_on_failure(self, hive_env, monkeypatch, capsys):
        """Mock pipe raises ClaudePipeError; verify error appears in stdout."""
        monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError))

        make_daily(hive_env, 0, [
            "- [10:00:00] DONE: Something",
        ])

        from keephive.claude import ClaudePipeError

        def failing_pipe(*_a, **_kw):
            raise ClaudePipeError("claude -p timed out after 120s")

        monkeypatch.setattr("keephive.claude.run_claude_pipe", failing_pipe)

        from keephive.commands.standup import _display_llm, _gather_raw_data
        data = _gather_raw_data()
        _display_llm(data)

        captured = capsys.readouterr()
        assert "LLM failed" in captured.out
        assert "timed out" in captured.out


# ---------------------------------------------------------------------------
# TestStandupModel
# ---------------------------------------------------------------------------

class TestStandupModel:
    def test_valid_response(self):
        data = {
            "yesterday": ["Merged feature #1 https://github.com/o/r/pull/1"],
            "today": ["Get #2 reviewed"],
            "blockers": [],
        }
        resp = StandupResponse.model_validate(data)
        assert len(resp.yesterday) == 1
        assert resp.blockers == []

    def test_empty_lists_valid(self):
        data = {
            "yesterday": [],
            "today": [],
            "blockers": [],
        }
        resp = StandupResponse.model_validate(data)
        assert len(resp.yesterday) == 0
        assert len(resp.today) == 0

    def test_with_blockers(self):
        data = {
            "yesterday": ["Fixed bug"],
            "today": [],
            "blockers": ["Waiting on code review"],
        }
        resp = StandupResponse.model_validate(data)
        assert len(resp.blockers) == 1
