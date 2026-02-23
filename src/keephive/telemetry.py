"""Platform telemetry store for keephive."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from keephive.storage import hive_dir


def _root() -> Path:
    return hive_dir().parent / "telemetry"


def telemetry_file(platform: str) -> Path:
    platform = platform.lower()
    return _root() / platform / "events.jsonl"


def append_event(
    platform: str, event: str, payload: dict | None = None, *, source: str = "hook"
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform,
        "event": event,
        "source": source,
        "payload": payload or {},
    }
    path = telemetry_file(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_events(platform: str, *, limit: Optional[int] = None) -> list[dict]:
    path = telemetry_file(platform)
    if not path.exists():
        return []

    events: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        if limit is None:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        else:
            # Read tail efficiently by keeping a ring buffer
            from collections import deque

            buffer = deque(maxlen=limit)
            for line in fh:
                buffer.append(line)
            for line in buffer:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def platforms() -> Iterable[str]:
    root = _root()
    if not root.exists():
        return []
    return [p.name for p in root.iterdir() if p.is_dir()]


def summarize(platform: str) -> dict:
    """Return basic telemetry stats for a platform."""
    path = telemetry_file(platform)
    total = 0
    latest: dict | None = None
    if not path.exists():
        return {"platform": platform, "total": 0, "latest": None}

    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            latest = record

    return {
        "platform": platform,
        "total": total,
        "latest": latest,
    }
