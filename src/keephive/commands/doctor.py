"""hive doctor: health check for keephive installation."""

from __future__ import annotations

import hashlib
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
from keephive.llm import available_backends, get_backend_state
from keephive.output import console, notify_sound, prompt_yn, show_hint
from keephive.platforms import platform_specs, read_hook_manifest
from keephive.settings import get_setting
from keephive.skillpack import get_record as get_skill_record
from keephive.storage import (
    archive_dir,
    collect_todos,
    daily_dir,
    daily_file,
    guides_dir,
    hive_dir,
    memory_file,
    needs_migration,
    profile_paths,
    prompts_dir,
    rules_file,
    safe_read_text,
    working_dir,
)
from keephive.telemetry import summarize as summarize_telemetry


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

    if needs_migration():
        preferred, legacy = profile_paths()
        console.print(
            f"  [warn]Legacy data root detected:[/warn] {legacy}\n"
            f"  [dim]Run 'hive setup' to migrate data to {preferred}[/dim]"
        )

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
    deps = [
        ("python3", "[ok]OK[/ok]", "[err]MISSING[/err]"),
        ("claude", "[ok]OK[/ok]", "[warn]OPTIONAL[/warn]"),
        ("gemini", "[ok]OK[/ok]", "[dim]SKIP[/dim]"),
        ("codex", "[ok]OK[/ok]", "[dim]SKIP[/dim]"),
    ]
    for cmd, ok_label, miss_label in deps:
        if shutil.which(cmd):
            console.print(f"  {ok_label} {cmd}")
        else:
            label = miss_label
            console.print(f"  {label} {cmd}")
            if cmd in {"python3"}:
                issues += 1

    # 3.5. LLM backend
    console.print()
    console.print("[bold]LLM Backend[/bold]")
    state = get_backend_state()
    persistent_pref = get_setting("llm_backend") or "auto"
    env_override = os.environ.get("HIVE_LLM_BACKEND")
    if env_override:
        console.print(f"  Config: settings={persistent_pref}, env override={env_override}")
    else:
        console.print(f"  Config: settings={persistent_pref}")

    if state:
        console.print(
            f"  Last selection: [cyan]{state.get('selected', '?')}[/cyan] "
            f"(source: {state.get('source', 'auto')}, reason: {state.get('reason', '')})"
        )
    else:
        console.print("  [dim]No LLM command has run yet (state unknown).[/dim]")

    healthy_backend_found = False
    for backend in available_backends():
        available, reason = backend.detect()
        marker = "[ok]OK[/ok]" if available else "[warn]WARN[/warn]"
        suffix = " (supports tools)" if backend.supports_tools else ""
        console.print(f"  {marker} {backend.name}{suffix} — {reason or 'available'}")
        if available and backend.name != "none":
            healthy_backend_found = True

    if not healthy_backend_found:
        console.print(
            "  [err]No operational backend detected.[/err] "
            "Install the claude CLI or set GEMINI_API_KEY / OPENAI_API_KEY."
        )
        issues += 1

    if os.environ.get("CLAUDECODE") and not os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            "  [warn]Inside Claude Code without ANTHROPIC_API_KEY: "
            "LLM features disabled in-session."
        )

    # 3.6. Platform integrations (skills + hooks)
    console.print()
    console.print("[bold]Platform Integrations[/bold]")
    specs = platform_specs()
    hook_manifest = read_hook_manifest()
    for slug, spec in specs.items():
        status = "[ok]OK[/ok]" if spec.detected else "[dim]SKIP[/dim]"
        console.print(f"  {status} {spec.title}: {spec.detection_reason}")

        skill_path = spec.skill_path
        skill_record = get_skill_record(slug)
        if skill_path.exists():
            console.print(f"    [ok]Skill[/ok] keephive-helper → {skill_path}")
        else:
            console.print("    [warn]Skill missing[/warn] keephive-helper not installed")
            issues += 1 if spec.detected else 0
        if skill_record:
            console.print(
                f"    [dim]Skill hash:[/dim] {skill_record.get('hash', '?')} "
                f"(updated {skill_record.get('updated_at', 'unknown')})"
            )

        hook_entry = hook_manifest.get(slug)
        if hook_entry:
            scripts = hook_entry.get("scripts", {})
            missing_scripts = []
            for name, expected_hash in scripts.items():
                target = spec.hooks_dir / name
                if not target.exists():
                    missing_scripts.append(name)
                    continue
                actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual_hash != expected_hash:
                    missing_scripts.append(f"{name}*")
            if missing_scripts:
                console.print(
                    f"    [warn]Hooks drift[/warn] {', '.join(missing_scripts)} "
                    f"(run 'hive setup --{slug}-hooks=yes')"
                )
                issues += 1
            else:
                console.print(f"    [ok]Hooks[/ok] scripts synced at {spec.hooks_dir}")

            cfg = Path(hook_entry.get("config_path", ""))
            if cfg:
                state = "present" if cfg.exists() else "missing"
                console.print(f"    [dim]Config:[/dim] {cfg} {state}")
                if spec.detected and not cfg.exists():
                    issues += 1
        else:
            console.print(
                f"    [warn]Hooks missing[/warn] run 'hive setup --platform {slug}' to install"
            )
            if spec.detected:
                issues += 1

        telemetry_stats = summarize_telemetry(slug)
        total_events = telemetry_stats.get("total", 0)
        latest = telemetry_stats.get("latest")
        if total_events:
            latest_event = ""
            if isinstance(latest, dict):
                event_name = latest.get("event", "unknown")
                ts = latest.get("timestamp", "recent")
                latest_event = f" · latest {event_name} @ {ts}"
            console.print(f"    [ok]Telemetry[/ok] {total_events} event(s){latest_event}")
        else:
            console.print("    [warn]Telemetry[/warn] no events captured yet")
            if spec.detected:
                console.print(
                    f"      [dim]Tip:[/dim] open a session or run "
                    f"'keephive setup --{slug}-hooks=yes' to seed data."
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

    # 3.8. Authentication
    console.print()
    console.print("[bold]Authentication[/bold]")
    auth_checks = [
        ("Gemini", Path.home() / ".gemini" / "auth.json"),
        ("Codex", Path.home() / ".codex" / "auth.json"),
    ]
    for label, path in auth_checks:
        if path.exists():
            console.print(f"  [ok]OK[/ok] {label} auth detected at {path}")
        else:
            console.print(f"  [dim]SKIP[/dim] {label} auth not found (optional)")

    # 4. Hooks
    console.print()
    console.print("[bold]Hooks[/bold]")
    for slug, spec in specs.items():
        if not spec.detected:
            continue

        settings_path = spec.config_path
        if settings_path.exists():
            try:
                # Handle both JSON and plain text (for simple config checks)
                content = settings_path.read_text()
                hooks_str = content

                if slug == "claude":
                    hook_checks = [
                        ("SessionStart", "hook-sessionstart"),
                        ("PreCompact", "hook-precompact"),
                        ("PostToolUse", "hook-posttooluse"),
                        ("UserPromptSubmit", "hook-userpromptsubmit"),
                    ]
                elif slug == "gemini":
                    hook_checks = [
                        ("SessionStart", "session_start.py"),
                        ("BeforeTool", "before_tool.py"),
                        ("AfterModel", "after_model.py"),
                    ]
                else:
                    hook_checks = []

                for label, needle in hook_checks:
                    if needle in hooks_str or needle in hooks_str.lower():
                        console.print(f"  [ok]OK[/ok] {spec.title} {label} hook")
                    else:
                        console.print(f"  [err]MISSING[/err] {spec.title} {label} hook")
                        issues += 1
            except Exception as e:
                console.print(f"  [err]ERROR[/err] reading {spec.title} config: {e}")
                issues += 1
        else:
            # Only count as issue for Claude since it's the primary platform
            marker = "[err]MISSING[/err]" if slug == "claude" else "[dim]SKIP[/dim]"
            console.print(f"  {marker} {spec.title} config: {settings_path}")
            if slug == "claude":
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

    # 5.5. Unexpected files in hive root
    console.print()
    console.print("[bold]Hive Root[/bold]")
    unexpected = _check_unexpected_hive_entries(hd)
    if unexpected:
        console.print(f"  [warn]WARN[/warn] {len(unexpected)} unexpected file(s) in hive root:")
        for name in sorted(unexpected):
            console.print(f"    [dim]{name}[/dim]  — review and remove if stale")
        issues += 1
    else:
        console.print("  [ok]OK[/ok] No unexpected files in hive root")

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
        console.print(f"  [warn]WARN[/warn] {len(stale)} TODO(s) older than 7 days → hive todo")
        findings.append(f"{len(stale)} stale TODO(s) >7d")
    else:
        console.print("  [ok]OK[/ok] No stale TODOs (>7d)")

    # Accumulation
    if len(ot) > 10:
        console.print(f"  [warn]WARN[/warn] {len(ot)} open TODOs → hive todo")
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
            console.print(
                f"  [info]{len(corrections)} hygiene correction(s) logged today → hive log[/info]"
            )
            findings.append(f"{len(corrections)} hygiene correction(s)")

    return extra_issues, findings


_EXPECTED_HIVE_ENTRIES = {
    # Directories (top-level children of hive_dir)
    "daily",
    "archive",
    "working",
    "knowledge",
    "wander",
    "telemetry",
    # Core files
    "memory.md",
    "SOUL.md",
    "TODO.md",
    "rules.md",
    "daemon.json",
    "daemon.log",
    # Counters
    ".prompt-counter",
    ".tool-counter",
    ".stop-counter",
    # Stats + state
    ".stats.json",
    ".stats.lock",
    ".daemon-state.json",
    ".daemon.pid",
    ".settings.json",
    ".index.json",
    ".fts.db",
    ".recall-stats.json",
    # Queues + pending
    ".pending-facts.md",
    ".pending-rules.md",
    ".pending-improvements.json",
    ".dismissed-improvements.json",
    ".kb-queue.md",
    ".ui-queue",
    # Privacy + flags
    ".llm-paused",
    ".force-cli",
    ".auto-improve-trusted",
    # Timestamps
    ".auto-triage.stamp",
    ".session-launched",
    ".last-analyze.json",
    ".last-reflect-date",
    # Daemon + tasks
    ".custom-tasks.json",
    ".wander-seeds.json",
    # Hooks
    ".hook-debug.log",
    # Snapshot (created by hive checkup --snapshot)
    ".git",
    ".gitignore",
}

# Dynamic filenames that use variable suffixes
_EXPECTED_HIVE_PREFIXES = (
    ".loop-",  # .loop-{id}.json, .loop-done-{id}, .loop-prompt-{id}.txt
    ".ui-queue-",  # .ui-queue-{project}
)


def _check_unexpected_hive_entries(hd: Path) -> list[str]:
    """Return names of top-level hive dir entries not in the expected set."""
    unexpected = []
    for entry in hd.iterdir():
        name = entry.name
        if name in _EXPECTED_HIVE_ENTRIES:
            continue
        if any(name.startswith(pfx) for pfx in _EXPECTED_HIVE_PREFIXES):
            continue
        unexpected.append(name)
    return unexpected


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
