"""Test Pydantic model validation (the root cause fix)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from keephive.models import (
    DoctorDuplicatesResponse,
    GuideDraftResponse,
    InsightCategory,
    PreCompactResponse,
    RecallExpandResponse,
    ReflectAnalyzeResponse,
    StandupResponse,
    Verdict,
    VerifyResponse,
)


class TestVerifyResponse:
    def test_valid_response(self):
        data = {
            "verdicts": [
                {"index": 1, "verdict": "VALID", "reason": "Still correct"},
                {"index": 2, "verdict": "STALE", "reason": "Outdated", "correction": "New fact"},
                {"index": 3, "verdict": "UNCERTAIN", "reason": "Cannot verify"},
            ]
        }
        resp = VerifyResponse.model_validate(data)
        assert len(resp.verdicts) == 3
        assert resp.verdicts[0].verdict == Verdict.VALID
        assert resp.verdicts[1].correction == "New fact"
        assert resp.verdicts[2].correction is None

    def test_invalid_verdict(self):
        data = {
            "verdicts": [
                {"index": 1, "verdict": "WRONG", "reason": "Bad verdict"},
            ]
        }
        with pytest.raises(ValidationError):
            VerifyResponse.model_validate(data)

    def test_missing_required_field(self):
        data = {
            "verdicts": [
                {"index": 1, "reason": "Missing verdict field"},
            ]
        }
        with pytest.raises(ValidationError):
            VerifyResponse.model_validate(data)

    def test_empty_verdicts(self):
        data = {"verdicts": []}
        resp = VerifyResponse.model_validate(data)
        assert len(resp.verdicts) == 0


class TestPreCompactResponse:
    def test_valid_insights(self):
        data = {
            "insights": [
                {"category": "FACT", "description": "Python is great"},
                {"category": "DECISION", "description": "Use Pydantic"},
            ]
        }
        resp = PreCompactResponse.model_validate(data)
        assert len(resp.insights) == 2
        assert resp.insights[0].category == InsightCategory.FACT

    def test_invalid_category(self):
        data = {
            "insights": [
                {"category": "INVALID", "description": "Bad category"},
            ]
        }
        with pytest.raises(ValidationError):
            PreCompactResponse.model_validate(data)

    def test_empty_insights(self):
        data = {"insights": []}
        resp = PreCompactResponse.model_validate(data)
        assert len(resp.insights) == 0


class TestReflectAnalyzeResponse:
    def test_full_response(self):
        data = {
            "patterns": [
                {"topic": "Python testing", "days": 5, "has_guide": False},
            ],
            "additions": [
                {"fact": "uv is 10x faster than pip", "source": "benchmarks"},
            ],
            "contradictions": [
                {"memory": "pip is standard", "log": "switched to uv", "date": "2026-02-15"},
            ],
        }
        resp = ReflectAnalyzeResponse.model_validate(data)
        assert len(resp.patterns) == 1
        assert resp.patterns[0].topic == "Python testing"
        assert len(resp.additions) == 1
        assert len(resp.contradictions) == 1

    def test_empty_response(self):
        data = {"patterns": [], "additions": [], "contradictions": []}
        resp = ReflectAnalyzeResponse.model_validate(data)
        assert len(resp.patterns) == 0

    def test_actions_field_optional(self):
        """actions field defaults to empty list when omitted."""
        data = {"patterns": [], "additions": [], "contradictions": []}
        resp = ReflectAnalyzeResponse.model_validate(data)
        assert resp.actions == []

    def test_actions_field_populated(self):
        """actions field accepts list of strings."""
        data = {
            "patterns": [],
            "additions": [],
            "contradictions": [],
            "actions": ["hive v", 'hive todo done "old task"'],
        }
        resp = ReflectAnalyzeResponse.model_validate(data)
        assert len(resp.actions) == 2
        assert resp.actions[0] == "hive v"


class TestStandupResponse:
    def test_valid_response(self):
        data = {
            "yesterday": ["Merged feature #1 https://github.com/o/r/pull/1"],
            "today": ["Get #2 reviewed"],
            "blockers": ["Waiting on API fix"],
        }
        resp = StandupResponse.model_validate(data)
        assert len(resp.yesterday) == 1
        assert len(resp.blockers) == 1

    def test_empty_lists(self):
        data = {
            "yesterday": [],
            "today": [],
            "blockers": [],
        }
        resp = StandupResponse.model_validate(data)
        assert len(resp.yesterday) == 0
        assert len(resp.today) == 0

    def test_missing_today_fails(self):
        data = {
            "yesterday": ["Task A"],
            "blockers": [],
        }
        with pytest.raises(ValidationError):
            StandupResponse.model_validate(data)


class TestDoctorDuplicatesResponse:
    def test_valid_with_groups(self):
        data = {
            "duplicate_groups": [
                {
                    "entries": ["Python uses GIL", "CPython has Global Interpreter Lock"],
                    "suggestion": "Consolidate into single entry",
                },
            ],
            "orphaned_todos": ["Fix old API endpoint"],
        }
        resp = DoctorDuplicatesResponse.model_validate(data)
        assert len(resp.duplicate_groups) == 1
        assert len(resp.duplicate_groups[0].entries) == 2
        assert len(resp.orphaned_todos) == 1

    def test_empty_response(self):
        data = {"duplicate_groups": [], "orphaned_todos": []}
        resp = DoctorDuplicatesResponse.model_validate(data)
        assert len(resp.duplicate_groups) == 0
        assert len(resp.orphaned_todos) == 0

    def test_multiple_groups(self):
        data = {
            "duplicate_groups": [
                {"entries": ["A", "A copy"], "suggestion": "Keep A"},
                {"entries": ["B", "B copy"], "suggestion": "Keep B"},
            ],
            "orphaned_todos": [],
        }
        resp = DoctorDuplicatesResponse.model_validate(data)
        assert len(resp.duplicate_groups) == 2


class TestGuideDraftResponse:
    def test_valid_response(self):
        data = {"title": "MCP Patterns", "content": "# MCP Patterns\n\nGuide content here."}
        resp = GuideDraftResponse.model_validate(data)
        assert resp.title == "MCP Patterns"
        assert "# MCP Patterns" in resp.content

    def test_empty_content(self):
        data = {"title": "Empty", "content": ""}
        resp = GuideDraftResponse.model_validate(data)
        assert resp.content == ""

    def test_missing_title_fails(self):
        data = {"content": "Some content"}
        with pytest.raises(ValidationError):
            GuideDraftResponse.model_validate(data)

    def test_missing_content_fails(self):
        data = {"title": "Title only"}
        with pytest.raises(ValidationError):
            GuideDraftResponse.model_validate(data)


class TestRecallExpandResponse:
    def test_valid_response(self):
        data = {"terms": ["speed", "performance", "latency", "throughput", "fast"]}
        resp = RecallExpandResponse.model_validate(data)
        assert len(resp.terms) == 5
        assert resp.terms[0] == "speed"

    def test_empty_terms(self):
        data = {"terms": []}
        resp = RecallExpandResponse.model_validate(data)
        assert len(resp.terms) == 0

    def test_single_term(self):
        data = {"terms": ["caching"]}
        resp = RecallExpandResponse.model_validate(data)
        assert resp.terms[0] == "caching"

    def test_missing_terms_fails(self):
        data = {}
        with pytest.raises(ValidationError):
            RecallExpandResponse.model_validate(data)


class TestClaudePipeJsonParsing:
    """THE critical test: verify we can handle claude -p's actual response format."""

    def test_array_format_with_structured_output(self):
        """claude -p --output-format json returns an array with structured_output."""
        raw = json.dumps(
            [
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "ok",
                    "structured_output": {
                        "verdicts": [
                            {"index": 1, "verdict": "VALID", "reason": "Confirmed"},
                        ]
                    },
                }
            ]
        )
        # Simulate what claude.py does
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            parsed = parsed[-1]
        structured = parsed.get("structured_output")
        if structured is not None:
            parsed = structured

        resp = VerifyResponse.model_validate(parsed)
        assert resp.verdicts[0].verdict == Verdict.VALID

    def test_direct_object_format(self):
        """If claude -p ever returns a plain object."""
        raw = json.dumps(
            {
                "verdicts": [
                    {
                        "index": 1,
                        "verdict": "STALE",
                        "reason": "Updated",
                        "correction": "New value",
                    },
                ]
            }
        )
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            parsed = parsed[-1]
        structured = parsed.get("structured_output")
        if structured is not None:
            parsed = structured

        resp = VerifyResponse.model_validate(parsed)
        assert resp.verdicts[0].verdict == Verdict.STALE
        assert resp.verdicts[0].correction == "New value"

    def test_precompact_array_format(self):
        """PreCompact response in array format."""
        raw = json.dumps(
            [
                {
                    "structured_output": {
                        "insights": [
                            {"category": "DECISION", "description": "Use Python for rewrite"},
                            {"category": "FACT", "description": "jq breaks on arrays"},
                        ]
                    }
                }
            ]
        )
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            parsed = parsed[-1]
        structured = parsed.get("structured_output")
        if structured is not None:
            parsed = structured

        resp = PreCompactResponse.model_validate(parsed)
        assert len(resp.insights) == 2
        assert resp.insights[1].description == "jq breaks on arrays"
