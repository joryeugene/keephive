#!/usr/bin/env python3
"""Gemini CLI BeforeTool hook shim for keephive."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path.home() / ".keephive" / ".hook-debug.log"
LOG_MAX_BYTES = 64_000
_BOOTSTRAPPED = False


def _keephive_bin() -> str:
    for name in ("keephive", "hive"):
        path = shutil.which(name)
        if path:
            return path
    return "keephive"


def _cli_env(platform: str) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("HIVE_PLATFORM", platform)
    env["KEEPHIVE_TELEMETRY_SHIM"] = "1"
    return env


def _log_failure(platform: str, event: str, details: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"[{datetime.now(timezone.utc).isoformat()}] "
            f"{platform}:{event} telemetry failed: {details}\n"
        )
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)
        if LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            data = LOG_FILE.read_text(encoding="utf-8")[-(LOG_MAX_BYTES // 2) :]
            LOG_FILE.write_text(data, encoding="utf-8")
    except Exception:
        pass  # pragma: no cover


def _candidate_paths() -> list[str]:
    paths: list[str] = []
    extra = os.environ.get("KEEPHIVE_PYTHONPATH")
    if extra:
        for entry in extra.split(os.pathsep):
            entry = entry.strip()
            if entry and entry not in paths:
                paths.append(entry)

    manifest_path = Path.home() / ".keephive" / ".hook-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
        config_path = manifest.get("gemini", {}).get("config_path")
        if config_path:
            cfg_path = Path(config_path)
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text())
                hooks = cfg.get("hooks", {})
                if isinstance(hooks, dict):
                    for blocks in hooks.values():
                        if not isinstance(blocks, list):
                            continue
                        for block in blocks:
                            for hook in block.get("hooks", []) or []:
                                cmd = hook.get("command", "") or ""
                                for match in re.finditer(r'KEEPHIVE_PYTHONPATH="([^"]+)"', cmd):
                                    candidate = match.group(1).strip()
                                    if candidate and candidate not in paths:
                                        paths.append(candidate)
    except Exception:
        pass
    return paths


def _bootstrap_sys_path() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    for raw in _candidate_paths():
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            resolved = str(candidate)
            if resolved not in sys.path:
                sys.path.append(resolved)
    _BOOTSTRAPPED = True


def _append_event(event: str, payload: dict[str, object]) -> None:
    platform = os.environ.get("HIVE_PLATFORM", "gemini").lower()
    data = payload or {}
    errors: list[str] = []

    try:
        _bootstrap_sys_path()
        extra = os.environ.get("KEEPHIVE_PYTHONPATH")
        if extra:
            for entry in extra.split(os.pathsep):
                if entry and entry not in sys.path:
                    sys.path.append(entry)
        from keephive.telemetry import append_event as _append  # type: ignore

        _append(platform, event, data, source="hook")
        return
    except Exception as exc:  # pragma: no cover
        errors.append(f"import:{exc!r}")

    try:
        cmd = [
            _keephive_bin(),
            "telemetry",
            "append",
            "--platform",
            platform,
            "--event",
            event,
            "--source",
            "hook",
            "--stdin-payload",
        ]
        proc = subprocess.run(
            cmd,
            input=json.dumps(data),
            text=True,
            capture_output=True,
            env=_cli_env(platform),
        )
        if proc.returncode == 0:
            return
        payload_err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        errors.append(f"cli:{payload_err}")
    except Exception as exc:  # pragma: no cover
        errors.append(f"cli_exc:{exc!r}")

    _log_failure(platform, event, "; ".join(errors))


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw": raw}

    _append_event("before_tool", payload)

    cmd = [_keephive_bin(), "hook-userpromptsubmit"]
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
