"""Inbox — surface KingBee output and review queues.

hive inbox [--days N]
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_KINGBEE_RE = re.compile(r"^\[(🐝 )?KingBee (\d{2}:\d{2})\] (.+)$")
# A regular log timestamp entry or excerpt delimiter ends a KingBee content block.
_END_RE = re.compile(r"^(- \[\d{2}:\d{2}:\d{2}\]|<!--)")

# Navigation hint shown below entries that have full content elsewhere
_TYPE_HINTS: dict[str, str] = {
    "wander": "hive wander show",
    "stale-check": "hive verify",
}
# Fallback hint for long entries with no type-specific target
_DEFAULT_LONG_HINT = "hive log"


def _plural(n: int, word: str) -> str:
    """Return "N word" (singular) or "N words" (plural)."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _parse_kingbee_entries(text: str) -> list[dict]:
    """Parse KingBee entries from a daily log text.

    Returns list of dicts: {time: str, type: str, lines: list[str]}.
    Content ends at the next timestamp entry, KingBee header, or excerpt marker.
    """
    entries: list[dict] = []
    current: dict | None = None

    for line in text.splitlines():
        m = _KINGBEE_RE.match(line)
        if m:
            if current is not None:
                entries.append(current)
            current = {
                "time": m.group(2),
                "type": m.group(3).strip(),
                "lines": [],
            }
            continue

        if current is not None:
            if _END_RE.match(line):
                entries.append(current)
                current = None
            elif line.strip():
                current["lines"].append(line.rstrip())

    if current is not None:
        entries.append(current)

    return entries


def _queue_depths() -> dict:
    """Count items pending review in each queue.

    Returns dict with keys: facts, rules, improvements, todos.
    """
    from keephive.storage import hive_dir, open_todos, read_pending_improvements

    hd = hive_dir()

    facts_path = hd / ".pending-facts.md"
    facts_count = 0
    if facts_path.exists():
        facts_count = sum(
            1
            for line in facts_path.read_text(errors="replace").splitlines()
            if line.startswith("- ")
        )

    rules_path = hd / ".pending-rules.md"
    rules_count = 0
    if rules_path.exists():
        rules_count = sum(
            1
            for line in rules_path.read_text(errors="replace").splitlines()
            if line.startswith("- ")
        )

    return {
        "facts": facts_count,
        "rules": rules_count,
        "improvements": len(read_pending_improvements()),
        "todos": len(open_todos()),
    }


def cmd_inbox(args: list[str]) -> None:
    """hive inbox [--days N] — surface KingBee output and review queues."""
    from keephive.clock import get_today
    from keephive.output import console
    from keephive.storage import daily_file, safe_read_text

    # Default 2 = today + yesterday. Clamped to [1, 30].
    days = 2
    for i, arg in enumerate(args):
        if arg == "--days" and i + 1 < len(args):
            try:
                days = max(1, min(int(args[i + 1]), 30))
            except ValueError:
                pass

    today = get_today()

    all_entries: list[tuple[date, dict]] = []
    for delta in range(days):
        day = today - timedelta(days=delta)
        path = daily_file(day.isoformat())
        if not path.exists():
            continue
        for entry in _parse_kingbee_entries(safe_read_text(path)):
            all_entries.append((day, entry))

    console.print()
    console.print("  [b]Inbox[/b]")
    console.print()

    if all_entries:
        console.print("  [b]KingBee Activity[/b]")
        console.print()
        for day, entry in all_entries:
            type_label = entry["type"]
            time_str = entry["time"]
            content_lines = entry["lines"]

            console.print(f"  [dim]{day.isoformat()} {time_str}[/dim]  {type_label}")

            if not content_lines:
                console.print("    [dim](no output)[/dim]")
            else:
                for line in content_lines[:6]:
                    console.print(f"    {line}")
                if len(content_lines) > 6:
                    extra = len(content_lines) - 6
                    line_word = "line" if extra == 1 else "lines"
                    console.print(f"    [dim]... ({extra} more {line_word})[/dim]")

            # Navigation hint: type-specific first, then generic for long entries
            hint = _TYPE_HINTS.get(type_label.lower())
            if hint is None and len(content_lines) > 6:
                hint = _DEFAULT_LONG_HINT
            if hint:
                console.print(f"    [dim]→ {hint}[/dim]")

            console.print()
    else:
        day_word = "day" if days == 1 else "days"
        console.print(f"  [dim]No KingBee activity in the last {days} {day_word}.[/dim]")
        console.print()

    depths = _queue_depths()
    has_queue = any(depths.values())

    console.print("  [b]Needs Your Attention[/b]")
    console.print()

    _COL = 36  # fixed label column width for consistent → arrow alignment

    if not has_queue:
        console.print("  [dim]Nothing in the review queue.[/dim]")
    else:
        if depths["facts"]:
            label = _plural(depths["facts"], "fact") + " to review"
            console.print(f"    • {label:<{_COL}}  → hive mem review")
        if depths["rules"]:
            label = _plural(depths["rules"], "rule") + " to review"
            console.print(f"    • {label:<{_COL}}  → hive rule review")
        if depths["improvements"]:
            label = _plural(depths["improvements"], "improvement") + " to review"
            console.print(f"    • {label:<{_COL}}  → hive improve review")
        if depths["todos"]:
            label = _plural(depths["todos"], "open TODO")
            console.print(f"    • {label:<{_COL}}  → hive todo")

    console.print()
