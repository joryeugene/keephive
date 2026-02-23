from __future__ import annotations

from keephive.telemetry import append_event, summarize


def test_telemetry_summary_counts(monkeypatch, tmp_path):
    hive_home = tmp_path / "hive"
    monkeypatch.setenv("HIVE_HOME", str(hive_home))

    append_event("claude", "session_start", {"source": "test"})
    append_event("claude", "after_model", {"source": "test"})

    stats = summarize("claude")
    assert stats["total"] == 2
    assert isinstance(stats["latest"], dict)
    assert stats["latest"]["event"] == "after_model"


def test_telemetry_summary_empty(monkeypatch, tmp_path):
    hive_home = tmp_path / "hive"
    monkeypatch.setenv("HIVE_HOME", str(hive_home))

    stats = summarize("gemini")
    assert stats == {"platform": "gemini", "total": 0, "latest": None}
