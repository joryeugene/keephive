import json
from pathlib import Path

import pytest

from keephive.llm.pending import list_pending, pending_count, queue_request


@pytest.fixture(autouse=True)
def _tmp_hive(monkeypatch, tmp_path):
    hive_home = tmp_path / "hive"
    monkeypatch.setenv("HIVE_HOME", str(hive_home))
    return hive_home


def test_queue_request_writes_entry(tmp_path):
    queue_request(
        "Test prompt",
        model="test-model",
        tools=["search"],
        stdin_text=None,
        max_turns=2,
    )

    assert pending_count() == 1
    records = list_pending()
    assert records and records[0]["model"] == "test-model"
    assert records[0]["tools"] == ["search"]
    queue_file = Path(tmp_path / "pending" / "llm.jsonl")
    assert queue_file.exists()
    payload = json.loads(queue_file.read_text().strip())
    assert payload["prompt_preview"].startswith("Test prompt")
