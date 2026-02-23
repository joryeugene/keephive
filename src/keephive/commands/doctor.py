"""hive doctor: health check for keephive installation."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import timedelta
from difflib import SequenceMatcher
from pathlib import Path

from keephive.clock import get_today
from keephive.health import (
    check_anthropic_memory,
    check_daemon,
    check_soul,
)
from keephive.health import (
    check_content_drift as _check_content_drift,
)
from keephive.health import (
    check_installed_deps as _check_installed_deps,
)
from keephive.health import (
    get_installed_version as _get_installed_version,
)
from keephive.output import console, notify_sound, prompt_yn, show_hint
from keephive.storage import (
    archive_dir,
    collect_todos,
    daily_dir,
    daily_file,
    guides_dir,
    hive_dir,
    memory_file,
    prompts_dir,
    rules_file,
    safe_read_text,
    working_dir,
)


def cmd_doctor(args: list[str]) -> None:
    console.print("[bold]hive doctor[/bold]")
    console.print()
    issues = 0

    # 1. Directory structure
    console.print("[bold]Directories[/bold]")
    hd = hive_dir()
    for d in [working_dir(), daily_dir(), guides_dir(), prompts_dir(), archive_dir()]:
        short = str(d).replace(str(hd) + "/", "")
        if d.exists():
            console.print(f"  [ok]OK[/ok] {short}")
        else:
            console.print(f"  [err]MISSING[/err] {short}")
            issues += 1

    # 2. Working memory
    console.print()
    console.print("[bold]Working Memory[/bold]")
    mem = memory_file()
    if mem.exists():
        lines = len(mem.read_text().splitlines())
        console.print(f"  [ok]OK[/ok] memory.md ({lines} lines)")
    else:
        console.print("  [err]MISSING[/err] memory.md")
        issues += 1

    rf = rules_file()
    if rf.exists():
        lines = len(rf.read_text().splitlines())
        console.print(f"  [ok]OK[/ok] rules.md ({lines} lines)")
    else:
        console.print("  [warn]MISSING[/warn] rules.md (optional)")

    # 2.5. Agent Identity (KingBee)
    console.print()
    console.print("[bold]Agent Identity[/bold]")
    if check_soul():
        from keephive.storage import read_soul_summary
        soul = read_soul_summary()
        console.print(f"  [ok]OK[/ok] SOUL.md: {soul}")
    else:
        console.print("  [warn]MISSING[/warn] SOUL.md")
        issues += 1

    if check_daemon():
        from keephive.storage import get_daemon_pid
        console.print(f"  [ok]OK[/ok] KingBee daemon running (PID {get_daemon_pid()})")
    else:
        console.print("  [err]OFFLINE[/err] KingBee daemon not running")
        console.print("  [dim]  Run: hive daemon start[/dim]")
        issues += 1

    # 3. Dependencies
    console.print()
    console.print("[bold]Dependencies[/bold]")
    for cmd in ["python3", "claude"]:
        if shutil.which(cmd):
            console.print(f"  [ok]OK[/ok] {cmd}")
        else:
            console.print(f"  [err]MISSING[/err] {cmd}")
            issues += 1

    # 3.5. LLM backend
    console.print()
    console.print("[bold]LLM Backend[/bold]")
    if os.environ.get("ANTHROPIC_API_KEY"):
        console.print("  [ok]OK[/ok] ANTHROPIC_API_KEY set (direct API, works everywhere)")
    else:
        console.print("  [dim]No ANTHROPIC_API_KEY (using claude -p, terminal only)[/dim]")
        if os.environ.get("CLAUDECODE"):
            console.print(
                "  [warn]WARN[/warn] Inside Claude Code without API key: LLM features disabled"
            )
            console.print(
                "  [dim]  Set ANTHROPIC_API_KEY for audit, verify, standup, reflect[/dim]"
            )

    # 3.7. Anthropic Memory
    console.print()
    console.print("[bold]Anthropic Memory[/bold]")
    anthropic_mem = check_anthropic_memory()
    if anthropic_mem == "active":
        console.print("  [warn]ACTIVE[/warn]  Both Anthropic memory and keephive are active.")
        console.print(
            "  [dim]  keephive = structured + auditable; Anthropic = opaque + session-aware[/dim]"
        )
        console.print("  [dim]  They complement, not conflict. Use both.[/dim]")
    else:
        console.print("  [dim]Not detected (inactive or unknown)[/dim]")

    # 4. Hooks
    console.print()
    console.print("[bold]Hooks[/bold]")
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text())
            hooks = data.get("hooks", {})
            hooks_str = json.dumps(hooks)

            hook_checks = [
                ("SessionStart", "hook-sessionstart"),
                ("PreCompact", "hook-precompact"),
                ("PostToolUse", "hook-posttooluse"),
                ("UserPromptSubmit", "hook-userpromptsubmit"),
            ]
            for label, needle in hook_checks:
                if needle in hooks_str or needle in hooks_str.lower():
                    console.print(f"  [ok]OK[/ok] {label} hook")
                else:
                    console.print(f"  [err]MISSING[/err] {label} hook")
                    issues += 1
        except (json.JSONDecodeError, KeyError):
            console.print("  [err]INVALID[/err] settings.json")
            issues += 1
    else:
        console.print("  [err]MISSING[/err] settings.json")
        issues += 1

    # 4.5. MCP registration + version drift
    console.print()
    console.print("[bold]MCP Server[/bold]")
    from keephive.health import check_mcp

    if check_mcp():
        console.print("  [ok]OK[/ok] MCP server registered")
    else:
        console.print("  [err]MISSING[/err] MCP server not registered")
        issues += 1

    installed_ver = _get_installed_version()
    from keephive import __version__ as dev_ver

    if installed_ver and installed_ver != dev_ver:
        console.print(f"  [err]STALE[/err] Installed v{installed_ver}, dev v{dev_ver}")
        console.print("  [dim]  Run: uv tool install --force --no-cache .[/dim]")
        issues += 1
    elif installed_ver:
        console.print(f"  [ok]OK[/ok] Installed version matches (v{installed_ver})")

    # Content drift: same version number but stale cached wheel
    if installed_ver and installed_ver == dev_ver and _check_content_drift():
        console.print(
            "  [warn]STALE[/warn] Version matches but installed code differs (cached wheel)"
        )
        console.print("  [dim]  Run: uv tool install --force --no-cache .[/dim]")
        issues += 1

    missing_deps = _check_installed_deps()
    if missing_deps:
        console.print(f"  [err]STALE DEPS[/err] Missing in tool env: {', '.join(missing_deps)}")
        console.print("  [dim]  Run: uv tool install --force --no-cache .[/dim]")
        issues += 1

    # 5. Last activity
    console.print()
    console.print("[bold]Last Activity[/bold]")
    df = daily_file()
    if df.exists():
        text = df.read_text()
        timestamps = re.findall(r"\[(\d{2}:\d{2}:\d{2})\]", text)
        last_ts = timestamps[-1] if timestamps else "none"
        entry_count = sum(1 for line in text.splitlines() if line.startswith("- "))
        console.print(f"  Last entry: [{last_ts}]")
        console.print(f"  Today's entries: {entry_count}")
    else:
        console.print("  [warn]No daily log yet[/warn]")

    # 6. Data quality
    console.print()
    console.print("[bold]Data Quality[/bold]")
    issues_dq, findings = _data_quality_checks()
    issues += issues_dq

    # Summary
    console.print()
    if issues == 0:
        console.print("[ok]All checks passed[/ok]")
    else:
        console.print(f"[err]{issues} issue(s) found[/err]")
        show_hint("hive setup")

    console.print()
    console.print("  [dim]hive dr[/dim]  checks setup, hooks, duplicate TODOs")
    console.print("  [dim]hive rf[/dim]  reviews logs for patterns to promote")
    console.print("  [dim]hive a[/dim]   scores overall knowledge quality")

    from keephive.storage import append_to_daily, ensure_daily

    ensure_daily()
    append_to_daily(f"HEALTH: {issues} issue(s)")
    for finding in findings[:5]:
        append_to_daily(f"HEALTH: {finding}")
    console.print("  [dim]Logged. ✓[/dim]")
    notify_sound(True)


def _data_quality_checks() -> tuple[int, list[str]]:
    """Run data quality checks on TODOs. Returns (issue_count, findings)."""
    t = get_today()
    todos_all, dones_set = collect_todos()
    ot = [(d, ts, text) for d, ts, text in todos_all if text.lower() not in dones_set]
    extra_issues = 0
    findings: list[str] = []

    # Duplicate detection: LLM or deterministic
    if os.environ.get("HIVE_SKIP_LLM"):
        _detect_duplicates_deterministic(ot)
    elif not prompt_yn("  Check for semantic duplicates with LLM?"):
        console.print("  [dim]Skipped.[/dim]")
        _detect_duplicates_deterministic(ot)
    else:
        dupes = _detect_duplicates_llm(ot)
        extra_issues += dupes
        if dupes:
            findings.append(f"{dupes} duplicate group(s)")

    # Stale TODOs
    stale = [(d, text) for d, _, text in ot if d < (t - timedelta(days=7)).isoformat()]
    if stale:
        console.print(f"  [warn]WARN[/warn] {len(stale)} TODO(s) older than 7 days")
        findings.append(f"{len(stale)} stale TODO(s) >7d")
    else:
        console.print("  [ok]OK[/ok] No stale TODOs (>7d)")

    # Accumulation
    if len(ot) > 10:
        console.print(f"  [warn]WARN[/warn] {len(ot)} open TODOs. Consider consolidating.")
        findings.append(f"{len(ot)} open TODOs (high)")
    else:
        console.print(f"  [ok]OK[/ok] {len(ot)} open, {len(dones_set)} done")
        findings.append(f"{len(ot)} open, {len(dones_set)} done")

    # Hygiene: surface recent correction entries about dead code/duplicates
    df = daily_file()
    if df.exists():
        daily_lines = safe_read_text(df).splitlines()
        corrections = [
            line
            for line in daily_lines
            if "CORRECTION:" in line
            and any(
                kw in line.lower() for kw in ("dead", "duplicate", "unused", "orphan", "remove")
            )
        ]
        if corrections:
            console.print(f"  [info]{len(corrections)} hygiene correction(s) logged today[/info]")
            findings.append(f"{len(corrections)} hygiene correction(s)")

    return extra_issues, findings


def _detect_duplicates_deterministic(ot: list[tuple[str, str, str]]) -> None:
    """SequenceMatcher-based duplicate detection (fallback)."""
    texts = [text for _, _, text in ot]
    dupe_count = 0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if SequenceMatcher(None, texts[i].lower(), texts[j].lower()).ratio() > 0.7:
                dupe_count += 1

    if dupe_count:
        console.print(f"  [warn]WARN[/warn] {dupe_count} duplicate TODO pair(s)")
    else:
        console.print("  [ok]OK[/ok] No duplicate TODOs")


def _detect_duplicates_llm(ot: list[tuple[str, str, str]]) -> int:
    """LLM-based semantic duplicate detection. Returns issue count."""
    texts = [text for _, _, text in ot]

    if len(texts) < 2:
        console.print("  [ok]OK[/ok] No duplicate TODOs")
        return 0

    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.models import DoctorDuplicatesResponse

    # Also gather working memory entries for orphan detection
    from keephive.storage import memory_file as _mf

    mem = _mf()
    mem_text = mem.read_text() if mem.exists() else ""

    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))

    prompt = f"""Analyze these open TODOs for semantic duplicates and orphaned items.

Rules:
- duplicate_groups: groups of TODOs that mean the same thing (different wording, same intent).
  Include the exact TODO text in 'entries'. Suggest how to consolidate in 'suggestion'.
- orphaned_todos: TODOs that reference completed work, obsolete contexts, or things that
  no longer make sense. Only flag if clearly orphaned, not merely old.
- Be conservative. Only flag true duplicates, not merely related items.
- If no duplicates or orphans, return empty lists.

=== OPEN TODOs ===
{numbered}

=== WORKING MEMORY (for orphan context) ===
{mem_text[:2000]}"""

    try:
        response = run_claude_pipe(prompt, DoctorDuplicatesResponse, model="haiku")
    except ClaudePipeError as e:
        notify_sound(False)
        console.print(f"  [warn]LLM duplicate check failed ({e}), using fallback[/warn]")
        console.print("  [dim]Check: claude -p availability, CLAUDECODE env var[/dim]")
        _detect_duplicates_deterministic(ot)
        return 0

    return display_duplicate_results(response)


def display_duplicate_results(response) -> int:
    """Display duplicate detection results from LLM. Returns issue count."""
    issue_count = 0

    if response.duplicate_groups:
        console.print(
            f"  [warn]WARN[/warn] {len(response.duplicate_groups)} duplicate group(s) found:"
        )
        for group in response.duplicate_groups:
            for entry in group.entries:
                console.print(f"    - {entry}")
            console.print(f"    [dim]Suggestion: {group.suggestion}[/dim]")
        issue_count += len(response.duplicate_groups)
    else:
        console.print("  [ok]OK[/ok] No duplicate TODOs")

    if response.orphaned_todos:
        console.print(f"  [warn]WARN[/warn] {len(response.orphaned_todos)} orphaned TODO(s):")
        for orphan in response.orphaned_todos:
            console.print(f"    - {orphan}")
        issue_count += len(response.orphaned_todos)

    return issue_count
