"""hive k, ke, p, pe: knowledge guides and prompts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from keephive.output import console
from keephive.storage import ensure_dirs, guides_dir, prompts_dir


def _resolve_file(name: str, *directories: Path) -> Path | None:
    """Resolve name to file: exact -> prefix -> substring across directories."""
    # Exact match
    for d in directories:
        for ext in ["", ".md"]:
            candidate = d / f"{name}{ext}"
            if candidate.exists():
                return candidate

    # Prefix match
    candidates = []
    for d in directories:
        if d.exists():
            candidates.extend(f for f in d.glob(f"{name}*.md") if f.is_file())
    if len(candidates) == 1:
        return candidates[0]

    # Substring match
    if not candidates:
        for d in directories:
            if d.exists():
                candidates.extend(f for f in d.glob(f"*{name}*.md") if f.is_file())
    if len(candidates) == 1:
        return candidates[0]

    return None


def cmd_knowledge(args: list[str]) -> None:
    subcmd = args[0] if args else ""
    if subcmd in ("edit", "e"):
        cmd_knowledge_edit(args[1:])
    elif subcmd == "rm":
        _knowledge_rm(args[1:], guides_dir())
    elif subcmd == "":
        _knowledge_list()
    else:
        _knowledge_view(subcmd)


def cmd_knowledge_edit(args: list[str]) -> None:
    name = " ".join(args)
    if not name:
        console.print("[err]Error: specify a guide name[/err]")
        console.print("Usage: hive k edit <name>")
        return

    ensure_dirs()

    # Try to resolve existing file first
    match = _resolve_file(name, guides_dir())
    if match:
        fpath = match
    else:
        safe_name = name.lower().replace(" ", "-")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "-")
        fpath = guides_dir() / f"{safe_name}.md"
        fpath.write_text(f"---\ntags: []\nprojects: []\n---\n# {name}\n\n")
        console.print(f"[ok]Created[/ok] -> {fpath}")

    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(fpath)])
    console.print(f"[ok]Saved[/ok] -> {fpath}")
    console.print(f"\n  -> [dim]hive k[/dim] to list  |  [dim]hive k {fpath.stem}[/dim] to view")


def cmd_prompt(args: list[str]) -> None:
    subcmd = args[0] if args else ""
    if subcmd in ("edit", "e"):
        cmd_prompt_edit(args[1:])
    elif subcmd == "rm":
        _knowledge_rm(args[1:], prompts_dir())
    elif subcmd == "":
        _prompt_list()
    else:
        _knowledge_view(subcmd)


def cmd_prompt_edit(args: list[str]) -> None:
    name = " ".join(args)
    if not name:
        console.print("[err]Error: specify a prompt name[/err]")
        console.print("Usage: hive p edit <name>")
        return

    ensure_dirs()

    # Try to resolve existing file first
    match = _resolve_file(name, prompts_dir())
    if match:
        fpath = match
    else:
        safe_name = name.lower().replace(" ", "-")
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "-")
        fpath = prompts_dir() / f"{safe_name}.md"
        fpath.write_text(f"# {name}\n\n")
        console.print(f"[ok]Created[/ok] -> {fpath}")

    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(fpath)])
    console.print(f"[ok]Saved[/ok] -> {fpath}")


def _knowledge_list() -> None:
    ensure_dirs()
    guide_count = 0
    prompt_count = 0

    console.print()
    console.print("[bold]Knowledge Guides[/bold]")

    gd = guides_dir()
    if gd.exists():
        for f in sorted(gd.glob("*.md")):
            name = f.stem
            tags = ""
            text = f.read_text()
            if text.startswith("---"):
                for line in text.splitlines()[1:]:
                    if line.startswith("---"):
                        break
                    if line.startswith("tags:"):
                        tags = line.split(":", 1)[1].strip().strip("[]")
                        break
            if tags:
                console.print(f"  [ok]{name}[/ok]    [dim]\\[tags: {tags}][/dim]")
            else:
                console.print(f"  [ok]{name}[/ok]")
            guide_count += 1

    if guide_count == 0:
        console.print("  [dim](none yet)[/dim]")

    console.print()
    console.print("[bold]Prompts[/bold]")

    pd = prompts_dir()
    if pd.exists():
        for f in sorted(pd.glob("*.md")):
            console.print(f"  [info]{f.stem}[/info]")
            prompt_count += 1

    if prompt_count == 0:
        console.print("  [dim](none yet)[/dim]")

    console.print()
    console.print("  -> [dim]hive k <name>[/dim] to view  |  [dim]hive k edit <name>[/dim] to create  |  [dim]hive k rm <name>[/dim] to remove")


def _knowledge_view(name: str) -> None:
    """View a guide by name with fuzzy matching."""
    gd = guides_dir()
    pd = prompts_dir()

    match = _resolve_file(name, gd, pd)
    if match:
        print(match.read_text())
        return

    # Check for ambiguous matches
    candidates = []
    for d in [gd, pd]:
        if d.exists():
            candidates.extend(f for f in d.glob(f"*{name}*.md") if f.is_file())

    if len(candidates) > 1:
        console.print(f"[warn]Ambiguous:[/warn] {name} matches {len(candidates)} files:")
        for c in candidates:
            console.print(f"  {c.stem}")
    else:
        console.print(f"[err]Guide not found:[/err] {name}")
        console.print()
        all_items: list[tuple[str, str]] = []
        if gd.exists():
            all_items.extend((f.stem, "guide") for f in gd.glob("*.md"))
        if pd.exists():
            all_items.extend((f.stem, "prompt") for f in pd.glob("*.md"))
        if all_items:
            console.print("Available:")
            for n, kind in sorted(all_items):
                console.print(f"  {n} [dim]({kind})[/dim]")


def _knowledge_rm(args: list[str], directory: Path) -> None:
    """Remove a guide or prompt by name."""
    name = " ".join(args)
    if not name:
        console.print("[err]Error: specify name to remove[/err]")
        return
    match = _resolve_file(name, directory)
    if match:
        match.unlink()
        console.print(f"[ok]Removed[/ok] {match.stem}")
    else:
        console.print(f"[err]Not found:[/err] {name}")


def _prompt_list() -> None:
    """List only prompt templates (not guides)."""
    ensure_dirs()
    console.print()
    console.print("[bold]Prompts[/bold]")

    pd = prompts_dir()
    count = 0
    if pd.exists():
        for f in sorted(pd.glob("*.md")):
            console.print(f"  [info]{f.stem}[/info]")
            count += 1

    if count == 0:
        console.print("  [dim](none yet)[/dim]")

    console.print()
    console.print("  -> [dim]hive p <name>[/dim] to view  |  [dim]hive pe <name>[/dim] to create")
