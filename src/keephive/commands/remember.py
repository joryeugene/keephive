"""hive r (remember) and hive rc (recall)."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta

from keephive.output import console, prompt_yn
from keephive.storage import (
    append_to_daily,
    archive_dir,
    daily_dir,
    daily_file,
    ensure_daily,
    fts_search,
    knowledge_dir,
    stale_days,
    working_dir,
)


def cmd_remember(args: list[str]) -> None:
    """Remember an insight to today's daily log."""
    insight = " ".join(args)
    if not insight:
        console.print("[err]Error: nothing to remember[/err]")
        console.print("Usage: hive r \"your insight here\"")
        return

    ensure_daily()
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S")

    append_to_daily(f"- [{timestamp}] {insight}")

    # Detect category
    category = ""
    for cat in ("FACT", "DECISION", "CORRECTION", "TODO", "INSIGHT"):
        if insight.startswith(f"{cat}:") or insight.startswith(f"{cat} "):
            category = cat
            break

    # Count today's entries
    df = daily_file()
    entry_count = sum(1 for line in df.read_text().splitlines() if line.startswith("- ["))

    if category:
        console.print(f"  [ok]Remembered[/ok]  [bold]\\[{category}][/bold]  {timestamp}")
    else:
        console.print(f"  [ok]Remembered[/ok]  {timestamp}")
    console.print(f"    -> daily/{df.name}  ({entry_count} entries today)")


def cmd_recall(args: list[str]) -> None:
    """Search across all memory tiers, with optional LLM query expansion."""
    json_mode = "--json" in args
    deep_mode = "--deep" in args
    query_parts = [a for a in args if not a.startswith("--")]
    query = " ".join(query_parts)

    if not query:
        console.print("[err]Error: nothing to search for[/err]")
        console.print("Usage: hive rc <query> [--json] [--deep]")
        return

    results = _search_all_tiers(query)

    if not results:
        if json_mode:
            print(json.dumps({"query": query, "results": [], "count": 0}))
        else:
            console.print(f"No results for: {query}")
            console.print(f"\n  -> hive r \"{query} ...\" to remember something about this")
        return

    if json_mode:
        print(json.dumps({"query": query, "results": results, "count": len(results)}, indent=2))
        return

    if deep_mode and not os.environ.get("HIVE_SKIP_LLM") and len(results) < 5:
        if prompt_yn("  Expand search with AI?"):
            expanded = _expand_and_search(query, results)
            if expanded is not None:
                results = expanded

    _display_results(query, results)


def _display_results(query: str, results: list[dict]) -> None:
    """Display recall results to console."""
    console.print(f"Found {len(results)} result(s) for: {query}\n")
    tier_styles = {
        "working": "tier.working",
        "knowledge": "tier.knowledge",
        "daily": "tier.daily",
        "archive": "tier.archive",
    }

    for r in results[:20]:
        style = tier_styles.get(r["tier"], "")
        score_str = f"[{r['score']:>3}]"
        tier_str = f"({r['tier']})"
        line = r["line"]
        # Strip grep line-number prefix for daily/archive tiers only
        if r["tier"] in ("daily", "archive") and ":" in line:
            line = line.split(":", 1)[-1].strip()
        if r["tier"] == "working":
            line = re.sub(r"^-\s*", "", line)
            line = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line)
        date_str = f" {r.get('date', '')}" if r.get("date") else ""
        console.print(f"[{style}]{score_str} {tier_str}{date_str}[/{style}] {line}")

    if len(results) > 20:
        console.print(f"\n  [dim]Showing 20 of {len(results)} results[/dim]")

    console.print(f"\n  -> hive e to edit working memory  |  hive k <name> to view a guide")


def _expand_and_search(query: str, existing: list[dict]) -> list[dict] | None:
    """Use LLM to generate related search terms and find additional results.

    Returns merged results or None if expansion fails or finds nothing new.
    """
    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.models import RecallExpandResponse

    prompt = (
        f'Given the search query "{query}", suggest 5 related search terms. '
        f"Include synonyms, related concepts, and abbreviations."
    )
    try:
        with console.status("  Expanding search with AI...", spinner="dots"):
            response = run_claude_pipe(prompt, RecallExpandResponse, timeout=20)
    except (ClaudePipeError, Exception) as e:
        print(f"[keephive] LLM recall expansion failed: {e}", file=sys.stderr)
        return None

    if not response.terms:
        return None

    existing_lines = {r["line"] for r in existing}
    expanded: list[dict] = []
    for term in response.terms[:5]:
        for hit in _search_all_tiers(term):
            if hit["line"] not in existing_lines:
                hit["score"] = int(hit["score"] * 0.6)
                expanded.append(hit)
                existing_lines.add(hit["line"])

    if not expanded:
        return None
    merged = list(existing) + expanded
    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged


def _fuzzy_word_score(query_words: list[str], line_words: list[str]) -> float:
    """Best-match word similarity using SequenceMatcher (Ratcliff/Obershelp).

    For each query word, finds the highest-similarity line word.
    Returns the average of those best matches (0.0-1.0).
    """
    from difflib import SequenceMatcher

    if not query_words or not line_words:
        return 0.0

    total = 0.0
    for qw in query_words:
        best = max(SequenceMatcher(None, qw, lw).ratio() for lw in line_words)
        total += best
    return total / len(query_words)


def _search_all_tiers(query: str) -> list[dict]:
    """Search across working, knowledge, daily, archive tiers."""
    results: list[dict] = []
    q_lower = query.lower()
    sd = stale_days()
    today_d = date.today()
    cutoff = today_d - timedelta(days=sd)

    # Tier 1: Working memory
    wd = working_dir()
    if wd.exists():
        for f in wd.glob("*.md"):
            for line in f.read_text().splitlines():
                if q_lower in line.lower():
                    score = 100
                    # Penalize stale facts
                    vm = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
                    if vm:
                        try:
                            vdate = date.fromisoformat(vm.group(1))
                            if vdate < cutoff:
                                score = score // 2
                        except ValueError:
                            pass
                    date_val = vm.group(1) if vm else ""
                    results.append({"tier": "working", "score": score, "line": line, "date": date_val})

    # Tier 2: Knowledge
    kd = knowledge_dir()
    if kd.exists():
        for f in kd.rglob("*.md"):
            for line in f.read_text().splitlines():
                if q_lower in line.lower():
                    results.append({"tier": "knowledge", "score": 80, "line": line})

    # Tier 3: Daily logs + archive — try FTS first, fall back to grep
    fts_hits = fts_search(query)
    if fts_hits:
        results.extend(fts_hits)
    else:
        dd = daily_dir()
        if dd.exists():
            files = sorted(dd.glob("*.md"), reverse=True)[:30]
            for f in files:
                fname = f.stem
                for line in f.read_text().splitlines():
                    if q_lower in line.lower():
                        try:
                            d = date.fromisoformat(fname)
                            days_ago = (today_d - d).days
                        except ValueError:
                            days_ago = 30
                        score = max(10, 70 - days_ago)
                        results.append({
                            "tier": "daily",
                            "score": score,
                            "line": line,
                            "file": str(f),
                            "date": fname,
                        })

        ad = archive_dir()
        if ad.exists():
            for f in ad.rglob("*.md"):
                for line in f.read_text().splitlines():
                    if q_lower in line.lower():
                        results.append({"tier": "archive", "score": 5, "line": line})

    results.sort(key=lambda x: x["score"], reverse=True)

    # Fuzzy second-pass when exact results are sparse
    if len(results) < 5:
        query_words = [w for w in re.findall(r"[a-z]+", q_lower) if len(w) > 2]
        if query_words:
            existing_lines = {r["line"] for r in results}
            for tier_name, tier_path, score_base in [
                ("working", wd, 100),
                ("knowledge", kd, 80),
            ]:
                if not tier_path or not tier_path.exists():
                    continue
                for f in tier_path.rglob("*.md"):
                    for line in f.read_text().splitlines():
                        if line in existing_lines or not line.strip():
                            continue
                        line_words = [w for w in re.findall(r"[a-z]+", line.lower()) if len(w) > 2]
                        if not line_words:
                            continue
                        ratio = _fuzzy_word_score(query_words, line_words)
                        if ratio >= 0.65:
                            results.append({
                                "tier": tier_name,
                                "score": int(score_base * ratio * 0.7),
                                "line": line,
                            })
                            existing_lines.add(line)
            results.sort(key=lambda x: x["score"], reverse=True)

    return results
