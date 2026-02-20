"""hive mem and hive rule: add/remove facts and rules in working memory."""

from __future__ import annotations

import re

from keephive.output import console
from keephive.storage import (
    backup_and_write,
    ensure_dirs,
    memory_file,
    rules_file,
    today,
)


def cmd_mem(args: list[str]) -> None:
    """Show, add, or remove facts from working memory."""
    if not args:
        mem = memory_file()
        if mem.exists() and mem.read_text().strip():
            text = mem.read_text()
            facts = [line for line in text.splitlines() if line.startswith("- ")]
            console.print(f"[bold]Working Memory[/bold] ({len(facts)} facts)\n")
            console.print(text)
            console.print(
                '\n  -> hive mem "fact" to add  |  hive mem rm "pattern" to remove  |  hive e to edit'
            )
        else:
            console.print("[dim]No working memory yet[/dim]")
            console.print('  -> hive mem "your first fact" to start')
        return

    if args[0] == "rm":
        _remove_line(memory_file(), " ".join(args[1:]), "memory.md")
        return

    fact_text = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", " ".join(args))
    ensure_dirs()
    mem = memory_file()

    if not mem.exists():
        mem.write_text("# Working Memory\n\n")

    # Backup then append (ensure preceding newline)
    backup_and_write(mem, mem.read_text())
    with open(mem, "a") as f:
        if mem.stat().st_size > 0:
            with open(mem, "rb") as check:
                check.seek(-1, 2)
                if check.read(1) != b"\n":
                    f.write("\n")
        f.write(f"- {fact_text} [verified:{today()}]\n")

    console.print(f"[ok]Saved[/ok] to working/memory.md [dim]\\[verified:{today()}][/dim]")
    console.print("[dim]Backup: memory.md.bak[/dim]")


def cmd_rule(args: list[str]) -> None:
    """Show, add, or remove rules from working rules."""
    if not args:
        rf = rules_file()
        if rf.exists() and rf.read_text().strip():
            text = rf.read_text()
            rules = [
                line
                for line in text.splitlines()
                if line.startswith("- ") or line.startswith("-> ")
            ]
            console.print(f"[bold]Working Rules[/bold] ({len(rules)} rules)\n")
            console.print(text)
            console.print(
                '\n  -> hive rule "rule" to add  |  hive rule rm "pattern" to remove  |  hive e rules to edit'
            )
        else:
            console.print("[dim]No working rules yet[/dim]")
            console.print('  -> hive rule "your first rule" to start')
        return

    if args[0] == "rm":
        _remove_line(rules_file(), " ".join(args[1:]), "rules.md")
        return

    rule_text = " ".join(args)
    ensure_dirs()
    rf = rules_file()

    if not rf.exists():
        rf.write_text("# Working Rules\n\n")

    backup_and_write(rf, rf.read_text())
    with open(rf, "a") as f:
        if rf.stat().st_size > 0:
            with open(rf, "rb") as check:
                check.seek(-1, 2)
                if check.read(1) != b"\n":
                    f.write("\n")
        f.write(f"- {rule_text}\n")

    console.print("[ok]Saved[/ok] to working/rules.md")
    console.print("[dim]Backup: rules.md.bak[/dim]")


def _remove_line(path, pattern: str, filename: str) -> None:
    """Remove first line matching pattern from a file."""
    if not pattern:
        console.print("[err]Error: specify a pattern to remove[/err]")
        return

    if not path.exists():
        console.print(f"[warn]No {filename} found[/warn]")
        return

    lines = path.read_text().splitlines(keepends=True)
    found = False
    new_lines = []
    removed_line = ""

    for line in lines:
        if not found and pattern.lower() in line.lower():
            removed_line = line.rstrip()
            found = True
        else:
            new_lines.append(line)

    if not found:
        console.print(f'[warn]No line matching "{pattern}" found in {filename}[/warn]')
        return

    backup_and_write(path, "".join(new_lines))
    console.print(f"[ok]Removed:[/ok] {removed_line}")
    console.print(f"[dim]Backup: {filename}.bak[/dim]")
