#!/usr/bin/env python3
"""Gemini CLI AfterModel hook shim for keephive."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


def _keephive_bin() -> str:
    for name in ("keephive", "hive"):
        path = shutil.which(name)
        if path:
            return path
    return "keephive"


def main() -> None:
    raw = sys.stdin.read()
    payload: dict[str, object]
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw": raw}

    try:
        from keephive.telemetry import append_event

        append_event("gemini", "after_model", payload, source="hook")
    except Exception:
        pass

    cmd = [_keephive_bin(), "hook-stop"]
    env = os.environ.copy()
    env.setdefault("HIVE_PLATFORM", "gemini")
    proc = subprocess.run(cmd, input=raw, text=True, capture_output=True, env=env)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
