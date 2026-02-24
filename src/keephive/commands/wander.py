"""Wander — KingBee's free-thinking log and seed selection.

hive wander [list|show [slug]|seed <text>|run]
"""

from __future__ import annotations

import random
import re
from collections import Counter
from datetime import date

from keephive.output import console

STOPWORDS = frozenset({
    "hive", "just", "that", "this", "with", "have", "from", "will",
    "make", "need", "more", "some", "been", "your", "they", "when",
    "what", "here", "there", "which", "about", "would", "could",
})


def select_wander_seed(today: date) -> tuple[str, str] | tuple[None, None]:
    """Select a seed deterministically for the given date.

    Priority:
    1. user-queued  — first item in .wander-seeds.json (popped, FIFO)
    2. cross-pollination — two memory.md lines with zero significant-word overlap
    3. recurring-topic  — word in 3+ daily logs (last 14 days) with freq >= 3
    4. stale-todo       — oldest open TODO older than 7 days
    5. None             — no seed available
    """
    from keephive.storage import (
        daily_dir,
        hive_dir,
        pop_wander_seed,
        read_memory,
    )

    # 1. User-queued seed
    seed = pop_wander_seed()
    if seed:
        return seed, "user-queued"

    # 2. Cross-pollination — two memory lines with no significant-word overlap
    memory = read_memory()
    mem_lines = [
        ln.lstrip("- ").strip()
        for ln in memory.splitlines()
        if ln.strip().startswith("- ") and len(ln.strip()) > 10
    ]
    if len(mem_lines) >= 2:
        rng = random.Random(today.isoformat())
        shuffled = list(mem_lines)
        rng.shuffle(shuffled)
        for i, line_a in enumerate(shuffled):
            words_a = {w.lower() for w in re.findall(r"[a-z]+", line_a.lower()) if len(w) > 3} - STOPWORDS
            for line_b in shuffled[i + 1:]:
                words_b = {w.lower() for w in re.findall(r"[a-z]+", line_b.lower()) if len(w) > 3} - STOPWORDS
                if words_a and words_b and not words_a.intersection(words_b):
                    seed = f"{line_a[:80].strip()} / {line_b[:80].strip()}"
                    return seed, "cross-pollination"

    # 3. Recurring topic — word appearing in 3+ of last 14 daily log files
    d_dir = daily_dir()
    if d_dir.exists():
        log_files = sorted(d_dir.glob("*.md"), reverse=True)[:14]
        word_file_counts: Counter[str] = Counter()
        word_total_counts: Counter[str] = Counter()
        for log_path in log_files:
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace").lower()
                words_in_file = {
                    w for w in re.findall(r"[a-z]{4,}", text) if w not in STOPWORDS
                }
                for w in words_in_file:
                    word_file_counts[w] += 1
                for w in re.findall(r"[a-z]{4,}", text):
                    if w not in STOPWORDS:
                        word_total_counts[w] += 1
            except OSError:
                continue

        candidates = [
            w for w, count in word_file_counts.items()
            if count >= 3 and word_total_counts[w] >= 3
        ]
        if candidates:
            rng = random.Random(today.isoformat() + "recurring")
            candidates.sort()
            return rng.choice(candidates), "recurring-topic"

    # 4. Stale TODO — oldest open TODO older than 7 days (detected by age marker)
    todo_path = hive_dir() / "TODO.md"
    if todo_path.exists():
        try:
            text = todo_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if not line.strip().startswith("- [ ]"):
                    continue
                # Look for age marker like [3d 14:22] or similar timestamp info
                # Use the line itself as seed since we can't parse creation date easily
                content = re.sub(r"\[.*?\]", "", line).lstrip("- [ ]").strip()
                if content and len(content) > 5:
                    # Simple heuristic: if it has a timestamp marker suggesting age
                    age_match = re.search(r"\[(\d+)d[\s\]]", line)
                    if age_match:
                        days_old = int(age_match.group(1))
                        if days_old >= 7:
                            return content[:120], "stale-todo"
        except OSError:
            pass

    return None, None


def cmd_wander(args: list[str]) -> None:
    """hive wander [list|show [slug]|seed <text>|run]"""
    sub = args[0] if args else "list"

    if sub == "list":
        _list_wander()
    elif sub == "show":
        _show_wander(args[1] if len(args) > 1 else None)
    elif sub == "seed":
        if len(args) < 2:
            console.print("[err]Usage: hive wander seed <text>[/err]")
            return
        _add_seed(" ".join(args[1:]))
    elif sub == "run":
        _run_wander()
    else:
        console.print(f"[err]Unknown subcommand: {sub}[/err]")
        console.print("Usage: hive wander [list|show [slug]|seed <text>|run]")


def _list_wander() -> None:
    from keephive.storage import list_wander_docs

    docs = list_wander_docs(limit=20)
    if not docs:
        console.print(
            "[dim]No wander docs yet. Run [b]hive daemon enable wander[/b] to start.[/dim]"
        )
        return

    console.print(f"\n  [b]Wander[/b] ({len(docs)} docs)\n")
    for doc in docs:
        date_str = doc["date"]
        if len(date_str) == 8:
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        source = doc.get("seed_source", "")
        hypothesis = doc.get("hypothesis", "")
        question = doc.get("question", "")
        web_note = " [web]" if doc.get("used_web_search") else ""
        console.print(f"  [dim]{date_str}[/dim]  [[cyan]{source}[/cyan]]{web_note}")
        if hypothesis:
            console.print(f"    {hypothesis}")
        if question:
            console.print(f"    [dim]Q: {question}[/dim]")
        console.print()


def _show_wander(slug: str | None) -> None:
    from keephive.storage import list_wander_docs, read_wander_doc, wander_dir

    if slug is None:
        docs = list_wander_docs(limit=1)
        if not docs:
            console.print("[dim]No wander docs yet.[/dim]")
            return
        filename = docs[0]["filename"]
    else:
        # Find by prefix match
        d = wander_dir()
        if not d.exists():
            console.print("[dim]No wander docs yet.[/dim]")
            return
        matches = sorted(
            [p.name for p in d.glob("*.md") if p.name.startswith(slug)],
            reverse=True,
        )
        if not matches:
            console.print(f"[err]No wander doc matching prefix: {slug}[/err]")
            return
        filename = matches[0]

    content = read_wander_doc(filename)
    if not content:
        console.print("[err]Could not read wander doc.[/err]")
        return
    console.print(content)


def _add_seed(text: str) -> None:
    from keephive.storage import add_wander_seed

    text = text.strip()
    if not text:
        console.print("[err]Seed text cannot be empty.[/err]")
        return
    add_wander_seed(text)
    console.print(f"[ok]Queued:[/ok] {text!r}")
    console.print("[dim]Runs next time the wander task fires (hive daemon enable wander).[/dim]")


def _run_wander() -> None:
    """Trigger wander task immediately."""
    from keephive.commands.daemon import _task_wander

    console.print("[dim]Running wander task...[/dim]")
    result = _task_wander()
    if result:
        console.print("[ok]Wander complete.[/ok] Run [b]hive wander show[/b] to read it.")
    else:
        console.print(
            "[dim]No output.[/dim] Check daemon.log or ensure there's a seed available."
        )
