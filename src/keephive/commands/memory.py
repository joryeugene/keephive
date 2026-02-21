"""hive mem and hive rule: add/remove facts and rules in working memory."""

from __future__ import annotations

import json
import re
from pathlib import Path

from keephive.output import console
from keephive.storage import (
    backup_and_write,
    ensure_dirs,
    memory_file,
    rules_file,
    today,
)

# ---- Friction-to-rule mapping ----
# Maps Claude Code /insights friction types to actionable behavioral rules.
# Environmental types (rate_limit, api_error, tool_unavailable, tool_limitation)
# are excluded because Claude cannot change its behavior to prevent them.

FRICTION_RULES: dict[str, str] = {
    "wrong_approach": (
        "Before starting implementation, state your intended approach in 1-2 sentences "
        "and wait for confirmation. Do not begin coding until the user agrees with the direction."
    ),
    "misunderstood_request": (
        "When a request could mean multiple things, ask a clarifying question before acting. "
        "Restate what you understood and confirm."
    ),
    "user_rejected_action": (
        "When the user interrupts or rejects an action, stop immediately. "
        "Ask what they wanted instead rather than continuing the current approach."
    ),
    "buggy_code": (
        "After writing or modifying code, run the relevant tests or build command "
        "and show the output before presenting work as complete."
    ),
    "excessive_changes": (
        "Make the minimal change needed. Do not refactor, reorganize, or improve "
        "unrelated code unless explicitly asked."
    ),
    "excessive_planning": (
        "Start implementation quickly. If the user asks for a change, make it directly "
        "instead of writing a plan first."
    ),
    "excessive_exploration": (
        "When the task is clear, act directly. Use sub-agents for codebase exploration "
        "so the main session stays focused on implementation."
    ),
}

# Annotation format in .pending-rules.md: [N sessions: friction_type]
_FRICTION_ANNOTATION_RE = re.compile(r"^\[\d+ sessions?: [^\]]+\]\s*")


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

    if args[0] == "learn":
        dry_run = "--dry-run" in args[1:]
        _rule_learn(dry_run=dry_run)
        return

    if args[0] == "review":
        _rule_review()
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


def _rule_review() -> None:
    """Review and accept/reject pending rule suggestions from .pending-rules.md."""
    from keephive.storage import hive_dir

    pending_path = hive_dir() / ".pending-rules.md"
    if not pending_path.exists() or not pending_path.read_text().strip():
        console.print("[dim]No pending rule suggestions.[/dim]")
        return

    lines = [ln for ln in pending_path.read_text().splitlines() if ln.strip().startswith("- ")]
    if not lines:
        console.print("[dim]No pending rule suggestions.[/dim]")
        pending_path.write_text("")
        return

    accepted = []
    remaining = []
    for line in lines:
        rule_text = line.lstrip("- ").strip()

        # Extract and display annotation if present (from rule learn)
        annotation_match = _FRICTION_ANNOTATION_RE.match(rule_text)
        if annotation_match:
            annotation = annotation_match.group(0).strip()
            clean_rule = rule_text[annotation_match.end():]
            console.print(f"\n  [dim]{annotation}[/dim]")
            console.print("  Suggested rule:")
            console.print(f"  [bold]{clean_rule}[/bold]")
        else:
            clean_rule = rule_text
            console.print("\n  Suggested rule:")
            console.print(f"  [bold]{clean_rule}[/bold]")

        console.print()
        response = input("  Add to rules.md? [y/N/e(dit)]: ").strip().lower()
        if response == "y":
            # Strip annotation before writing to rules.md
            accepted.append(clean_rule)
        elif response.startswith("e"):
            edited = input("  Edit rule: ").strip()
            if edited:
                accepted.append(edited)
            else:
                remaining.append(line)
        else:
            remaining.append(line)

    # Apply accepted rules
    if accepted:
        rf = rules_file()
        ensure_dirs()
        if not rf.exists():
            rf.write_text("# Working Rules\n\n")
        backup_and_write(rf, rf.read_text())
        with open(rf, "a") as f:
            with open(rf, "rb") as check:
                check.seek(-1, 2)
                if check.read(1) != b"\n":
                    f.write("\n")
            for rule in accepted:
                f.write(f"- {rule}\n")
        console.print(f"\n[ok]Added {len(accepted)} rule(s).[/ok]")

    # Write back remaining
    if remaining:
        pending_path.write_text("\n".join(remaining) + "\n")
        console.print(f"[dim]{len(remaining)} suggestion(s) deferred.[/dim]")
    else:
        pending_path.write_text("")
        console.print("[dim]No more pending rules.[/dim]")


def _trigrams(text: str) -> set[str]:
    """Extract character trigrams from text for fuzzy matching."""
    words = text.lower().split()
    t: set[str] = set()
    for w in words:
        if len(w) >= 3:
            for i in range(len(w) - 2):
                t.add(w[i : i + 3])
    return t


def _extract_rule_lines(text: str) -> str:
    """Extract only rule content lines from rules/pending files.

    Strips headers, blank lines, and formatting to leave just the rule text.
    This prevents false positives from large documents with many common words.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("-> "):
            lines.append(stripped)
    return " ".join(lines)


def _rule_already_covered(candidate: str, existing_text: str) -> bool:
    """Check if a candidate rule is already covered by existing rules.

    Extracts only rule lines (- or ->) from existing text to avoid false
    positives from headers and formatting. Then uses trigram overlap proportion:
    if 45%+ of the candidate's character trigrams appear in the extracted
    rule text, the candidate is considered covered.
    """
    rule_text = _extract_rule_lines(existing_text)
    if not rule_text:
        return False
    candidate_trigrams = _trigrams(candidate)
    if not candidate_trigrams:
        return False
    existing_trigrams = _trigrams(rule_text)
    overlap = candidate_trigrams & existing_trigrams
    return len(overlap) / len(candidate_trigrams) >= 0.45


def _read_facets() -> dict[str, dict[str, int]]:
    """Read friction data from Claude Code facet files.

    Returns {friction_type: {"count": N, "sessions": N}} aggregated across
    all facet files. Each file represents one session.
    """
    facets_dir = Path.home() / ".claude" / "usage-data" / "facets"
    if not facets_dir.exists():
        return {}

    result: dict[str, dict[str, int]] = {}
    for fpath in facets_dir.glob("*.json"):
        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for ftype, count in data.get("friction_counts", {}).items():
            if ftype not in result:
                result[ftype] = {"count": 0, "sessions": 0}
            result[ftype]["count"] += count
            result[ftype]["sessions"] += 1

    return result


def _rule_learn(dry_run: bool = False) -> None:
    """Learn rules from Claude Code /insights friction data.

    Reads facet files, aggregates friction by type, maps actionable types
    to behavioral rules, deduplicates against existing rules, and queues
    new candidates in .pending-rules.md for review.
    """
    from keephive.storage import hive_dir

    # 1. Read and aggregate friction data
    friction = _read_facets()
    if not friction:
        console.print("[dim]No friction data found.[/dim]")
        console.print("[dim]  Friction data comes from Claude Code's /insights command.[/dim]")
        console.print("[dim]  Location: ~/.claude/usage-data/facets/[/dim]")
        return

    # 2. Print friction summary table
    console.print("[bold]Friction Summary[/bold]\n")
    console.print(f"  {'Type':<26s} {'Count':>5s}  {'Sessions':>8s}")
    console.print(f"  {'─' * 26} {'─' * 5}  {'─' * 8}")
    for ftype, fdata in sorted(friction.items(), key=lambda x: -x[1]["count"]):
        actionable = ftype in FRICTION_RULES
        marker = " *" if actionable else ""
        console.print(
            f"  {ftype:<26s} {fdata['count']:>5d}  {fdata['sessions']:>8d}{marker}"
        )
    console.print("\n  [dim]* = actionable (has a mapped rule)[/dim]")

    # 3. Filter: only types in FRICTION_RULES with >= 3 unique sessions
    candidates: list[tuple[str, str, int]] = []  # (type, rule_text, session_count)
    for ftype, rule_text in FRICTION_RULES.items():
        fdata = friction.get(ftype)
        if fdata and fdata["sessions"] >= 3:
            candidates.append((ftype, rule_text, fdata["sessions"]))

    if not candidates:
        console.print("\n[dim]No friction types meet the threshold (3+ sessions).[/dim]")
        return

    # 4. Dedup against existing rules + pending rules
    rf = rules_file()
    existing_rules = rf.read_text() if rf.exists() else ""
    pending_path = hive_dir() / ".pending-rules.md"
    pending_text = pending_path.read_text() if pending_path.exists() else ""
    combined_existing = existing_rules + "\n" + pending_text

    new_candidates: list[tuple[str, str, int]] = []
    for ftype, rule_text, session_count in candidates:
        if _rule_already_covered(rule_text, combined_existing):
            continue
        new_candidates.append((ftype, rule_text, session_count))

    if not new_candidates:
        console.print(f"\n[dim]All {len(candidates)} candidate(s) already covered by existing rules.[/dim]")
        return

    # 5. Display candidates
    console.print(f"\n[bold]New Rule Candidates ({len(new_candidates)})[/bold]\n")
    for ftype, rule_text, session_count in new_candidates:
        console.print(f"  [dim][{session_count} sessions: {ftype}][/dim]")
        console.print(f"  {rule_text}\n")

    if dry_run:
        console.print("[dim]Dry run: no rules queued.[/dim]")
        return

    # 6. Append to .pending-rules.md
    ensure_dirs()
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    with open(pending_path, "a") as f:
        for ftype, rule_text, session_count in new_candidates:
            f.write(f"- [{session_count} sessions: {ftype}] {rule_text}\n")

    console.print(
        f"[ok]Queued {len(new_candidates)} rule(s).[/ok] Run: [bold]hive rule review[/bold]"
    )


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
