"""Shared platform integration helpers (skills, hooks, detection)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict

from keephive.storage import hive_dir


@dataclass(slots=True)
class PlatformSpec:
    slug: str
    title: str
    config_path: Path
    hooks_dir: Path
    skill_path: Path
    detected: bool
    detection_reason: str


def _detect(dir_path: Path, binary: str) -> tuple[bool, str]:
    cli = shutil.which(binary)
    if cli:
        return True, f"{binary} CLI detected at {cli}"
    if dir_path.exists():
        return True, f"{dir_path} exists"
    return False, f"{binary} CLI/config not found"


def platform_specs() -> Dict[str, PlatformSpec]:
    home = Path.home()

    claude_dir = home / ".claude"
    gemini_dir = home / ".gemini"
    codex_dir = home / ".codex"

    claude_detected, claude_reason = _detect(claude_dir, "claude")
    gemini_detected, gemini_reason = _detect(gemini_dir, "gemini")
    codex_detected, codex_reason = _detect(codex_dir, "codex")

    keephive_root = hive_dir().parent

    return {
        "claude": PlatformSpec(
            slug="claude",
            title="Claude Code",
            config_path=claude_dir / "settings.json",
            hooks_dir=keephive_root / "hooks" / "claude",
            skill_path=claude_dir / "skills" / "keephive-helper" / "SKILL.md",
            detected=claude_detected,
            detection_reason=claude_reason,
        ),
        "gemini": PlatformSpec(
            slug="gemini",
            title="Gemini CLI",
            config_path=gemini_dir / "settings.json",
            hooks_dir=keephive_root / "hooks" / "gemini",
            skill_path=gemini_dir / "skills" / "keephive-helper" / "SKILL.md",
            detected=gemini_detected,
            detection_reason=gemini_reason,
        ),
        "codex": PlatformSpec(
            slug="codex",
            title="Codex CLI",
            config_path=codex_dir / "config.toml",
            hooks_dir=keephive_root / "hooks" / "codex",
            skill_path=codex_dir / "skills" / "keephive-helper" / "SKILL.md",
            detected=codex_detected,
            detection_reason=codex_reason,
        ),
    }


def hook_manifest_path() -> Path:
    return hive_dir().parent / ".hook-manifest.json"


def read_hook_manifest() -> dict:
    path = hook_manifest_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def write_hook_manifest(data: dict) -> None:
    path = hook_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def record_hook_update(slug: str, scripts: dict[str, str], config_path: Path) -> None:
    manifest = read_hook_manifest()
    manifest[slug] = {
        "scripts": scripts,
        "config_path": str(config_path),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_hook_manifest(manifest)


__all__ = [
    "PlatformSpec",
    "platform_specs",
    "hook_manifest_path",
    "read_hook_manifest",
    "write_hook_manifest",
    "record_hook_update",
]
