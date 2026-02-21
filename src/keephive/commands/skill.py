"""hive sk: skill management (publish/unpublish guides as Claude Code skills)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from keephive import __version__
from keephive.output import console
from keephive.storage import guides_dir, hive_dir, today

SKILL_MANIFEST = hive_dir() / ".skill-manifest.json"


def _plugin_dir() -> Path:
    base = Path(
        os.environ.get(
            "KEEPHIVE_PLUGIN_DIR",
            str(Path.home() / ".claude" / "plugins" / "cache" / "keephive"),
        )
    )
    return base / __version__


def _ensure_plugin() -> None:
    pd = _plugin_dir()
    (pd / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (pd / "skills").mkdir(exist_ok=True)
    (pd / "commands").mkdir(exist_ok=True)

    plugin_json = pd / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        plugin_json.write_text(
            json.dumps(
                {
                    "name": "keephive",
                    "version": __version__,
                    "description": "A knowledge sidecar for Claude Code",
                },
                indent=2,
            )
            + "\n"
        )

    if not SKILL_MANIFEST.exists():
        SKILL_MANIFEST.write_text("{}\n")


def _read_manifest() -> dict:
    if SKILL_MANIFEST.exists():
        return json.loads(SKILL_MANIFEST.read_text())
    return {}


def _write_manifest(data: dict) -> None:
    SKILL_MANIFEST.write_text(json.dumps(data, indent=2) + "\n")


def cmd_skill(args: list[str]) -> None:
    subcmd = args[0] if args else ""
    rest = args[1:]

    if subcmd in ("", "list"):
        _skill_list()
    elif subcmd == "publish":
        _skill_publish(rest)
    elif subcmd == "unpublish":
        _skill_unpublish(rest)
    elif subcmd == "sync":
        _skill_sync()
    elif subcmd == "find":
        _skill_find(rest)
    else:
        _skill_view(subcmd)


def _skill_list() -> None:
    _ensure_plugin()
    manifest = _read_manifest()

    gd = guides_dir()

    console.print()
    console.print("[bold]Available Skills[/bold]")

    all_guides = sorted(f.stem for f in gd.glob("*.md")) if gd.exists() else []
    for name in all_guides:
        published = name in manifest
        status = "[ok]published[/ok]" if published else "[dim]local[/dim]"
        console.print(f"  {name}  {status}")

    if not all_guides:
        console.print("  [dim](none)[/dim]")

    console.print()
    console.print(
        "  → [dim]hive sk publish <name>[/dim]  |  [dim]hive sk sync[/dim]  |  [dim]hive k edit <name>[/dim]"
    )


def _skill_publish(args: list[str]) -> None:
    if not args:
        console.print("[warn]Specify a guide to publish, or use 'sync' to publish all[/warn]")
        console.print("Usage: hive sk publish <name>  |  hive sk sync")
        return

    name = args[0]
    gd = guides_dir()
    fpath = gd / f"{name}.md"
    if not fpath.exists():
        console.print(f"[err]Guide not found:[/err] {name}")
        return

    _ensure_plugin()
    _publish_one(name, fpath)
    console.print(f"[ok]Published:[/ok] {name}")


def _skill_unpublish(args: list[str]) -> None:
    if not args:
        console.print("[err]Error: specify a skill name[/err]")
        return

    name = args[0]
    manifest = _read_manifest()
    if name not in manifest:
        console.print(f"[warn]{name} is not published[/warn]")
        return

    del manifest[name]
    _write_manifest(manifest)

    # Remove skill file
    skill_file = _plugin_dir() / "skills" / f"{name}.md"
    if skill_file.exists():
        skill_file.unlink()

    console.print(f"[ok]Unpublished:[/ok] {name}")


def _skill_sync() -> None:
    _ensure_plugin()
    gd = guides_dir()
    if not gd.exists():
        console.print("[dim]No guides to sync[/dim]")
        return

    count = 0
    for f in gd.glob("*.md"):
        _publish_one(f.stem, f)
        count += 1

    console.print(f"[ok]Synced {count} skill(s)[/ok]")


def _skill_find(args: list[str]) -> None:
    query = " ".join(args).lower()
    if not query:
        console.print("[err]Error: specify search terms[/err]")
        return

    gd = guides_dir()
    if not gd.exists():
        console.print("[dim]No guides to search[/dim]")
        return

    for f in sorted(gd.glob("*.md")):
        if query in f.stem.lower() or query in f.read_text().lower():
            console.print(f"  [ok]{f.stem}[/ok]")


def _skill_view(name: str) -> None:
    import sys

    gd = guides_dir()
    fpath = gd / f"{name}.md"
    if fpath.exists():
        text = fpath.read_text()
        print(text)
        if sys.stdout.isatty():
            from keephive.output import copy_to_clipboard

            if copy_to_clipboard(text):
                console.print("[dim]Copied to clipboard[/dim]")
    else:
        console.print(f"[err]Skill not found:[/err] {name}")


def _publish_one(name: str, source: Path) -> None:
    """Copy a guide to the plugin skills directory."""
    content = source.read_text()
    dest = _plugin_dir() / "skills" / f"{name}.md"
    dest.write_text(content)

    manifest = _read_manifest()
    manifest[name] = {
        "source": str(source),
        "type": "guide",
        "published_at": today(),
    }
    _write_manifest(manifest)
