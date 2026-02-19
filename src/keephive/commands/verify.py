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
from keephive.output import console, prompt_yn
from keephive.storage import (
    backup_and_write,
    get_all_verified_facts,
    get_stale_facts,
    memory_file,
    today,
    version_context,
)

# Tools the model can use to investigate facts
VERIFY_TOOLS = ["Read", "Grep", "Glob", "WebSearch"]


def cmd_verify(args: list[str]) -> None:
    json_mode = "--json" in args
    check_mode = "--check" in args
    verbose = "--verbose" in args

    if os.environ.get("HIVE_SKIP_LLM"):
        if check_mode:
            sys.exit(0)
        console.print("[dim]Skipping verification (HIVE_SKIP_LLM=1)[/dim]")
        return

    mem = memory_file()
    if not mem.exists():
        if check_mode:
            sys.exit(0)
        console.print("[warn]No working memory to verify[/warn]")
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

    # Main path: verify ALL facts regardless of age
    all_facts = get_all_verified_facts()
    fact_count = len(all_facts)

    if fact_count == 0:
        if json_mode:
            print(json.dumps({"fact_count": 0, "message": "No verified facts found"}))
        else:
            console.print("[dim]No verified facts to check[/dim]")
        return

    console.print(f"[bold]Verifying {fact_count} fact(s) against codebase...[/bold]")
    console.print("[dim](This uses claude -p with tool access and takes 10-20 seconds)[/dim]")
    console.print()

    for _, fact_text, _ in all_facts:
        console.print(f"  [dim]* {fact_text}[/dim]")
    console.print()

    if not prompt_yn("  Verify with LLM?"):
        return

    # Build the prompt with tool-based investigation
    versions = version_context()
    facts_text = "\n".join(f"{i+1}. {fact}" for i, (_, fact, _) in enumerate(all_facts))

    prompt = f"""You are a fact-checking investigator with access to tools.

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

After investigating, provide your verdict:
- VALID: found confirming evidence (cite what you found)
- STALE: found contradicting evidence (provide corrected fact text in correction field)
- UNCERTAIN: investigated but found no evidence either way

For STALE verdicts, correction must contain the full replacement fact text."""

    try:
        with console.status("  Investigating with claude...", spinner="dots"):
            response = run_claude_pipe(
                prompt, VerifyResponse,
                model="sonnet",
                tools=VERIFY_TOOLS,
                max_turns=12,
                timeout=180,
                verbose=verbose,
            )
    except ClaudePipeError as e:
        console.print(f"[err]Verification failed: {e}[/err]")
        console.print("[dim]Check: claude -p availability, CLAUDECODE env var[/dim]")
        console.print("  -> [dim]hive e[/dim] to manually review working memory")
        return
    console.print()

    if json_mode:
        print(response.model_dump_json(indent=2))
        return

    # Backup before write
    backup_and_write(mem, mem.read_text())

    updated, refreshed = apply_verdicts(response, all_facts, mem, today())

    console.print(f"[dim]Updated {updated} fact(s), refreshed {refreshed} in working/memory.md[/dim]")

    console.print()
    console.print("  -> [dim]hive e[/dim] to review working memory  |  [dim]hive s[/dim] to check status")


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
                console.print(f"    [info]-> Updated to: {corr}[/info]")
                updated += 1
        else:
            console.print(f"    [warn]UNCERTAIN[/warn]: {v.reason}")
            console.print(f"    [dim]-> Refreshed (not disproven)[/dim]")
            clean = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", lines[target]).rstrip("\n")
            lines[target] = f"{clean} [verified:{today_str}]\n"
            refreshed += 1
        console.print()

    mem_path.write_text("".join(lines))
    return updated, refreshed
