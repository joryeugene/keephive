"""hive rf: reflect on daily logs for patterns."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime

from keephive.clock import get_now, get_today
from keephive.claude import ClaudePipeError, run_claude_pipe
from keephive.models import GuideDraftResponse, ReflectAnalyzeResponse
from keephive.output import console, notify_sound, prompt_choice, prompt_yn
from keephive.storage import (
    _strip_verified_tags,
    append_to_daily,
    backup_and_write,
    daily_dir,
    ensure_daily,
    guides_dir,
    hive_dir,
    memory_file,
    read_memory,
    recent_daily_files,
)


def cmd_reflect(args: list[str]) -> None:
    subcmd = args[0] if args else ""

    if subcmd == "analyze":
        _reflect_analyze(args[1:])
    elif subcmd == "apply":
        _reflect_apply(args[1:])
    elif subcmd == "draft":
        _reflect_draft(args[1:])
    elif subcmd == "scan":
        _reflect_scan(args[1:])
    elif subcmd == "--json":
        _reflect_scan(["--json"])
    elif subcmd.isdigit():
        _reflect_scan(args)
    elif subcmd == "":
        _reflect_scan([])
    else:
        console.print(f"[err]Unknown reflect subcommand:[/err] {subcmd}")
        console.print("Usage: hive rf [scan|analyze|apply|draft <topic>]")


def _reflect_scan(args: list[str]) -> None:
    """Quick scan of recent daily logs (no AI)."""
    days = 7
    for a in args:
        if a.isdigit():
            days = int(a)

    files = recent_daily_files(days)
    console.print(f"[bold]Last {days} days of logs:[/bold]")
    console.print()

    total_entries = 0
    day_count = 0

    for f in files:
        entries = sum(1 for line in f.read_text().splitlines() if line.startswith("- ["))
        if entries > 0:
            console.print(f"  {f.stem}  {entries} entries")
            total_entries += entries
            day_count += 1

    if total_entries == 0:
        console.print("  [dim]No entries found[/dim]")
        return

    console.print()

    # Find TODOs
    todo_count = 0
    for f in files:
        for line in f.read_text().splitlines():
            if "TODO:" in line and "DONE:" not in line:
                todo_count += 1
                text = re.sub(r".*TODO:\s*", "", line)
                console.print(f"  [info]{text}[/info]")

    if todo_count > 0:
        console.print()

    # Find corrections
    corr_count = 0
    for f in files:
        for line in f.read_text().splitlines():
            if "CORRECTION:" in line:
                corr_count += 1
                text = re.sub(r".*CORRECTION:\s*", "", line)
                console.print(f"  [warn]! {text} ({f.stem})[/warn]")

    if corr_count > 0:
        console.print()

    # Session summaries count
    summary_count = sum(f.read_text().count("### Session Summary") for f in files)
    if summary_count > 0:
        console.print(f"  [bold]Auto-captured:[/bold] {summary_count} session summaries")
        console.print()

    console.print("  -> [dim]hive rf analyze[/dim]    Find patterns with AI (~20s)")
    console.print("  -> [dim]hive l[/dim]             View today's entries")


def _reflect_analyze(args: list[str]) -> None:
    """LLM pattern detection using claude -p."""
    days = int(args[0]) if args and args[0].isdigit() else 7

    files = recent_daily_files(days)
    all_entries = ""
    entry_count = 0

    for f in files:
        all_entries += f"--- {f.stem} ---\n{f.read_text()}\n\n"
        entry_count += sum(1 for line in f.read_text().splitlines() if line.startswith("- ["))

    if entry_count == 0:
        console.print("[warn]No entries to analyze[/warn]")
        return

    console.print(f"Reading {entry_count} entries across {len(files)} days...")

    if os.environ.get("HIVE_SKIP_LLM"):
        console.print("[dim]Skipping LLM analysis (HIVE_SKIP_LLM=1)[/dim]")
        return

    if not prompt_yn("  Analyze with LLM? (~20s)"):
        return

    current_memory = read_memory()
    existing_guides = ""
    gd = guides_dir()
    if gd.exists():
        existing_guides = ", ".join(f.stem for f in gd.glob("*.md"))

    prompt = f"""Analyze these developer daily log entries.

CURRENT WORKING MEMORY:
{current_memory}

EXISTING GUIDES: {existing_guides}

Rules:
- patterns: topics discussed 3+ days. 'has_guide' true if topic matches an existing guide name
- additions: facts worth adding to working memory (not already there)
- contradictions: where log entries conflict with current working memory
- actions: specific hive commands the user should run based on findings.
  Available commands: hive v, hive todo, hive todo done <pattern>,
  hive ke <name>, hive r "...", hive rf, hive doctor, hive mem "fact",
  hive mem rm "pattern", hive rule "rule text"
  Only suggest actions that directly follow from the data. Be specific.
- Be precise. Don't invent. Only report what's in the entries.
- Only report patterns you can point to in the entries. Cite entry dates as evidence.
- For additions, quote the exact log entry that supports the fact."""

    try:
        with console.status("  Analyzing with claude...", spinner="dots"):
            response = run_claude_pipe(
                prompt,
                ReflectAnalyzeResponse,
                stdin_text=all_entries,
                tools=["Read"],
                max_turns=3,
                timeout=240,
            )
    except ClaudePipeError as e:
        notify_sound(False)
        console.print(f"[err]LLM failed: {e}[/err]")
        console.print("[dim]Check: claude -p availability, CLAUDECODE env var[/dim]")
        console.print("  -> [dim]hive rf[/dim] for instant scan (no AI needed)")
        return
    notify_sound(True)
    console.print()

    # Save for reflect apply
    hd = hive_dir()
    (hd / ".last-analyze.json").write_text(response.model_dump_json(indent=2))

    # Persist summary to daily log
    ensure_daily()
    ts = get_now().strftime("%H:%M:%S")
    pattern_count = len(response.patterns)
    addition_count = len(response.additions)
    append_to_daily(f"- [{ts}] REFLECT: {pattern_count} pattern(s), {addition_count} addition(s)")

    # Display results
    if response.patterns:
        console.print("[bold]## Recurring Patterns[/bold]")
        for p in response.patterns:
            guide_status = "[ok](guide exists)[/ok]" if p.has_guide else "[warn](no guide)[/warn]"
            day_word = "day" if p.days == 1 else "days"
            console.print(f"  {p.topic} ({p.days} {day_word}) {guide_status}")
        console.print()

    if response.additions:
        console.print("[bold]## Suggested Additions to Working Memory[/bold]")
        for a in response.additions:
            console.print(f"  + {a.fact} \\[source: {a.source}]")
        console.print()

    if response.contradictions:
        console.print("[bold]## Contradictions[/bold]")
        for c in response.contradictions:
            console.print(f"  [warn]warning[/warn]  Memory: {c.memory}")
            console.print(f"     Latest ({c.date}): {c.log}")
        console.print()

    # Actions from LLM analysis
    if response.actions:
        console.print("[bold]## Suggested Actions[/bold]")
        for action in response.actions:
            console.print(f"  [info]-> {action}[/info]")
        console.print()

    if (
        not response.patterns
        and not response.additions
        and not response.contradictions
        and not response.actions
    ):
        console.print("No significant patterns found in recent logs.")
        console.print()

    # Post-pass: check additions against memory
    _analyze_post_pass(response, current_memory)

    # Suggestions
    unguided = [p for p in response.patterns if not p.has_guide]
    for p in unguided[:2]:
        safe = p.topic.lower().replace(" ", "-").replace("/", "-")
        console.print(f"[dim]-> hive rf draft {safe}    Draft {p.topic} guide from logs[/dim]")

    if response.additions or response.contradictions:
        console.print("[dim]-> hive rf apply    Review and graduate to working memory[/dim]")


def _analyze_post_pass(response: ReflectAnalyzeResponse, current_memory: str) -> None:
    """Deterministic post-pass: flag additions that are already in memory."""
    if not response.additions:
        return

    mem_lower = current_memory.lower()
    not_in_memory = []
    for a in response.additions:
        # Check if key words from the addition appear in memory
        words = [w for w in a.fact.lower().split() if len(w) > 4]
        matches = sum(1 for w in words if w in mem_lower)
        if words and matches < len(words) * 0.5:
            not_in_memory.append(a)

    if not_in_memory:
        console.print("[bold]## Not Yet in Working Memory[/bold]")
        for a in not_in_memory:
            console.print(f'  "{a.fact}" (from {a.source})')
        console.print("  -> [dim]hive rf apply[/dim] to review")
        console.print()


def _reflect_apply(args: list[str]) -> None:
    """Review analysis results and graduate approved items to working memory.

    Reads .last-analyze.json from a prior `hive rf analyze`, walks through
    each addition and contradiction with interactive prompts, and writes
    approved changes to memory.md. No LLM call needed.

    Pass --auto to skip prompts and accept all additions/contradictions.
    """
    auto = "--auto" in args
    hd = hive_dir()
    analyze_path = hd / ".last-analyze.json"

    if not analyze_path.exists():
        console.print("[warn]No pending analysis.[/warn] Run: [dim]hive rf analyze[/dim] (~20s)")
        return

    # Show freshness
    mtime = analyze_path.stat().st_mtime
    age_hours = (time.time() - mtime) / 3600

    try:
        data = json.loads(analyze_path.read_text())
        response = ReflectAnalyzeResponse.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        console.print(f"[err]Error reading analysis: {e}[/err]")
        console.print("  -> [dim]hive rf analyze[/dim] to regenerate")
        return

    # Deduplicate contradictions targeting the same memory line
    if response.contradictions:
        seen_keys: set[str] = set()
        deduped = []
        for c in response.contradictions:
            key = c.memory.strip().lower()
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(c)
        response.contradictions = deduped

    if not response.additions and not response.contradictions:
        console.print("[dim]Analysis has no additions or contradictions to review.[/dim]")
        return

    add_count = len(response.additions)
    contra_count = len(response.contradictions)
    parts = []
    if add_count:
        parts.append(f"{add_count} addition{'s' if add_count != 1 else ''}")
    if contra_count:
        parts.append(f"{contra_count} contradiction{'s' if contra_count != 1 else ''}")
    analysis_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    console.print(f"  Reviewing {', '.join(parts)} (from {analysis_date} analysis)")
    if age_hours > 24 and not auto:
        console.print(
            f"  [warn]Analysis is {age_hours:.0f}h old. Consider re-running: hive rf analyze[/warn]"
        )
    if auto:
        console.print("  [info]Auto mode: accepting all items[/info]")
    console.print()

    mem_path = memory_file()
    mem_content = mem_path.read_text() if mem_path.exists() else ""
    today_str = get_today().isoformat()

    added = 0
    updated = 0
    skipped = 0

    # Walk additions
    total_items = add_count + contra_count
    item_num = 0
    if response.additions:
        for a in response.additions:
            item_num += 1
            console.print(f'  [{item_num}/{total_items}] + "{a.fact}"')
            console.print(f"         from: {a.source}")
            console.print("         to:   working/memory.md")

            if auto:
                choice = "y"
            else:
                choice = prompt_choice("         (y)es  (n)o  (e)dit ? ", ["y", "n", "e"])
            if choice == "y":
                line = f"- {_strip_verified_tags(a.fact)} [verified:{today_str}]"
                mem_content = _append_to_memory(mem_content, line)
                ensure_daily()
                ts = get_now().strftime("%H:%M:%S")
                if auto:
                    console.print("         [ok]Added to memory.md[/ok]")
                    append_to_daily(f"- [{ts}] AUTO-PROMOTED: [reflect] {a.fact}")
                else:
                    console.print("         [ok]Added to memory.md[/ok]")
                    append_to_daily(f"- [{ts}] PROMOTED: {a.fact}")
                added += 1
            elif choice == "e":
                edited = input("         Edit text: ").strip()
                if edited:
                    line = f"- {_strip_verified_tags(edited)} [verified:{today_str}]"
                    mem_content = _append_to_memory(mem_content, line)
                    console.print("         [ok]Added to memory.md[/ok]")
                    added += 1
                else:
                    console.print("         [dim]Skipped (empty edit)[/dim]")
                    skipped += 1
            else:
                console.print("         [dim]Skipped[/dim]")
                skipped += 1
            console.print()

    # Walk contradictions
    if response.contradictions:
        for c in response.contradictions:
            item_num += 1
            console.print(f'  [{item_num}/{total_items}] ! Memory says: "{c.memory}"')
            console.print(f'         Log says ({c.date}): "{c.log}"')
            console.print("         Action: update memory.md line")

            if auto:
                choice = "u"
            else:
                choice = prompt_choice("         (u)pdate  (s)kip ? ", ["u", "s"])
            if choice == "u":
                mem_content = _update_contradiction(mem_content, c.memory, c.log, today_str)
                ensure_daily()
                ts = get_now().strftime("%H:%M:%S")
                if auto:
                    console.print("         [ok]Updated in memory.md[/ok]")
                    append_to_daily(
                        f"- [{ts}] AUTO-PROMOTED: [reflect] Corrected: {c.memory} -> {c.log}"
                    )
                else:
                    console.print("         [ok]Updated in memory.md[/ok]")
                    append_to_daily(f"- [{ts}] CORRECTED: {c.memory} -> {c.log}")
                updated += 1
            else:
                console.print("         [dim]Skipped[/dim]")
                skipped += 1
            console.print()

    # Write changes
    if added > 0 or updated > 0:
        backup_and_write(mem_path, mem_content)

    console.print(
        f"  Done: {added} added, {updated} updated, {skipped} skipped. View: [dim]hive m[/dim]"
    )


def _append_to_memory(mem_content: str, line: str) -> str:
    """Append a line to memory content, before any trailing whitespace."""
    lines = mem_content.rstrip().split("\n")
    lines.append(line)
    return "\n".join(lines) + "\n"


def _update_contradiction(mem_content: str, old_text: str, new_text: str, today_str: str) -> str:
    """Find and replace a contradicted fact in memory.

    Looks for a line containing the old text and replaces it with the
    corrected version.
    """
    old_lower = old_text.lower().strip()
    clean_new = _strip_verified_tags(new_text)
    lines = mem_content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip("- ").strip()
        # Remove any existing verified tag for comparison
        stripped_no_tag = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", stripped)
        if stripped_no_tag.lower().strip() == old_lower or old_lower in stripped_no_tag.lower():
            new_line = f"- {clean_new} [verified:{today_str}]"
            lines[i] = new_line
            return "\n".join(lines)
    # If no match found, append as new
    return _append_to_memory(mem_content, f"- {clean_new} [verified:{today_str}]")


def _reflect_draft(args: list[str]) -> None:
    """Gather daily log entries about a topic and draft a knowledge guide.

    Uses claude -p (haiku) to synthesize the entries into a structured guide.
    User reviews the draft before it's written to guides/.
    """
    if not args:
        console.print("[err]Usage: hive rf draft <topic-name>[/err]")
        console.print("  Example: hive rf draft mcp-patterns")
        return

    topic = args[0]
    safe_name = re.sub(r"[^a-z0-9-]", "-", topic.lower()).strip("-")

    # Gather entries from all daily logs
    dd = daily_dir()
    if not dd.exists():
        console.print("[warn]No daily logs found[/warn]")
        return

    console.print(f'  Gathering "{topic}" from daily logs...')

    entries: list[tuple[str, str]] = []  # (date, context_block)
    topic_lower = topic.lower().replace("-", " ")
    topic_words = topic_lower.split()

    for f in sorted(dd.glob("*.md"), reverse=True):
        lines = f.read_text().splitlines()
        for j, line in enumerate(lines):
            line_lower = line.lower()
            if any(w in line_lower for w in topic_words):
                # Grab surrounding context (3 lines before/after)
                start = max(0, j - 3)
                end = min(len(lines), j + 4)
                context = "\n".join(lines[start:end])
                entries.append((f.stem, context))

    if not entries:
        console.print(f'[warn]No entries found matching "{topic}"[/warn]')
        console.print(f"  -> [dim]hive rc {topic}[/dim] to search all tiers")
        return

    # Deduplicate contexts (same block from same day)
    seen: set[str] = set()
    unique_entries: list[tuple[str, str]] = []
    for d, ctx in entries:
        key = f"{d}:{ctx}"
        if key not in seen:
            seen.add(key)
            unique_entries.append((d, ctx))

    day_count = len(set(d for d, _ in unique_entries))
    console.print(f"  Found {len(unique_entries)} entries across {day_count} days")

    if os.environ.get("HIVE_SKIP_LLM"):
        console.print("[dim]Skipping LLM draft (HIVE_SKIP_LLM=1)[/dim]")
        return

    if not prompt_yn("  Draft guide with LLM?"):
        return

    # Build input for LLM
    entry_text = ""
    for d, ctx in unique_entries:
        entry_text += f"--- {d} ---\n{ctx}\n\n"

    prompt = f"""Synthesize these daily log entries about "{topic}" into a knowledge guide.

The guide should be a concise, actionable markdown document. Structure it with:
- A title (# heading)
- Key sections with ## headings
- Bullet points for specific facts and patterns
- Code examples if relevant entries contain them

Be precise. Only include information that appears in the entries.
Do not invent or extrapolate beyond what the entries say."""

    try:
        with console.status("  Drafting with claude...", spinner="dots"):
            response = run_claude_pipe(
                prompt,
                GuideDraftResponse,
                stdin_text=entry_text,
                timeout=240,
            )
    except ClaudePipeError as e:
        notify_sound(False)
        console.print(f"[err]LLM failed: {e}[/err]")
        console.print("[dim]Check: claude -p availability, CLAUDECODE env var[/dim]")
        return
    notify_sound(True)
    console.print()

    # Preview
    content_lines = response.content.splitlines()
    preview_lines = content_lines[:30]
    console.print("  Preview:")
    console.print("  " + "\u2500" * 40)
    for line in preview_lines:
        console.print(f"  {line}")
    if len(content_lines) > 30:
        console.print(f"  ... ({len(content_lines)} lines total)")
    console.print("  " + "\u2500" * 40)
    console.print()

    # Prompt to write or discard
    guide_path = guides_dir() / f"{safe_name}.md"
    choice = prompt_choice(
        f"  (w)rite to guides/{safe_name}.md  (d)iscard ? ",
        ["w", "d"],
    )
    if choice == "w":
        guides_dir().mkdir(parents=True, exist_ok=True)
        guide_path.write_text(response.content)
        console.print(f"  [ok]Saved.[/ok] View with: hive k {safe_name}")
        ensure_daily()
        ts = get_now().strftime("%H:%M:%S")
        append_to_daily(f"- [{ts}] REFLECT: Drafted guide: {safe_name}")
    else:
        console.print("  [dim]Discarded.[/dim]")


def get_pending_analysis() -> tuple[int, int] | None:
    """Check if there's a recent analysis with pending items.

    Returns (additions_count, contradictions_count) if .last-analyze.json
    exists and is less than 24h old. Returns None otherwise.
    """
    hd = hive_dir()
    analyze_path = hd / ".last-analyze.json"

    if not analyze_path.exists():
        return None

    mtime = analyze_path.stat().st_mtime
    age_hours = (time.time() - mtime) / 3600
    if age_hours > 24:
        return None

    try:
        data = json.loads(analyze_path.read_text())
        response = ReflectAnalyzeResponse.model_validate(data)
        additions = len(response.additions)
        contradictions = len(response.contradictions)
        if additions == 0 and contradictions == 0:
            return None
        return (additions, contradictions)
    except (json.JSONDecodeError, Exception):
        return None
