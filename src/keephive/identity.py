"""Core identity constants and render functions.

Single source of truth for default memory facts, rules, quality standards,
and workflow text. Used by setup.py (initial creation) and sessionstart.py
(injection). Keeps these in sync without maintaining three copies.
"""

from __future__ import annotations

from datetime import date

# --- Effectiveness facts (seeded into default memory.md) ---

EFFECTIVENESS_FACTS: list[str] = [
    "Verify every change. Run it, show output, prove it works.",
    "80% of 'not working' bugs are data/schema mismatches. Check field names first.",
    "Record insights as you learn them. Don't wait to be asked.",
    "Search before creating. Duplicate utilities are the #1 agent code smell.",
    "'Should work' is meaningless. 'WILL work because [evidence]' is the standard.",
    "If you touch a file, leave it cleaner. Fix errors you encounter.",
    "Capture multi-part requests as TODOs before starting work.",
]


# --- Quality standards (injected into every session via sessionstart) ---

QUALITY_STANDARDS: list[str] = [
    "Verify every change: run it, show output, prove it works.",
    "One change at a time. Verify before making the next.",
    "Never say 'should work'. Show the evidence.",
    "Check the simplest cause first when debugging (data/schema before logic).",
    "If you encounter an error, fix it. No 'preexisting' excuses.",
    "Capture multi-part requests as TODOs before starting work.",
]


# --- Default rules (seeded into default rules.md) ---

DEFAULT_RULES: list[tuple[str, str]] = [
    (
        "When You Learn Something New",
        '-> hive_remember("FACT: what you learned") or `hive r "FACT: ..."`',
    ),
    (
        "When a Previous Assumption Was Wrong",
        '-> hive_remember("CORRECTION: old -> new") or `hive r "CORRECTION: ..."`',
    ),
    (
        "When Work Is Left Unfinished",
        '-> hive_remember("TODO: what needs doing") or `hive r "TODO: ..."`',
    ),
    (
        "When You Need Previous Context",
        "-> hive_recall(topic) or `hive rc <topic>`",
    ),
    (
        "Before Writing New Utility Functions",
        "-> hive_recall(concept) first. Consolidate, don't duplicate.",
    ),
    (
        "When Status Shows Stale Facts",
        "-> `hive v` to verify against codebase",
    ),
    (
        "When You Make an Architecture or Tool Choice",
        '-> hive_remember("DECISION: chose X over Y because Z") or `hive r "DECISION: ..."`',
    ),
    (
        "When You Notice a Non-Obvious Pattern",
        '-> hive_remember("INSIGHT: the pattern") or `hive r "INSIGHT: ..."`',
    ),
    (
        "When User Gives a Multi-Part Request",
        "-> IMMEDIATELY log each distinct ask as a TODO before starting work\n"
        '-> hive_remember("TODO: <specific request>") for each one\n'
        "-> User asks get lost during complex sessions. "
        "Capture first, execute second.",
    ),
    (
        "When Something Isn't Working",
        "-> Check the simplest cause first. "
        "80% of 'not showing' bugs are field name mismatches.\n"
        "-> Get actual data (curl, logs, output) before theorizing.\n"
        "-> Never say 'should work' without proof. "
        "Show the evidence.",
    ),
    (
        "When Making Code Changes",
        "-> One change at a time, verify each before the next.\n"
        "-> Read the ENTIRE file before modifying it.\n"
        "-> After every change: run it, test it, show the output.\n"
        "-> Never claim success without proof.",
    ),
    (
        "When Encountering Any Error",
        "-> Fix it. No 'preexisting' excuses. "
        "If you touch a file, that file should be clean when you leave.",
    ),
    (
        "When Finishing a Task",
        "-> Remove dead code you introduced.\n"
        "-> Check for unused imports and empty catch blocks.\n"
        "-> Verify the change is covered by tests. If not, flag it.",
    ),
]


# --- Render functions ---


def render_default_memory() -> str:
    """Render the default memory.md content for hive setup."""
    today = date.today().isoformat()
    lines = [
        "# Working Memory",
        "",
        "Core facts loaded into every session context. Keep lean.",
        "",
        "## Agent Effectiveness",
        "",
    ]
    for fact in EFFECTIVENESS_FACTS:
        lines.append(f"- {fact} [verified:{today}]")
    lines.extend(
        [
            "",
            "## Your Projects",
            "",
            "(Add project-specific facts here as you learn them)",
            "",
        ]
    )
    return "\n".join(lines)


def render_default_rules() -> str:
    """Render the default rules.md content for hive setup."""
    lines = [
        "# Working Rules",
        "",
        "Imperative triggers: when X happens, do Y.",
        "",
    ]
    for heading, body in DEFAULT_RULES:
        lines.append(f"## {heading}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def render_workflows() -> str:
    """Render the workflows section for sessionstart injection."""
    standards = "\n".join(f"- {s}" for s in QUALITY_STANDARDS)

    return (
        "## Workflows\n\n"
        "### Starting Work\n"
        "Check what you already know before diving in:\n"
        "1. hive_recall(topic) or `hive rc <topic>` for previous insights\n"
        "2. hive_todo() or `hive todo` for open items\n"
        "3. Read warnings above for stale facts or overdue items\n\n"
        "### During Work\n"
        "Record as you go. Use hive_remember(text) or `hive r`:\n"
        "- FACT: something learned\n"
        "- DECISION: chose X over Y because Z\n"
        "- CORRECTION: old assumption was wrong, truth is X\n"
        "- TODO: what needs doing\n"
        "- INSIGHT: non-obvious pattern\n\n"
        f"### Quality Standards\n{standards}\n\n"
        "### Code Hygiene\n"
        "Before writing new utility functions or patterns:\n"
        "1. hive_recall(concept) to check if it already exists\n"
        "2. Search the codebase for existing implementations\n"
        "3. Consolidate rather than duplicate\n"
        "After finishing work:\n"
        "- Remove any dead code you introduced\n"
        "- Check for empty catch blocks (log or handle, never swallow)\n"
        "- Verify no orphaned imports or unused variables\n\n"
        "### Knocking Out TODOs\n"
        "1. hive_todo() to see all open items\n"
        "2. Pick one, do it\n"
        "3. hive_todo_done(pattern) to mark it complete\n"
        "4. Repeat until clean\n\n"
        "### Maintenance (CLI only)\n"
        "- `hive v` to verify stale facts against codebase\n"
        "- `hive rf` to find patterns in daily logs\n"
        "- `hive k edit <name>` to create a knowledge guide"
    )
