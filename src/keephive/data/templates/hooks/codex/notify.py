#!/usr/bin/env python3
"""Codex CLI notify hook shim for keephive."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_payload() -> tuple[str, dict[str, object]]:
    raw = sys.stdin.read()
    if not raw.strip() and len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser()
        if candidate.exists():
            raw = candidate.read_text()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {"raw": raw}
    return raw, data


def _keephive_bin() -> str:
    for name in ("keephive", "hive"):
        path = shutil.which(name)
        if path:
            return path
    return "keephive"


def _guess_hive_home() -> Path:
    env = os.environ.get("HIVE_HOME")
    if env:
        return Path(env).expanduser()
    candidates = [
        Path.home() / ".keephive" / "hive",
        Path.home() / ".claude" / "hive",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _append_event_safe(platform: str, event: str, payload: dict | None) -> None:
    try:
        from keephive.telemetry import append_event as _append
    except Exception:
        _append = None

    if _append:
        try:
            _append(platform, event, payload, source="hook")
            return
        except Exception:
            pass

    hive_home = _guess_hive_home()
    telemetry_root = hive_home.parent / "telemetry" / platform.lower()
    telemetry_root.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform,
        "event": event,
        "source": "hook",
        "payload": payload or {},
    }
    target = telemetry_root / "events.jsonl"
    try:
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> None:
    raw, payload = _read_payload()

    _append_event_safe("codex", "notify", payload)

    cmd = [_keephive_bin(), "hook-stop"]
    env = os.environ.copy()
    env.setdefault("HIVE_PLATFORM", "codex")
    proc = subprocess.run(cmd, input=raw, text=True, capture_output=True, env=env)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
