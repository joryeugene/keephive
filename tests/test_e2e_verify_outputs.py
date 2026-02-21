"""Deterministic tests that validate saved E2E lifecycle outputs.

Load JSON from tests/e2e_outputs/lifecycle_*/ and assert on structural
quality. No LLM calls. Runs with the normal test suite (`uv run pytest`).
Skips if saved outputs are absent (run `uv run pytest -m llm` first).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

E2E_OUTPUT_DIR = Path(__file__).parent / "e2e_outputs"


def _load_latest_output(command: str) -> dict | None:
    """Load the most recent valid saved E2E output for a command.

    Skips artifacts that contain LLM error messages (stale runs).
    """
    cmd_dir = E2E_OUTPUT_DIR / command
    if not cmd_dir.exists():
        return None
    files = sorted(cmd_dir.glob("*.json"))
    for f in reversed(files):
        data = json.loads(f.read_text())
        if "LLM failed" not in data.get("output", ""):
            return data
    return None


class TestLifecycleOutputVerification:
    """Validate saved E2E lifecycle outputs with deterministic assertions."""

    def test_best_analyze_output(self):
        data = _load_latest_output("lifecycle_best_analyze")
        if data is None:
            pytest.skip("No saved lifecycle_best_analyze output")

        output = data["output"]
        metadata = data["metadata"]

        assert metadata["stage"] == "analyze"
        assert "Recurring" in output or "Pattern" in output
        assert "deploy" in output.lower()
        assert "Addition" in output or "addition" in output.lower()
        assert "Traceback" not in output

    def test_best_apply_output(self):
        data = _load_latest_output("lifecycle_best_apply")
        if data is None:
            pytest.skip("No saved lifecycle_best_apply output")

        output = data["output"]
        metadata = data["metadata"]

        assert metadata["stage"] == "apply"
        assert "Done:" in output
        assert "Added to memory.md" in output
        assert "Updated in memory.md" in output
        assert metadata.get("additions_count", 0) >= 1
        assert metadata.get("contradictions_count", 0) >= 1

    def test_best_draft_output(self):
        data = _load_latest_output("lifecycle_best_draft")
        if data is None:
            pytest.skip("No saved lifecycle_best_draft output")

        output = data["output"]
        metadata = data["metadata"]

        assert metadata["stage"] == "draft"
        assert "Saved" in output
        assert "deploy" in output.lower()
        assert "Preview:" in output
        assert metadata.get("topic") == "deployment"

    def test_best_recall_output(self):
        data = _load_latest_output("lifecycle_best_recall")
        if data is None:
            pytest.skip("No saved lifecycle_best_recall output")

        output = data["output"]
        metadata = data["metadata"]

        assert metadata["stage"] == "recall"
        assert "result" in output.lower()
        out_lower = output.lower()
        tiers_found = sum(1 for t in ["working", "knowledge", "daily"] if t in out_lower)
        assert tiers_found >= 2, f"Expected >= 2 tiers, found {tiers_found}"
        assert "deploy" in out_lower

    def test_worst_analyze_output(self):
        data = _load_latest_output("lifecycle_worst_analyze")
        if data is None:
            pytest.skip("No saved lifecycle_worst_analyze output")

        output = data["output"]
        metadata = data["metadata"]

        assert metadata["stage"] == "analyze"
        assert "Traceback" not in output
        assert "lunch" not in output.lower()
        assert "weekend" not in output.lower()
        has_substance = (
            "Pattern" in output
            or "Addition" in output
            or "Contradiction" in output
            or "pattern" in output.lower()
            or "addition" in output.lower()
            or "contradiction" in output.lower()
        )
        assert has_substance, f"Expected analysis content in output:\n{output[:500]}"

    def test_worst_apply_output(self):
        data = _load_latest_output("lifecycle_worst_apply")
        if data is None:
            pytest.skip("No saved lifecycle_worst_apply output")

        output = data["output"]
        metadata = data["metadata"]

        assert metadata["stage"] == "apply"
        assert "Done:" in output or "no additions or contradictions" in output.lower()
        assert "lunch" not in output.lower()
        assert "weekend" not in output.lower()

    def test_worst_draft_output(self):
        data = _load_latest_output("lifecycle_worst_draft")
        if data is None:
            pytest.skip("No saved lifecycle_worst_draft output")

        output = data["output"]
        metadata = data["metadata"]

        assert metadata["stage"] == "draft"
        has_guide = "Saved" in output and "api" in output.lower()
        has_no_entries = "No entries found" in output
        assert has_guide or has_no_entries, (
            f"Expected either a saved guide or 'No entries found'. Output:\n{output[:500]}"
        )
        assert "lunch" not in output.lower()
        assert "weekend" not in output.lower()
