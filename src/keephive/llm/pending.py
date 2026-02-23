"""Persistent queue for LLM requests when no backend is available."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from keephive.storage import hive_dir


def _queue_path() -> Path:
    return hive_dir().parent / "pending" / "llm.jsonl"


def queue_request(
    prompt: str,
    *,
    model: str,
    tools: list[str] | None,
    stdin_text: str | None,
    max_turns: int | None,
) -> None:
    """Persist details about a request that could not run."""
    path = _queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    entry = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        "prompt_preview": prompt[:4000],
        "prompt_digest": digest,
        "model": model,
        "tools": tools or [],
        "stdin": bool(stdin_text),
        "max_turns": max_turns,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def list_pending(limit: int | None = None) -> list[dict]:
    """Return queued requests (most recent last)."""
    path = _queue_path()
    if not path.exists():
        return []
    records: list[dict] = []
    if limit is None:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    else:
        from collections import deque

        buffer = deque(maxlen=limit)
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                buffer.append(line)
        for line in buffer:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def pending_count() -> int:
    path = _queue_path()
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def clear_queue() -> None:
    path = _queue_path()
    if path.exists():
        path.unlink()
