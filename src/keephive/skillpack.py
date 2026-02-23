"""Utilities for packaging and deploying keephive-helper skills across agents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Optional

from keephive.storage import hive_dir

_SKILL_NAME = "keephive-helper"
_BASE_TEMPLATE = "templates/skills/base.md"
_PLATFORM_TEMPLATES = {
    "claude": "templates/skills/claude.md",
    "gemini": "templates/skills/gemini.md",
    "codex": "templates/skills/codex.md",
}


@dataclass(slots=True)
class SkillRender:
    platform: str
    title: str
    slug: str
    content: str
    hash: str


def _read_template(path: str) -> str:
    return resources.files("keephive.data").joinpath(path).read_text()


def render_platform_skill(platform: str) -> SkillRender:
    platform = platform.lower()
    if platform not in _PLATFORM_TEMPLATES:
        raise ValueError(f"Unsupported platform: {platform}")

    base = _read_template(_BASE_TEMPLATE)
    specific = _read_template(_PLATFORM_TEMPLATES[platform])
    title = {
        "claude": "Claude Code",
        "gemini": "Gemini CLI",
        "codex": "Codex CLI",
    }[platform]

    rendered = (
        base.replace("{{platform_title}}", title)
        .replace("{{platform_slug}}", platform)
        .replace("{{platform_specific}}", specific.strip())
    )
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return SkillRender(platform=platform, title=title, slug=platform, content=rendered, hash=digest)


def manifest_path() -> Path:
    """Location of the shared skill manifest."""
    return hive_dir().parent / ".skill-manifest.json"


def read_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def write_manifest(data: dict) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def record_deployment(render: SkillRender, path: Path) -> None:
    manifest = read_manifest()
    auto = manifest.setdefault("__auto", {})
    auto[render.platform] = {
        "skill": _SKILL_NAME,
        "hash": render.hash,
        "path": str(path),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_manifest(manifest)


def get_record(platform: str) -> Optional[dict]:
    manifest = read_manifest()
    auto = manifest.get("__auto", {})
    return auto.get(platform)
