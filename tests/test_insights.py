"""Tests for keephive.insights: deterministic session quality aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keephive.insights import (
    aggregate_insights,
    read_facets_full,
    read_joined_sessions,
    read_session_meta,
)

# ---- Fixture helpers ----


def _write_facet(facets_dir: Path, session_id: str, data: dict) -> None:
    """Write a facets JSON file."""
    data.setdefault("session_id", session_id)
    (facets_dir / f"{session_id}.json").write_text(json.dumps(data))


def _write_meta(meta_dir: Path, session_id: str, data: dict) -> None:
    """Write a session-meta JSON file."""
    data.setdefault("session_id", session_id)
    (meta_dir / f"{session_id}.json").write_text(json.dumps(data))


def _make_usage_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Create usage-data dirs and monkeypatch the home directory."""
    usage = tmp_path / ".claude" / "usage-data"
    facets_dir = usage / "facets"
    meta_dir = usage / "session-meta"
    facets_dir.mkdir(parents=True)
    meta_dir.mkdir(parents=True)
    monkeypatch.setattr("keephive.insights.Path.home", lambda: tmp_path)
    return facets_dir, meta_dir


# ---- read_facets_full ----


class TestReadFacetsFull:
    def test_empty_dir(self, tmp_path, monkeypatch):
        facets_dir, _ = _make_usage_dirs(tmp_path, monkeypatch)
        result = read_facets_full()
        assert result == {}

    def test_single_facet(self, tmp_path, monkeypatch):
        facets_dir, _ = _make_usage_dirs(tmp_path, monkeypatch)
        _write_facet(
            facets_dir,
            "sess-1",
            {
                "outcome": "fully_achieved",
                "session_type": "single_task",
                "friction_counts": {"wrong_approach": 1},
            },
        )
        result = read_facets_full()
        assert "sess-1" in result
        assert result["sess-1"]["outcome"] == "fully_achieved"
        assert result["sess-1"]["friction_counts"] == {"wrong_approach": 1}

    def test_multiple_facets(self, tmp_path, monkeypatch):
        facets_dir, _ = _make_usage_dirs(tmp_path, monkeypatch)
        for i in range(5):
            _write_facet(facets_dir, f"sess-{i}", {"outcome": "fully_achieved"})
        result = read_facets_full()
        assert len(result) == 5

    def test_malformed_json_skipped(self, tmp_path, monkeypatch):
        facets_dir, _ = _make_usage_dirs(tmp_path, monkeypatch)
        (facets_dir / "bad.json").write_text("not json{{{")
        _write_facet(facets_dir, "good", {"outcome": "fully_achieved"})
        result = read_facets_full()
        assert len(result) == 1
        assert "good" in result

    def test_missing_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("keephive.insights.Path.home", lambda: tmp_path)
        result = read_facets_full()
        assert result == {}

    def test_session_id_from_filename(self, tmp_path, monkeypatch):
        facets_dir, _ = _make_usage_dirs(tmp_path, monkeypatch)
        # Write a facet without session_id field; should use filename
        (facets_dir / "fallback-id.json").write_text(json.dumps({"outcome": "not_achieved"}))
        result = read_facets_full()
        assert "fallback-id" in result


# ---- read_session_meta ----


class TestReadSessionMeta:
    def test_empty_dir(self, tmp_path, monkeypatch):
        _, meta_dir = _make_usage_dirs(tmp_path, monkeypatch)
        result = read_session_meta()
        assert result == {}

    def test_single_meta(self, tmp_path, monkeypatch):
        _, meta_dir = _make_usage_dirs(tmp_path, monkeypatch)
        _write_meta(
            meta_dir,
            "sess-1",
            {
                "project_path": "/home/user/proj",
                "duration_minutes": 30,
                "tool_counts": {"Read": 5, "Edit": 2},
            },
        )
        result = read_session_meta()
        assert "sess-1" in result
        assert result["sess-1"]["duration_minutes"] == 30

    def test_malformed_json_skipped(self, tmp_path, monkeypatch):
        _, meta_dir = _make_usage_dirs(tmp_path, monkeypatch)
        (meta_dir / "bad.json").write_text("{invalid")
        _write_meta(meta_dir, "good", {"duration_minutes": 10})
        result = read_session_meta()
        assert len(result) == 1

    def test_missing_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("keephive.insights.Path.home", lambda: tmp_path)
        result = read_session_meta()
        assert result == {}


# ---- read_joined_sessions ----


class TestReadJoinedSessions:
    def test_inner_join_matching(self, tmp_path, monkeypatch):
        facets_dir, meta_dir = _make_usage_dirs(tmp_path, monkeypatch)
        _write_facet(
            facets_dir, "sess-1", {"outcome": "fully_achieved", "session_type": "single_task"}
        )
        _write_meta(
            meta_dir, "sess-1", {"project_path": "/home/user/myproj", "duration_minutes": 45}
        )
        result = read_joined_sessions()
        assert len(result) == 1
        assert result[0]["outcome"] == "fully_achieved"
        assert result[0]["duration_minutes"] == 45
        assert result[0]["project_name"] == "myproj"

    def test_no_match_excluded(self, tmp_path, monkeypatch):
        """Sessions with facets but no meta (or vice versa) are excluded."""
        facets_dir, meta_dir = _make_usage_dirs(tmp_path, monkeypatch)
        _write_facet(facets_dir, "only-facet", {"outcome": "not_achieved"})
        _write_meta(meta_dir, "only-meta", {"duration_minutes": 10})
        result = read_joined_sessions()
        assert len(result) == 0

    def test_partial_match(self, tmp_path, monkeypatch):
        """Only matched sessions appear in result."""
        facets_dir, meta_dir = _make_usage_dirs(tmp_path, monkeypatch)
        _write_facet(facets_dir, "match", {"outcome": "fully_achieved"})
        _write_facet(facets_dir, "orphan", {"outcome": "not_achieved"})
        _write_meta(meta_dir, "match", {"project_path": "/proj", "duration_minutes": 20})
        result = read_joined_sessions()
        assert len(result) == 1
        assert result[0]["session_id"] == "match"

    def test_empty_project_path(self, tmp_path, monkeypatch):
        facets_dir, meta_dir = _make_usage_dirs(tmp_path, monkeypatch)
        _write_facet(facets_dir, "s1", {"outcome": "fully_achieved"})
        _write_meta(meta_dir, "s1", {"project_path": "", "duration_minutes": 5})
        result = read_joined_sessions()
        assert result[0]["project_name"] == ""

    def test_empty_dirs(self, tmp_path, monkeypatch):
        _make_usage_dirs(tmp_path, monkeypatch)
        result = read_joined_sessions()
        assert result == []


# ---- aggregate_insights ----


def _make_session(
    outcome: str = "fully_achieved",
    session_type: str = "single_task",
    friction: dict | None = None,
    satisfaction: dict | None = None,
    goals: dict | None = None,
    duration: float | None = 30.0,
    project: str = "testproj",
    helpfulness: str = "very_helpful",
) -> dict:
    """Build a synthetic session dict for aggregate testing."""
    return {
        "session_id": f"test-{id(outcome)}",
        "outcome": outcome,
        "session_type": session_type,
        "friction_counts": friction or {},
        "user_satisfaction_counts": satisfaction or {"likely_satisfied": 1},
        "goal_categories": goals or {"code_generation": 1},
        "duration_minutes": duration,
        "project_name": project,
        "claude_helpfulness": helpfulness,
    }


class TestAggregateInsights:
    def test_empty_sessions(self):
        result = aggregate_insights([])
        assert result["total_sessions"] == 0
        assert result["outcome_dist"] == {}
        assert result["patterns"] == []

    def test_outcome_distribution(self):
        sessions = [
            _make_session(outcome="fully_achieved"),
            _make_session(outcome="fully_achieved"),
            _make_session(outcome="not_achieved"),
        ]
        result = aggregate_insights(sessions)
        assert result["outcome_dist"]["fully_achieved"] == 2
        assert result["outcome_dist"]["not_achieved"] == 1
        assert result["total_sessions"] == 3

    def test_type_distribution(self):
        sessions = [
            _make_session(session_type="single_task"),
            _make_session(session_type="multi_task"),
            _make_session(session_type="single_task"),
        ]
        result = aggregate_insights(sessions)
        assert result["type_dist"]["single_task"] == 2
        assert result["type_dist"]["multi_task"] == 1

    def test_satisfaction_aggregation(self):
        sessions = [
            _make_session(satisfaction={"satisfied": 3, "neutral": 1}),
            _make_session(satisfaction={"satisfied": 2}),
        ]
        result = aggregate_insights(sessions)
        assert result["satisfaction_dist"]["satisfied"] == 5
        assert result["satisfaction_dist"]["neutral"] == 1

    def test_friction_aggregation(self):
        sessions = [
            _make_session(friction={"wrong_approach": 2}),
            _make_session(friction={"wrong_approach": 1, "buggy_code": 3}),
            _make_session(friction={}),
        ]
        result = aggregate_insights(sessions)
        assert result["friction_dist"]["wrong_approach"]["count"] == 3
        assert result["friction_dist"]["wrong_approach"]["sessions"] == 2
        assert result["friction_dist"]["buggy_code"]["count"] == 3
        assert result["friction_dist"]["buggy_code"]["sessions"] == 1

    def test_goal_distribution_top_10(self):
        sessions = []
        for i in range(15):
            sessions.append(_make_session(goals={f"goal_{i}": 1}))
        # Add extra occurrences for first 3 goals so they rank higher
        for i in range(3):
            sessions.append(_make_session(goals={f"goal_{i}": 1}))
        result = aggregate_insights(sessions)
        assert len(result["goal_dist"]) == 10

    def test_helpfulness_distribution(self):
        sessions = [
            _make_session(helpfulness="very_helpful"),
            _make_session(helpfulness="very_helpful"),
            _make_session(helpfulness="somewhat_helpful"),
        ]
        result = aggregate_insights(sessions)
        assert result["helpfulness_dist"]["very_helpful"] == 2
        assert result["helpfulness_dist"]["somewhat_helpful"] == 1

    def test_per_project_breakdown(self):
        sessions = [
            _make_session(project="proj-a", outcome="fully_achieved"),
            _make_session(project="proj-a", outcome="not_achieved"),
            _make_session(project="proj-b", outcome="fully_achieved"),
        ]
        result = aggregate_insights(sessions)
        assert result["per_project"]["proj-a"]["total"] == 2
        assert result["per_project"]["proj-a"]["outcomes"]["fully_achieved"] == 1
        assert result["per_project"]["proj-b"]["total"] == 1

    def test_pattern_type_outcome_threshold(self):
        """type_outcome patterns require 3+ sessions per type."""
        sessions = [
            _make_session(session_type="single_task", outcome="fully_achieved"),
            _make_session(session_type="single_task", outcome="fully_achieved"),
        ]
        result = aggregate_insights(sessions)
        type_outcome = [p for p in result["patterns"] if p["type"] == "type_outcome"]
        assert len(type_outcome) == 0  # only 2 sessions, below threshold

    def test_pattern_type_outcome_detected(self):
        """3+ sessions triggers type_outcome pattern detection."""
        sessions = [
            _make_session(session_type="single_task", outcome="fully_achieved"),
            _make_session(session_type="single_task", outcome="fully_achieved"),
            _make_session(session_type="single_task", outcome="not_achieved"),
        ]
        result = aggregate_insights(sessions)
        type_outcome = [p for p in result["patterns"] if p["type"] == "type_outcome"]
        assert len(type_outcome) == 1
        assert type_outcome[0]["achieved_rate"] == 0.67
        assert type_outcome[0]["total"] == 3

    def test_pattern_goal_satisfaction(self):
        """Goal satisfaction pattern for 3+ sessions of same category."""
        sessions = [
            _make_session(goals={"debugging": 1}, satisfaction={"satisfied": 1}),
            _make_session(goals={"debugging": 1}, satisfaction={"dissatisfied": 1}),
            _make_session(goals={"debugging": 1}, satisfaction={"neutral": 1}),
        ]
        result = aggregate_insights(sessions)
        goal_patterns = [p for p in result["patterns"] if p["type"] == "goal_satisfaction"]
        assert len(goal_patterns) == 1
        assert goal_patterns[0]["goal_category"] == "debugging"
        # (5 + 2 + 3) / 3 = 3.33
        assert goal_patterns[0]["avg_satisfaction"] == pytest.approx(3.33, abs=0.01)

    def test_pattern_friction_by_type(self):
        """Friction rate pattern requires 3+ sessions."""
        sessions = [
            _make_session(session_type="multi_task", friction={"wrong_approach": 1}),
            _make_session(session_type="multi_task", friction={}),
            _make_session(session_type="multi_task", friction={"buggy_code": 2}),
        ]
        result = aggregate_insights(sessions)
        friction_patterns = [p for p in result["patterns"] if p["type"] == "friction_by_type"]
        assert len(friction_patterns) == 1
        assert friction_patterns[0]["friction_rate"] == pytest.approx(0.67, abs=0.01)

    def test_pattern_duration_by_outcome(self):
        """Duration pattern requires 2+ sessions per outcome."""
        sessions = [
            _make_session(outcome="fully_achieved", duration=20.0),
            _make_session(outcome="fully_achieved", duration=40.0),
            _make_session(outcome="not_achieved", duration=60.0),
        ]
        result = aggregate_insights(sessions)
        duration_patterns = [p for p in result["patterns"] if p["type"] == "duration_by_outcome"]
        achieved_p = [p for p in duration_patterns if p["outcome"] == "fully_achieved"]
        assert len(achieved_p) == 1
        assert achieved_p[0]["avg_duration_minutes"] == 30.0

    def test_missing_fields_graceful(self):
        """Sessions with missing fields don't crash aggregation."""
        sessions = [
            {"session_id": "s1"},  # minimal
            {"session_id": "s2", "outcome": "fully_achieved"},
            {"session_id": "s3", "outcome": "not_achieved", "friction_counts": {"x": 1}},
        ]
        result = aggregate_insights(sessions)
        assert result["total_sessions"] == 3
        assert result["outcome_dist"].get("unknown", 0) == 1
        assert result["outcome_dist"].get("fully_achieved", 0) == 1

    def test_no_duration_skipped_in_pattern(self):
        """Sessions without duration_minutes are excluded from duration patterns."""
        sessions = [
            _make_session(outcome="fully_achieved", duration=None),
            _make_session(outcome="fully_achieved", duration=None),
            _make_session(outcome="fully_achieved", duration=None),
        ]
        result = aggregate_insights(sessions)
        duration_patterns = [p for p in result["patterns"] if p["type"] == "duration_by_outcome"]
        assert len(duration_patterns) == 0
