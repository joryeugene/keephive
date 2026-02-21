"""hive gc: garbage collect old daily logs, rebuild index."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, timedelta

from keephive.clock import get_today
from keephive.output import console
from keephive.storage import (
    archive_dir,
    daily_dir,
    ensure_dirs,
    get_all_verified_facts,
    guides_dir,
    hive_dir,
    index_file,
    knowledge_dir,
    prompts_dir,
    rebuild_fts_index,
    score_fact_decay,
    stale_days,
    working_dir,
)


def cmd_gc(args: list[str]) -> None:
    ensure_dirs()
    dry_run = "--dry-run" in args

    archived = 0
    console.print("[bold]Garbage collection[/bold]")
    console.print()

    # 1. Archive old daily logs
    sd = stale_days()
    cutoff = (get_today() - timedelta(days=sd)).isoformat()
    dd = daily_dir()
    ad = archive_dir()

    if dd.exists():
        for f in sorted(dd.glob("*.md")):
            # Only process date-named files
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", f.stem):
                continue
            if f.stem < cutoff:
                if dry_run:
                    console.print(f"  [dim]Would archive:[/dim] {f.name}")
                else:
                    f.rename(ad / f.name)
                    console.print(f"  [info]Archived:[/info] {f.name}")
                archived += 1

    # 2. Clean old .reminded-* marker files (legacy PostToolUse hook)
    markers_cleaned = 0
    hd = hive_dir()
    for marker in hd.glob(".reminded-*"):
        if dry_run:
            console.print(f"  [dim]Would remove:[/dim] {marker.name}")
        else:
            try:
                marker.unlink()
            except OSError:
                pass
        markers_cleaned += 1

    if markers_cleaned > 0:
        verb = "Would remove" if dry_run else "Cleaned"
        console.print(f"  [ok]{verb}[/ok] {markers_cleaned} legacy session marker(s)")

    # 3. Clean legacy bash artifacts
    cleaned = 0
    legacy_dirs = [
        (hive_dir() / "bin", "bash scripts"),
        (hive_dir() / "tests", "bash tests"),
    ]
    for legacy, desc in legacy_dirs:
        if legacy.is_dir():
            if dry_run:
                console.print(f"  [dim]Would clean:[/dim] legacy {desc}")
            else:
                shutil.rmtree(legacy)
                console.print(f"  [ok]Cleaned[/ok] legacy {desc}")
            cleaned += 1

    # Clean temp files
    for pattern in [".recall_tmp_*", "*.bak"]:
        for f in hive_dir().glob(pattern):
            if dry_run:
                console.print(f"  [dim]Would remove:[/dim] {f.name}")
            else:
                f.unlink()
            cleaned += 1
    # Also clean working/*.bak
    wd = working_dir()
    if wd.exists():
        for f in wd.glob("*.bak"):
            if dry_run:
                console.print(f"  [dim]Would remove:[/dim] working/{f.name}")
            else:
                f.unlink()
            cleaned += 1

    if cleaned > 0 and not dry_run:
        console.print(f"  [ok]Cleaned[/ok] {cleaned} legacy/temp file(s)")

    # 4. Disk usage
    def _du(path):
        try:
            r = subprocess.run(
                ["du", "-sk", str(path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.stdout.split()[0] + "KB" if r.returncode == 0 else "?"
        except Exception:
            return "?"

    console.print()
    console.print(f"  [ok]Daily logs:[/ok]    {_du(dd)}")
    console.print(f"  [info]Knowledge:[/info]     {_du(knowledge_dir())}")
    console.print(f"  [dim]Archive:[/dim]       {_du(ad)}")
    console.print()

    if archived > 0:
        console.print(f"  Archived {archived} file(s)")
    else:
        console.print("  [ok]Nothing to archive[/ok]")

    # 3. Rebuild index + FTS
    if not dry_run:
        _rebuild_index()
        console.print("  [ok]Index rebuilt[/ok]")
        try:
            rebuild_fts_index()
            console.print("  [ok]FTS index rebuilt[/ok]")
        except Exception as e:
            console.print(f"  [warn]FTS rebuild failed:[/warn] {e}")

    # 4. Memory decay check
    if not dry_run:
        _memory_decay_check()

    console.print()
    console.print("  -> [dim]hive s[/dim] to check status")


def _memory_decay_check() -> None:
    """Score all verified facts and show the bottom 5 as archive candidates."""
    import re

    facts = get_all_verified_facts()
    if not facts:
        return

    scored: list[tuple[float, int, str]] = []
    for _line_num, fact_text, raw_line in facts:
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", raw_line)
        vdate_str = m.group(1) if m else ""
        s = score_fact_decay(fact_text, vdate_str)
        scored.append((s, _line_num, fact_text))

    scored.sort(key=lambda x: x[0])
    candidates = scored[:5]

    if not candidates:
        return

    console.print()
    console.print("[bold]Memory Decay Candidates[/bold]  [dim](lowest score = archive first)[/dim]")
    console.print()
    for score, _, fact_text in candidates:
        console.print(f"  [dim][{score:.2f}][/dim] {fact_text[:100]}")

    console.print()
    console.print("  [dim]Run: hive e memory  to archive manually[/dim]")


def _rebuild_index() -> None:
    """Rebuild the .index.json file."""
    from datetime import datetime

    from keephive.clock import get_now

    wd = working_dir()
    kd = knowledge_dir()
    gd = guides_dir()
    pd = prompts_dir()
    dd = daily_dir()
    ad = archive_dir()

    # Count facts
    fact_count = 0
    for d in [wd, kd]:
        if d.exists():
            for f in d.rglob("*.md"):
                fact_count += sum(1 for line in f.read_text().splitlines() if line.startswith("- "))

    data = {
        "rebuilt": get_now().isoformat(timespec="seconds"),
        "fact_count": fact_count,
        "guides": sum(1 for _ in gd.glob("*.md")) if gd.exists() else 0,
        "prompts": sum(1 for _ in pd.glob("*.md")) if pd.exists() else 0,
        "files": {
            "working": sum(1 for _ in wd.glob("*.md")) if wd.exists() else 0,
            "knowledge": sum(1 for _ in kd.rglob("*.md")) if kd.exists() else 0,
            "daily": sum(1 for _ in dd.glob("*.md")) if dd.exists() else 0,
            "archive": sum(1 for _ in ad.glob("*.md")) if ad.exists() else 0,
        },
    }

    index_file().write_text(json.dumps(data, indent=2) + "\n")
