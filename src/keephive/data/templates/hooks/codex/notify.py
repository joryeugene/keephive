#!/usr/bin/env python3
"""Codex CLI notify hook shim for keephive."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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


def main() -> None:
    raw, payload = _read_payload()

    try:
        from keephive.telemetry import append_event

        append_event("codex", "notify", payload, source="hook")
    except Exception:
        pass

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
