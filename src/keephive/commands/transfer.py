"""hive export / hive import: archive and restore hive data."""

from __future__ import annotations

import json
import os
import sys
import tarfile
from pathlib import Path

from keephive import __version__
from keephive.clock import today_iso
from keephive.output import console
from keephive.storage import (
    active_profile,
    ensure_dirs,
    hive_dir,
    profile_dir,
    set_active_profile,
)

# Directories to include in export (relative to hive_dir)
_EXPORT_DIRS = ["working", "daily", "knowledge", "archive"]
# Dot-files to include
_EXPORT_DOTS = [
    ".stats.json",
    ".recall-stats.json",
    ".index.json",
]


def cmd_export(args: list[str]) -> None:
    """Export current profile data as tar.gz."""
    profile = active_profile() or "default"
    default_name = f"hive-{profile}-{today_iso().replace('-', '')}.tar.gz"

    output_path = args[0] if args else default_name
    if not output_path.endswith(".tar.gz"):
        output_path += ".tar.gz"

    source = hive_dir()
    if not source.exists():
        console.print(f"[red]No data found at {source}[/red]")
        sys.exit(1)

    # Build manifest
    manifest = {
        "version": __version__,
        "profile": profile,
        "date": today_iso(),
        "source": str(source),
    }

    with tarfile.open(output_path, "w:gz") as tar:
        # Add manifest
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        import io

        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

        # Add directories
        for dirname in _EXPORT_DIRS:
            dirpath = source / dirname
            if dirpath.exists():
                for fpath in sorted(dirpath.rglob("*")):
                    if fpath.is_file():
                        arcname = str(fpath.relative_to(source))
                        tar.add(str(fpath), arcname=arcname)

        # Add dot-files
        for dotfile in _EXPORT_DOTS:
            fpath = source / dotfile
            if fpath.exists():
                tar.add(str(fpath), arcname=dotfile)

    size_kb = os.path.getsize(output_path) / 1024
    console.print(f"[green]Exported: {output_path} ({size_kb:.1f} KB)[/green]")
    console.print(f"[dim]Profile: {profile}  |  Source: {source}[/dim]")


def cmd_import(args: list[str]) -> None:
    """Import data from a tar.gz archive."""
    if not args:
        console.print("[red]Usage: hive import <path.tar.gz> [--profile name][/red]")
        sys.exit(1)

    archive_path = args[0]
    if not os.path.exists(archive_path):
        console.print(f"[red]File not found: {archive_path}[/red]")
        sys.exit(1)

    # Parse --profile flag
    target_profile = None
    for i, arg in enumerate(args[1:], 1):
        if arg == "--profile" and i + 1 < len(args):
            target_profile = args[i + 1]
            break
        if arg.startswith("--profile="):
            target_profile = arg.split("=", 1)[1]
            break

    # Validate archive (check for path traversal)
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                console.print(f"[red]Security: archive contains unsafe path: {member.name}[/red]")
                sys.exit(1)

        # Read manifest if present
        manifest = {}
        try:
            mf = tar.extractfile("manifest.json")
            if mf:
                manifest = json.loads(mf.read())
        except (KeyError, json.JSONDecodeError):
            pass

    # Determine target directory
    if target_profile:
        target = profile_dir(target_profile)
        if target.exists():
            console.print(f"[yellow]Profile '{target_profile}' already exists[/yellow]")
            from keephive.output import prompt_yn

            if not prompt_yn(f"Overwrite data in {target}?"):
                console.print("[dim]Cancelled[/dim]")
                return
        else:
            target.mkdir(parents=True, exist_ok=True)
            # Scaffold dirs
            old_profile = active_profile()
            set_active_profile(target_profile)
            try:
                ensure_dirs()
            finally:
                set_active_profile(old_profile)
    else:
        target = hive_dir()
        from keephive.output import prompt_yn

        if not prompt_yn(f"Import into current profile at {target}?"):
            console.print("[dim]Cancelled[/dim]")
            return

    # Extract
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name == "manifest.json":
                continue  # Skip manifest
            # Security: re-check paths
            if member.name.startswith("/") or ".." in member.name:
                continue
            dest = target / member.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                src = tar.extractfile(member)
                if src:
                    dest.write_bytes(src.read())

    source_profile = manifest.get("profile", "unknown")
    console.print(f"[green]Imported from: {archive_path}[/green]")
    console.print(f"[dim]Source profile: {source_profile}  |  Target: {target}[/dim]")
