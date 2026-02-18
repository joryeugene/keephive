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
from keephive.output import console
from keephive.storage import (
    backup_and_write,
    get_stale_facts,
    memory_file,
    stale_days,
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

    stale_facts = get_stale_facts()
    stale_count = len(stale_facts)

    if check_mode:
        sys.exit(1 if stale_count > 0 else 0)

    if stale_count == 0:
        if json_mode:
            print(json.dumps({"stale_count": 0, "message": "All facts are current"}))
        else:
            console.print(f"[ok]All facts are current[/ok] (verified within {stale_days()} days)")
        return

    console.print(f"[bold]Verifying {stale_count} stale fact(s) against codebase...[/bold]")
    console.print("[dim](This uses claude -p with tool access and takes 10-20 seconds)[/dim]")
    console.print()

    for _, fact_text, _ in stale_facts:
        console.print(f"  [dim]* {fact_text}[/dim]")
    console.print()

    # Build the prompt with tool-based investigation
    versions = version_context()
    facts_text = "\n".join(f"{i+1}. {fact}" for i, (_, fact, _) in enumerate(stale_facts))

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

    updated, refreshed = apply_verdicts(response, stale_facts, mem, today())

    console.print(f"[dim]Updated {updated} fact(s), refreshed {refreshed} in working/memory.md[/dim]")

    console.print()
    console.print("  -> [dim]hive e[/dim] to review working memory  |  [dim]hive s[/dim] to check status")


def apply_verdicts(
    response: VerifyResponse,
    stale_facts: list[tuple[int, str, str]],
    mem_path: Path,
    today_str: str,
) -> tuple[int, int]:
    """Apply verification verdicts to memory.md.

    Args:
        response: VerifyResponse with verdicts.
        stale_facts: List of (line_num, fact_text, raw_line) tuples.
        mem_path: Path to memory.md file.
        today_str: Today's date string (YYYY-MM-DD).

    Returns:
        Tuple of (updated_count, refreshed_count).
    """
    lines = mem_path.read_text().splitlines(keepends=True)
    updated = 0
    refreshed = 0

    for v in response.verdicts:
        idx = v.index - 1  # 0-based into stale_facts
        if idx < 0 or idx >= len(stale_facts):
            continue

        line_num, fact_text, raw_line = stale_facts[idx]
        target = line_num - 1  # 0-based line index

        if target < 0 or target >= len(lines):
            continue

        # Show the fact
        console.print(f"  [dim]{fact_text}[/dim]")

        if v.verdict.value == "VALID":
            console.print(f"    [ok]VALID[/ok]: {v.reason}")
            lines[target] = re.sub(
                r"\[verified:\d{4}-\d{2}-\d{2}\]",
                f"[verified:{today_str}]",
                lines[target],
            )
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
            lines[target] = re.sub(
                r"\[verified:\d{4}-\d{2}-\d{2}\]",
                f"[verified:{today_str}]",
                lines[target],
            )
            refreshed += 1
        console.print()

    mem_path.write_text("".join(lines))
    return updated, refreshed
