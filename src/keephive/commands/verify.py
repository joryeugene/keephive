"""hive v: verify stale facts against codebase using claude -p with tool access."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from pathlib import Path

from keephive.claude import ClaudePipeError, run_claude_pipe
from keephive.models import VerifyResponse
from keephive.output import console, notify_sound, prompt_yn
from keephive.storage import (
    append_to_daily,
    backup_and_write,
    ensure_daily,
    get_all_verified_facts,
    get_evidence_for_fact,
    get_stale_facts,
    get_unverified_auto_facts,
    memory_file,
    normalize_memory,
    store_evidence,
    today,
    version_context,
)

# Tools the model can use to investigate facts
VERIFY_TOOLS = ["Read", "Grep", "Glob", "WebSearch"]


def _build_verify_prompt(facts: list[tuple[int, str, str]], versions: str) -> str:
    """Build the verification prompt for a batch of facts."""
    facts_lines = []
    for i, (_, fact, _) in enumerate(facts):
        line = f"{i + 1}. {fact}"
        evidence = get_evidence_for_fact(fact)
        if evidence and evidence.get("last_reason"):
            line += f"\n   Previous evidence ({evidence.get('last_date', '?')}): {evidence['last_reason'][:150]}"
            locs = evidence.get("source_locations")
            if locs:
                line += f"\n   Known locations: {', '.join(locs[:3])}"
        facts_lines.append(line)
    facts_text = "\n".join(facts_lines)

    return f"""You are a fact-checking investigator with access to tools.

FACTS TO VERIFY:
{facts_text}

SYSTEM INFO:
{versions}
Date: {today()}
System: {platform.system()} {platform.release()}

INVESTIGATION INSTRUCTIONS:
For each fact, actively investigate using available tools:
- Read/Grep/Glob: search the local codebase for evidence
- WebSearch: check external tools, versions, libraries
- When previous evidence is provided, check those locations first (faster verification)

After investigating, provide your verdict:
- VALID: found confirming evidence (cite what you found)
- STALE: found contradicting evidence (provide corrected fact text in correction field)
- UNCERTAIN: investigated but found no evidence either way

For STALE verdicts, correction must contain the full replacement fact text."""


def cmd_verify(args: list[str]) -> None:
    json_mode = "--json" in args
    check_mode = "--check" in args
    verbose = "--verbose" in args
    dark_mode = "--dark" in args
    limit_arg = next(
        (args[i + 1] for i, a in enumerate(args) if a == "--limit" and i + 1 < len(args)),
        None,
    )
    limit = int(limit_arg) if limit_arg else None

    if os.environ.get("HIVE_SKIP_LLM"):
        if check_mode:
            sys.exit(0)
        console.print("[dim]Skipping verification (HIVE_SKIP_LLM=1)[/dim]")
        return

    mem = memory_file()
    if not mem.exists():
        if check_mode:
            sys.exit(0)
        console.print(
            '[warn]No working memory to verify. Add facts first: hive mem "FACT: something you learned"[/warn]'
        )
        return

    # --check mode: quick stale count, no LLM
    if check_mode:
        stale_count = len(get_stale_facts())
        if stale_count > 0:
            console.print(f"{stale_count} stale")
            sys.exit(1)
        else:
            all_count = len(get_all_verified_facts())
            console.print(f"All current ({all_count} facts)")
            sys.exit(0)

    # Main path: verified facts + unverified auto facts (dark knowledge)
    all_facts = get_all_verified_facts()
    auto_facts = get_unverified_auto_facts()

    if dark_mode:
        all_facts = auto_facts  # skip re-verifying already-verified facts
    else:
        all_facts = all_facts + auto_facts

    if limit is not None:
        all_facts = all_facts[:limit]

    fact_count = len(all_facts)

    if fact_count == 0:
        if json_mode:
            print(json.dumps({"fact_count": 0, "message": "No verified facts found"}))
        else:
            console.print("[dim]No verified facts to check[/dim]")
        return

    if dark_mode:
        console.print(
            f"[bold]Verifying {fact_count} unreviewed fact(s) (dark knowledge only)...[/bold]"
        )
    else:
        console.print(f"[bold]Verifying {fact_count} fact(s) against codebase...[/bold]")
        if auto_facts and limit is None:
            console.print(
                f"  [dim]({len(auto_facts)} auto-captured, never reviewed)[/dim]"
            )
    console.print("[dim](This uses claude -p with tool access and takes 10-20 seconds)[/dim]")
    console.print()

    for _, fact_text, _ in all_facts:
        console.print(f"  [dim]* {fact_text}[/dim]")
    console.print()

    if not prompt_yn("  Verify with LLM?"):
        console.print("  [dim]Skipped.[/dim]")
        return

    # Capture pre-verification freshness for delta
    try:
        from keephive.commands.stats import _knowledge_health

        pre_health = _knowledge_health()
        pre_fresh_pct = pre_health["fresh_pct"]
    except Exception:
        pre_fresh_pct = None

    # Build the prompt with tool-based investigation
    versions = version_context()

    BATCH_SIZE = 8
    batches = [all_facts[i : i + BATCH_SIZE] for i in range(0, len(all_facts), BATCH_SIZE)]
    all_verdicts: list = []
    total_updated = 0
    total_refreshed = 0

    # Backup before any writes
    backup_and_write(mem, mem.read_text())

    from keephive.clock import get_now

    ensure_daily()

    for batch_num, batch_facts in enumerate(batches, 1):
        if len(batches) > 1:
            console.print(
                f"  [bold]Batch {batch_num}/{len(batches)}[/bold] ({len(batch_facts)} facts)"
            )

        prompt = _build_verify_prompt(batch_facts, versions)

        try:
            with console.status("  Investigating with claude...", spinner="dots"):
                response = run_claude_pipe(
                    prompt,
                    VerifyResponse,
                    model="sonnet",
                    tools=VERIFY_TOOLS,
                    max_turns=25,
                    timeout=240,
                    verbose=verbose,
                )
        except ClaudePipeError as e:
            console.print(f"  [err]Batch {batch_num} failed: {e}[/err]")
            break

        if json_mode:
            all_verdicts.extend(v.model_dump() for v in response.verdicts)
        else:
            console.print()
            updated, refreshed = apply_verdicts(response, batch_facts, mem, today())
            total_updated += updated
            total_refreshed += refreshed

            # Persist verdicts to daily log per batch
            ts = get_now().strftime("%H:%M:%S")
            for v in response.verdicts:
                idx = v.index - 1
                if 0 <= idx < len(batch_facts):
                    fact_text = batch_facts[idx][1][:80]
                    append_to_daily(f"- [{ts}] VERIFY: {v.verdict.value}: {fact_text}")

            all_verdicts.extend(response.verdicts)

        # Ask to continue if more batches remain
        if batch_num < len(batches):
            console.print()
            if not prompt_yn(f"  Continue with batch {batch_num + 1}/{len(batches)}?"):
                console.print(f"  [dim]Stopped at batch {batch_num}.[/dim]")
                break

    if json_mode:
        print(json.dumps({"verdicts": all_verdicts}, indent=2))
        return

    if not all_verdicts:
        notify_sound(False)
        console.print(
            "[err]Verification failed: no verdicts returned. Retry: hive v  |  Check: hive doctor[/err]"
        )
        return

    notify_sound(True)

    # Aggregate summary with before/after freshness delta
    valid_count = sum(1 for v in all_verdicts if v.verdict.value == "VALID")
    stale_count = sum(1 for v in all_verdicts if v.verdict.value == "STALE")
    uncertain_count = sum(1 for v in all_verdicts if v.verdict.value == "UNCERTAIN")
    console.print(
        f"  [ok]{valid_count} VALID[/ok]  ·  "
        f"[warn]{stale_count} CORRECTED[/warn]  ·  "
        f"{uncertain_count} UNCERTAIN"
    )

    # Before/after freshness delta
    try:
        from keephive.commands.stats import _knowledge_health

        post_health = _knowledge_health()
        post_fresh_pct = post_health["fresh_pct"]
        if pre_fresh_pct is not None:
            console.print(
                f"  Memory health: {post_fresh_pct:.0f}% fresh"
                f" (was {pre_fresh_pct:.0f}% before this run)"
            )
        else:
            console.print(f"  Memory health: {post_fresh_pct:.0f}% fresh")
    except Exception:
        console.print(
            f"[dim]Updated {total_updated} fact(s), refreshed {total_refreshed} in working/memory.md[/dim]"
        )

    # Post-verify cleanup: normalize memory.md quality
    cleanup = normalize_memory(mem)
    cleanup_parts = []
    if cleanup["double_tags"]:
        cleanup_parts.append(f"{cleanup['double_tags']} double tags fixed")
    if cleanup["resolved_todos"]:
        cleanup_parts.append(f"{cleanup['resolved_todos']} resolved TODOs removed")
    if cleanup["deduped"]:
        cleanup_parts.append(f"{cleanup['deduped']} duplicates merged")
    if cleanup["malformed_prefix"]:
        cleanup_parts.append(f"{cleanup['malformed_prefix']} prefixes fixed")
    if cleanup_parts:
        console.print(f"  [dim]Cleanup: {', '.join(cleanup_parts)}[/dim]")

    console.print()
    console.print(
        "  \u2192 [dim]hive e[/dim] to review working memory  |  [dim]hive s[/dim] to check status"
    )


def apply_verdicts(
    response: VerifyResponse,
    facts: list[tuple[int, str, str]],
    mem_path: Path,
    today_str: str,
) -> tuple[int, int]:
    """Apply verification verdicts to memory.md.

    Args:
        response: VerifyResponse with verdicts.
        facts: List of (line_num, fact_text, raw_line) tuples.
        mem_path: Path to memory.md file.
        today_str: Today's date string (YYYY-MM-DD).

    Returns:
        Tuple of (updated_count, refreshed_count).
    """
    lines = mem_path.read_text().splitlines(keepends=True)
    updated = 0
    refreshed = 0

    for v in response.verdicts:
        idx = v.index - 1  # 0-based into facts list
        if idx < 0 or idx >= len(facts):
            continue

        line_num, fact_text, _raw_line = facts[idx]
        target = line_num - 1  # 0-based line index

        if target < 0 or target >= len(lines):
            continue

        # Show the fact
        console.print(f"  [dim]{fact_text}[/dim]")

        if v.verdict.value == "VALID":
            console.print(f"    [ok]VALID[/ok]: {v.reason}")
            clean = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", lines[target]).rstrip("\n")
            lines[target] = f"{clean} [verified:{today_str}]\n"
            updated += 1
        elif v.verdict.value == "STALE":
            console.print(f"    [err]STALE[/err]: {v.reason}")
            if v.correction:
                corr = v.correction.strip()
                if not corr.startswith("- "):
                    corr = f"- {corr}"
                lines[target] = f"{corr} [verified:{today_str}]\n"
                console.print(f"    [info]\u2192 Updated to: {corr}[/info]")
                updated += 1
        else:
            console.print(f"    [warn]UNCERTAIN[/warn]: {v.reason}")
            console.print("    [dim]\u2192 Refreshed (not disproven)[/dim]")
            clean = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", lines[target]).rstrip("\n")
            lines[target] = f"{clean} [verified:{today_str}]\n"
            refreshed += 1
        console.print()

        # Store verification evidence for compounding re-verification
        store_evidence(fact_text, v.verdict.value, v.reason, v.correction)

    mem_path.write_text("".join(lines))
    return updated, refreshed
