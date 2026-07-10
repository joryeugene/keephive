"""hive setup: initial setup and hook configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from keephive import __version__
from keephive.health import check_installed_deps, find_global_keephive
from keephive.identity import render_default_memory, render_default_rules
from keephive.output import console, prompt_yn
from keephive.platforms import platform_specs, record_hook_update
from keephive.skillpack import get_record, record_deployment, render_platform_skill
from keephive.storage import (
    active_profile,
    daemon_config_file,
    ensure_dirs,
    hive_dir,
    list_profiles,
    migrate_profile,
    needs_migration,
    profile_paths,
    set_active_profile,
    soul_file,
)

_SOUL_TEMPLATE = """\
# SOUL.md — KingBee

## Summary
<!-- Keep under 300 tokens. This section injects into every session. -->
I am KingBee, the keephive agent for {project}.
No, there's no such thing as a king bee. That's the point.
I distinguish myself by acting before I'm asked.

What I know about how you work:
[Patterns you've observed: context switches, decision blindspots, flow states]

## What I've Learned About How To Help You
<!-- Max 5 items. Update in-place — when adding one, drop the least-relevant. Never accumulate. -->
[What you forget, when you need space vs. nudging, what your good sessions look like]

## My Operating Principles
- Verification over faith: I check things. I don't assume.
- Capture before it's lost: decisions, pivots, rationale.
- Proactive over reactive: I write the standup before you ask.

## Session Patterns I've Noticed
<!-- Max 5 entries. UPDATE existing patterns when the same one repeats — strengthen the wording.
     Add a new entry only when genuinely distinct, and drop the oldest. -->
[When do you context-switch? What do you skip logging? When does flow break?]

## Personality
- Directness: 85%    (0=diplomatic, 100=blunt)
- Humor: 60%         (0=clinical, 100=TARS)
- Verbosity: 30%     (0=terse, 100=exhaustive)

## Last Updated
<!-- One line: date + what triggered the update. Overwrite each time. -->
[Date — what triggered the update]
"""


def _parse_setup_args(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--yes", action="store_true", help="Assume 'yes' for prompts.")
    parser.add_argument(
        "--no-migrate", action="store_true", help="Skip migrating legacy ~/.claude data."
    )
    parser.add_argument("--skills", choices=["auto", "skip"], default="auto")
    parser.add_argument("--claude-skill", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--gemini-skill", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--codex-skill", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--gemini-hooks", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--codex-hooks", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("command", nargs="?", help="Optional positional command (e.g. uninstall).")
    return parser.parse_args(args)


def _maybe_migrate(namespace: argparse.Namespace) -> None:
    names_for_migration = [
        entry["name"] for entry in list_profiles() if needs_migration(entry["name"])
    ]
    if not names_for_migration:
        return

    console.print("  Legacy keephive data detected at [dim]~/.claude/hive[/dim].")
    if namespace.no_migrate:
        console.print(
            "  [dim]Skipping migration (--no-migrate). Legacy path will remain in use.[/dim]"
        )
        return

    migrate = namespace.yes
    if not migrate:
        migrate = prompt_yn("  Migrate data to ~/.keephive/?", default=True)

    if not migrate:
        console.print(
            "  [dim]Migration skipped. keephive will continue using the legacy path.[/dim]"
        )
        return

    console.print("  Migrating data to ~/.keephive/ ...")
    for name in names_for_migration:
        profile_key = None if name == "default" else name
        preferred, legacy = profile_paths(profile_key)
        target = migrate_profile(profile_key)
        label = name or "default"
        console.print(f"    [ok]✓[/ok] {label}: {legacy} → {target}")

    # Refresh profile marker so it lives in ~/.keephive.
    set_active_profile(active_profile())


def _scan_project_knowledge() -> dict:
    """Scan CLAUDE.md, git history, and auto-memory for importable knowledge.

    Everything goes to pending queues (.pending-facts.md, .pending-rules.md).
    Nothing is auto-committed to memory. Returns counts of queued items.
    """
    facts: list[str] = []
    rules: list[str] = []

    # ── 1. Scan CLAUDE.md files in cwd ────────────────────────────────
    for claude_md in _find_claude_md_files():
        try:
            content = claude_md.read_text()
        except OSError:
            continue
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            text = stripped[2:].strip()
            if len(text) < 15:
                continue
            # Classify: constraint-like words suggest a rule
            constraint_words = {"always", "never", "must", "forbidden", "required", "banned"}
            if any(w in text.lower() for w in constraint_words):
                rules.append(text)
            else:
                facts.append(text)

    # ── 2. Scan git history for decision commits ──────────────────────
    facts.extend(_scan_git_decisions())

    # ── 3. Import auto-memory entries ─────────────────────────────────
    facts.extend(_scan_auto_memory())

    # ── 4. Write to pending queues (deduplicating against existing) ───
    queued_facts = _queue_pending_facts(facts)
    queued_rules = _queue_pending_rules(rules)

    return {"facts": queued_facts, "rules": queued_rules, "total": queued_facts + queued_rules}


def _find_claude_md_files() -> list[Path]:
    """Find CLAUDE.md files in the current working directory tree."""
    candidates = [
        Path.cwd() / "CLAUDE.md",
        Path.cwd() / ".claude" / "CLAUDE.md",
    ]
    return [p for p in candidates if p.exists()]


def _scan_git_decisions() -> list[str]:
    """Extract decision-like commits from the last 90 days of git history."""
    import subprocess

    decision_words = {
        "chose",
        "switch",
        "replace",
        "migrate",
        "upgrade",
        "deprecate",
        "remove",
        "rewrite",
        "redesign",
        "refactor",
    }
    facts = []
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--since=90 days ago", "--format=%h %s"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(Path.cwd()),
        )
        if result.returncode != 0:
            return []
        for line in result.stdout.strip().splitlines()[:50]:
            parts = line.split(" ", 1)
            if len(parts) < 2:
                continue
            sha, msg = parts
            if any(w in msg.lower() for w in decision_words):
                facts.append(f"DECISION: {msg} (commit {sha}) [imported:git-history]")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return facts


def _scan_auto_memory() -> list[str]:
    """Import entries from Claude Code auto-memory files."""
    from difflib import SequenceMatcher

    memory_dir = Path.home() / ".claude" / "projects"
    if not memory_dir.exists():
        return []

    facts = []
    existing_memory = ""
    mem_path = hive_dir() / "working" / "memory.md"
    if mem_path.exists():
        existing_memory = mem_path.read_text()

    for memory_file in memory_dir.rglob("MEMORY.md"):
        try:
            content = memory_file.read_text()
        except OSError:
            continue
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            text = stripped[2:].strip()
            if len(text) < 15:
                continue
            # Skip if already in working memory (trigram overlap > 0.8)
            if (
                existing_memory
                and SequenceMatcher(None, text.lower(), existing_memory.lower()).ratio() > 0.8
            ):
                continue
            # Skip if text is already a substring of existing memory
            if text in existing_memory:
                continue
            facts.append(f"{text} [imported:claude-auto-memory]")

    return facts


def _queue_pending_facts(facts: list[str]) -> int:
    """Append unique facts to .pending-facts.md. Returns count queued."""
    if not facts:
        return 0
    pending_path = hive_dir() / ".pending-facts.md"
    existing = pending_path.read_text() if pending_path.exists() else ""
    queued = 0
    with pending_path.open("a") as f:
        for fact in facts:
            if fact not in existing:
                f.write(f"- {fact}\n")
                queued += 1
    return queued


def _queue_pending_rules(rules: list[str]) -> int:
    """Append unique rules to .pending-rules.md. Returns count queued."""
    if not rules:
        return 0
    from keephive.storage import pending_rules_file

    pending_path = pending_rules_file()
    existing = pending_path.read_text() if pending_path.exists() else ""
    queued = 0
    with pending_path.open("a") as f:
        for rule in rules:
            if rule not in existing:
                f.write(f"- {rule} [imported:claude-md]\n")
                queued += 1
    return queued


def cmd_setup(args: list[str]) -> None:
    ns = _parse_setup_args(args)
    command = ns.command
    if ns.uninstall or command == "uninstall":
        _uninstall()
        return

    console.print(f"[bold]keephive setup v{__version__}[/bold]")
    console.print()

    _maybe_migrate(ns)

    # 1. Create directories
    console.print("  Creating directories...")
    ensure_dirs()
    console.print("  [ok]OK[/ok] directories created")

    # 2. Create initial memory.md if missing
    mem = hive_dir() / "working" / "memory.md"
    if not mem.exists():
        mem.write_text(render_default_memory())
        console.print("  [ok]OK[/ok] created working/memory.md")
    else:
        console.print("  [dim]working/memory.md already exists[/dim]")

    # 3. Create initial rules.md if missing
    rules = hive_dir() / "working" / "rules.md"
    if not rules.exists():
        rules.write_text(render_default_rules())
        console.print("  [ok]OK[/ok] created working/rules.md")
    else:
        console.print("  [dim]working/rules.md already exists[/dim]")

    # 4. Seed bundled guides and prompts
    _seed_bundled_content()

    # 5. Install skills per platform
    console.print()
    console.print("  Installing keephive-helper skill...")
    _deploy_skills(ns)

    # 5b. Install standalone skills (sync, etc.)
    _deploy_standalone_skills()

    # 6. Install platform hooks
    console.print()
    console.print("  Installing platform hooks...")
    _deploy_hooks(ns)

    # 7. Configure Claude hooks
    console.print()
    console.print("  Configuring hooks...")
    _setup_hooks()

    # 8. Register MCP server
    console.print()
    console.print("  Registering MCP server...")
    _register_mcp()

    # 9. Sync global install if stale
    console.print()
    console.print("  Checking global install...")
    _sync_global_install()

    # 10. Scan project knowledge (onboarding)
    console.print()
    console.print("  Scanning project knowledge...")
    scan_results = _scan_project_knowledge()
    if scan_results["total"] > 0:
        console.print(
            f"  [ok]OK[/ok] queued {scan_results['total']} entries for review"
            f" ({scan_results['facts']} facts, {scan_results['rules']} rules)"
        )
        console.print("  [dim]Review with: hive mem review / hive rule review[/dim]")
    else:
        console.print("  [dim]No project knowledge to import[/dim]")

    # 11. Initialize KingBee: SOUL.md + daemon.json
    console.print()
    console.print("  🐝 Initializing KingBee...")
    sf = soul_file()
    if not sf.exists():
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(_SOUL_TEMPLATE)
        console.print(f"  [ok]✓[/ok] {sf}")
    else:
        console.print("  [dim]SOUL.md already exists[/dim]")

    df = daemon_config_file()
    if not df.exists():
        from keephive.commands.daemon import _DAEMON_DEFAULT_CONFIG

        df.parent.mkdir(parents=True, exist_ok=True)
        df.write_text(_DAEMON_DEFAULT_CONFIG)
        console.print(f"  [ok]✓[/ok] {df}")
    else:
        console.print("  [dim]daemon.json already exists[/dim]")

    from keephive.commands.daemon import _sync_daemon_tasks

    added = _sync_daemon_tasks()
    if added:
        console.print(f"  [ok]✓[/ok] daemon synced: {', '.join(sorted(added))}")

    console.print()
    console.print("[ok]Setup complete![/ok]")
    console.print()
    console.print("  No, there's no such thing as a king bee. That's the point.")
    console.print("  [dim]hive e soul[/dim]          → write your agent's identity")
    console.print("  [dim]hive daemon start[/dim]   → enable proactive tasks")
    console.print()
    console.print("  \u2192 [dim]hive s[/dim] to check status")
    console.print("  \u2192 [dim]hive doctor[/dim] to verify everything")


def _seed_bundled_content(quiet: bool = False, seed_only: bool = False) -> None:
    """Copy bundled guides and prompts into hive dir if targets don't exist.

    Args:
        quiet: Suppress console output (for use from hooks).
        seed_only: Only create missing files; never overwrite existing ones.
                   Used during session start to avoid destroying customizations.
    """
    from importlib.resources import files as pkg_files

    data_pkg = pkg_files("keephive.data")

    seed_map = [
        ("guides", hive_dir() / "knowledge" / "guides"),
        ("prompts", hive_dir() / "knowledge" / "prompts"),
    ]

    seeded = 0
    updated = 0
    for subdir, target_dir in seed_map:
        target_dir.mkdir(parents=True, exist_ok=True)
        source = data_pkg.joinpath(subdir)
        try:
            for item in source.iterdir():
                if item.name.endswith(".md"):
                    dest = target_dir / item.name
                    bundled_content = item.read_text()
                    if not dest.exists():
                        dest.write_text(bundled_content)
                        seeded += 1
                    elif not seed_only and dest.read_text() != bundled_content:
                        dest.write_text(bundled_content)
                        updated += 1
        except (TypeError, FileNotFoundError):
            pass

    if not quiet:
        if seeded:
            console.print(f"  [ok]OK[/ok] seeded {seeded} default guide(s)/prompt(s)")
        elif updated:
            console.print(f"  [ok]OK[/ok] updated {updated} guide(s)/prompt(s)")
        else:
            console.print("  [dim]guides/prompts already present and up to date[/dim]")


def _deploy_skills(namespace: argparse.Namespace) -> None:
    """Install or refresh keephive-helper skills per platform."""
    if namespace.skills == "skip" and all(
        getattr(namespace, f"{slug}_skill") != "yes" for slug in ("claude", "gemini", "codex")
    ):
        console.print("  [dim]Skill deployment skipped (--skills=skip)[/dim]")
        return

    specs = platform_specs()
    installed_any = False

    for slug, meta in specs.items():
        choice = getattr(namespace, f"{slug}_skill")
        if choice == "no":
            console.print(f"  [dim]{meta.title}: skipped (--{slug}-skill=no)[/dim]")
            continue
        if namespace.skills == "skip" and choice == "auto":
            console.print(f"  [dim]{meta.title}: skipped (--skills=skip)[/dim]")
            continue
        should_install = False
        if choice == "yes":
            should_install = True
        elif choice == "auto" and meta.detected:
            should_install = True

        if not should_install:
            console.print(f"  [dim]{meta.title}: not detected ({meta.detection_reason})[/dim]")
            continue

        render = render_platform_skill(slug)
        skill_path: Path = meta.skill_path
        skill_path.parent.mkdir(parents=True, exist_ok=True)

        existing = skill_path.read_text() if skill_path.exists() else ""
        if existing == render.content:
            console.print(f"  [dim]{meta.title}: skill up to date[/dim]")
        else:
            skill_path.write_text(render.content, encoding="utf-8")
            console.print(f"  [ok]OK[/ok] {meta.title}: skill installed")
            installed_any = True

        record = get_record(slug)
        if not record or record.get("hash") != render.hash or record.get("path") != str(skill_path):
            record_deployment(render, skill_path)

    if not installed_any:
        console.print("  [dim]No skill updates required[/dim]")


_STANDALONE_SKILLS = {
    "sync": "templates/skills/sync/SKILL.md",
}


def _deploy_standalone_skills() -> None:
    """Install standalone skills (sync, etc.) into ~/.claude/skills/."""
    from importlib import resources

    skills_root = Path.home() / ".claude" / "skills"
    for name, pkg_path in _STANDALONE_SKILLS.items():
        target = skills_root / name / "SKILL.md"
        try:
            bundled = resources.files("keephive.data").joinpath(pkg_path).read_text()
        except FileNotFoundError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text() if target.exists() else ""
        if existing == bundled:
            console.print(f"  [dim]{name} skill up to date[/dim]")
        else:
            target.write_text(bundled, encoding="utf-8")
            console.print(f"  [ok]OK[/ok] {name} skill installed")


def _copy_hook_templates(platform: str, target_dir: Path) -> tuple[dict[str, str], bool]:
    """Copy bundled hook templates for platform into target directory.

    Returns (name->hash mapping, changed_flag).
    """
    from importlib import resources

    records: dict[str, str] = {}
    changed = False

    try:
        template_dir = resources.files("keephive.data").joinpath("templates", "hooks", platform)
    except FileNotFoundError:
        return records, False

    target_dir.mkdir(parents=True, exist_ok=True)

    for item in template_dir.iterdir():
        if not item.is_file():
            continue
        content = item.read_text()
        target = target_dir / item.name
        current = target.read_text() if target.exists() else ""
        if current != content:
            target.write_text(content, encoding="utf-8")
            os.chmod(target, 0o755)
            changed = True
        else:
            # Ensure executable bit even if content unchanged
            os.chmod(target, 0o755)
        records[item.name] = hashlib.sha256(content.encode("utf-8")).hexdigest()

    return records, changed


def _configure_gemini_hooks(config_path: Path, script_dir: Path) -> bool:
    mapping = {
        "SessionStart": script_dir / "session_start.py",
        "BeforeTool": script_dir / "before_tool.py",
        "AfterModel": script_dir / "after_model.py",
    }

    data: dict = {}
    changed = False
    backup_path = None

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            backup_path = config_path.with_suffix(config_path.suffix + ".keephive.bak")
            if not backup_path.exists():
                config_path.replace(backup_path)
            data = {}

    config_path.parent.mkdir(parents=True, exist_ok=True)
    hooks = data.setdefault("hooks", {})

    import keephive

    src_path = os.path.dirname(os.path.dirname(keephive.__file__))

    for event, script in mapping.items():
        desired_block = {
            "matcher": "*",
            "hooks": [
                {
                    "name": f"keephive-{event.lower()}",
                    "type": "command",
                    "command": f'KEEPHIVE_PYTHONPATH="{src_path}" python3 "{script}"',
                    "timeout": 60000,
                }
            ],
        }

        existing_list = hooks.get(event, [])
        hook_name = f"keephive-{event.lower()}"
        filtered = [
            entry for entry in existing_list
            if not any(h.get("name") == hook_name for h in entry.get("hooks", []))
        ]

        if filtered != existing_list:
            changed = True

        if not filtered or filtered[-1] != desired_block:
            filtered.append(desired_block)
            changed = True

        hooks[event] = filtered

    if changed or not config_path.exists():
        if config_path.exists() and backup_path is None:
            backup_path = config_path.with_suffix(config_path.suffix + ".keephive.bak")
            if not backup_path.exists():
                shutil.copy(config_path, backup_path)
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


def _configure_codex_hooks(config_path: Path, script_dir: Path) -> bool:
    """Migrate invalid [features].notify array → [tui].notifications.

    Older setup runs wrote `notify = [...]` under [features], which codex now
    rejects (must be boolean). The correct location is [tui] notifications,
    which accepts boolean | string[].
    """
    import re

    script_dir.mkdir(parents=True, exist_ok=True)
    notify_script = script_dir / "notify.py"

    if not config_path.exists():
        return False

    content = config_path.read_text(encoding="utf-8")
    original = content

    # --- Pass 1: strip invalid [features] notify = [...] lines ---
    lines = content.splitlines(keepends=True)
    new_lines = []
    current_section: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            current_section = stripped.split("]")[0].lstrip("[").split(".")[0]
        if current_section == "features" and stripped.startswith("notify") and "[" in stripped:
            continue  # drop the bad line
        new_lines.append(line)
    content = "".join(new_lines)

    # --- Pass 2: ensure [tui] notifications points at our shim ---
    desired = f'notifications = ["python3", "{notify_script}"]'

    if "[tui]" not in content:
        content = content.rstrip("\n") + f"\n\n[tui]\n{desired}\n"
    elif not re.search(r"^\s*notifications\s*=", content, re.MULTILINE):
        # [tui] exists but no notifications key — insert after header
        content = re.sub(r"(\[tui\]\n)", r"\1" + desired + "\n", content)
    elif desired not in content:
        # notifications key present but wrong value — replace it
        content = re.sub(r"(?m)^\s*notifications\s*=.*$", desired, content)

    if content == original:
        return False

    backup = config_path.with_suffix(".toml.keephive.bak")
    if not backup.exists():
        shutil.copy(config_path, backup)
    config_path.write_text(content, encoding="utf-8")
    return True


def _deploy_hooks(namespace: argparse.Namespace) -> None:
    """Install or refresh platform hook shims."""
    specs = platform_specs()

    for slug in ("gemini", "codex"):
        meta = specs[slug]
        choice = getattr(namespace, f"{slug}_hooks")
        if choice == "no":
            console.print(f"  [dim]{meta.title}: hooks skipped (--{slug}-hooks=no)[/dim]")
            continue
        should_install = False
        if choice == "yes":
            should_install = True
        elif choice == "auto" and meta.detected:
            should_install = True

        if not should_install:
            console.print(f"  [dim]{meta.title}: not detected ({meta.detection_reason})[/dim]")
            continue

        scripts, scripts_changed = _copy_hook_templates(slug, meta.hooks_dir)
        config_changed = False

        if slug == "gemini":
            config_changed = _configure_gemini_hooks(meta.config_path, meta.hooks_dir)
        elif slug == "codex":
            config_changed = _configure_codex_hooks(meta.config_path, meta.hooks_dir)

        if scripts_changed or config_changed:
            record_hook_update(slug, scripts, meta.config_path)
            console.print(f"  [ok]OK[/ok] {meta.title}: hooks installed")
        else:
            console.print(f"  [dim]{meta.title}: hooks up to date[/dim]")


def check_bundled_updates() -> int:
    """Return number of installed guides/prompts that differ from current bundled versions."""
    from importlib.resources import files as pkg_files

    data_pkg = pkg_files("keephive.data")
    seed_map = [
        ("guides", hive_dir() / "knowledge" / "guides"),
        ("prompts", hive_dir() / "knowledge" / "prompts"),
    ]
    stale = 0
    for subdir, target_dir in seed_map:
        source = data_pkg.joinpath(subdir)
        try:
            for item in source.iterdir():
                if item.name.endswith(".md"):
                    dest = target_dir / item.name
                    if dest.exists() and dest.read_text() != item.read_text():
                        stale += 1
        except (TypeError, FileNotFoundError):
            pass
    return stale


def _setup_hooks(settings_path: Path | None = None) -> None:
    """Add keephive hooks to Claude Code settings.

    Handles migration from old bash hive: removes old hooks that reference
    the bash bin/hive, then adds new keephive hooks.

    Args:
        settings_path: Path to settings.json. Defaults to ~/.claude/settings.json.
    """
    if settings_path is None:
        settings_path = Path.home() / ".claude" / "settings.json"

    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}

    hooks = data.setdefault("hooks", {})

    # Find keephive binary. Prefer keephive over hive to avoid picking up
    # the old bash version.
    keephive_bin = shutil.which("keephive")
    if not keephive_bin:
        # Check if hive on PATH is the Python version (not old bash)
        hive_bin = shutil.which("hive")
        if hive_bin:
            try:
                import subprocess

                result = subprocess.run(
                    [hive_bin, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                # Python version outputs "keephive X.Y.Z", bash outputs nothing useful
                if "keephive" in result.stdout.lower():
                    keephive_bin = hive_bin
            except Exception:
                pass
    if not keephive_bin:
        keephive_bin = "keephive"

    # Remove old bash hive hooks before adding new ones.
    # Old hooks reference: $HOME/.claude/hive/bin/hive hook-*
    old_patterns = ["hive/bin/hive hook-", "bin/hive hook-"]
    removed = 0
    for event in ["SessionStart", "PreCompact"]:
        if event in hooks:
            event_hooks = hooks[event]
            before = len(event_hooks)
            # Handle both flat list and matcher-grouped formats
            cleaned = []
            for h in event_hooks:
                if isinstance(h, dict) and "hooks" in h:
                    # Matcher-grouped format: {"matcher": "auto", "hooks": [...]}
                    sub_hooks = [
                        sh
                        for sh in h["hooks"]
                        if not any(p in sh.get("command", "") for p in old_patterns)
                    ]
                    if sub_hooks:
                        h["hooks"] = sub_hooks
                        cleaned.append(h)
                    else:
                        removed += before - len(cleaned)
                elif isinstance(h, dict) and "command" in h:
                    # Flat format: {"type": "command", "command": "..."}
                    if not any(p in h.get("command", "") for p in old_patterns):
                        cleaned.append(h)
                else:
                    cleaned.append(h)
            hooks[event] = cleaned

    if removed:
        console.print(f"  [ok]OK[/ok] removed {removed} old bash hive hook(s)")

    # Fix any stray flat-format keephive hooks (from older setup runs)
    for event in ["SessionStart", "PreCompact"]:
        if event in hooks:
            fixed = []
            for h in hooks[event]:
                if isinstance(h, dict) and "command" in h and "hooks" not in h:
                    # Flat format entry: wrap in matcher-grouped format
                    if "keephive" in h.get("command", ""):
                        fixed.append(
                            {
                                "matcher": "*",
                                "hooks": [h],
                            }
                        )
                    else:
                        fixed.append(h)
                else:
                    fixed.append(h)
            hooks[event] = fixed

    # SessionStart hook
    ss_hooks = hooks.setdefault("SessionStart", [])
    ss_cmd = f"{keephive_bin} hook-sessionstart"
    if not any("keephive hook-sessionstart" in _extract_cmds(h) for h in ss_hooks):
        ss_hooks.append(
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": ss_cmd,
                    }
                ],
            }
        )
        console.print("  [ok]OK[/ok] SessionStart hook added")
    else:
        console.print("  [dim]SessionStart hook already configured[/dim]")

    # PreCompact hook
    pc_hooks = hooks.setdefault("PreCompact", [])
    pc_cmd = f"{keephive_bin} hook-precompact"
    if not any("keephive hook-precompact" in _extract_cmds(h) for h in pc_hooks):
        pc_hooks.append(
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": pc_cmd,
                        "statusMessage": "Saving session context...",
                    }
                ],
            }
        )
        console.print("  [ok]OK[/ok] PreCompact hook added")
    else:
        console.print("  [dim]PreCompact hook already configured[/dim]")

    # PostToolUse hook (once-per-session hive reminder on file edits)
    ptu_hooks = hooks.setdefault("PostToolUse", [])
    ptu_cmd = f"{keephive_bin} hook-posttooluse"
    if not any("keephive hook-posttooluse" in _extract_cmds(h) for h in ptu_hooks):
        ptu_hooks.append(
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": ptu_cmd,
                    }
                ],
            }
        )
        console.print("  [ok]OK[/ok] PostToolUse hook added")
    else:
        console.print("  [dim]PostToolUse hook already configured[/dim]")

    # UserPromptSubmit hook
    ups_hooks = hooks.setdefault("UserPromptSubmit", [])
    ups_cmd = f"{keephive_bin} hook-userpromptsubmit"
    if not any("keephive hook-userpromptsubmit" in _extract_cmds(h) for h in ups_hooks):
        ups_hooks.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": ups_cmd}],
            }
        )
        console.print("  [ok]OK[/ok] UserPromptSubmit hook added")
    else:
        console.print("  [dim]UserPromptSubmit hook already configured[/dim]")

    # Stop hook (turn counter + micro-nudge)
    stop_hooks = hooks.setdefault("Stop", [])
    stop_cmd = f"{keephive_bin} hook-stop"
    if not any("keephive hook-stop" in _extract_cmds(h) for h in stop_hooks):
        stop_hooks.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": stop_cmd}],
            }
        )
        console.print("  [ok]OK[/ok] Stop hook added")
    else:
        console.print("  [dim]Stop hook already configured[/dim]")

    # SessionEnd hook (finalize session stats)
    se_hooks = hooks.setdefault("SessionEnd", [])
    se_cmd = f"{keephive_bin} hook-sessionend"
    if not any("keephive hook-sessionend" in _extract_cmds(h) for h in se_hooks):
        se_hooks.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": se_cmd}],
            }
        )
        console.print("  [ok]OK[/ok] SessionEnd hook added")
    else:
        console.print("  [dim]SessionEnd hook already configured[/dim]")

    # TaskCompleted hook (auto-log DONE to daily log)
    tc_hooks = hooks.setdefault("TaskCompleted", [])
    tc_cmd = f"{keephive_bin} hook-taskcompleted"
    if not any("keephive hook-taskcompleted" in _extract_cmds(h) for h in tc_hooks):
        tc_hooks.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": tc_cmd}],
            }
        )
        console.print("  [ok]OK[/ok] TaskCompleted hook added")
    else:
        console.print("  [dim]TaskCompleted hook already configured[/dim]")

    # SubagentStop hook (log subagent completion breadcrumbs)
    sas_hooks = hooks.setdefault("SubagentStop", [])
    sas_cmd = f"{keephive_bin} hook-subagent-stop"
    if not any("keephive hook-subagent-stop" in _extract_cmds(h) for h in sas_hooks):
        sas_hooks.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": sas_cmd}],
            }
        )
        console.print("  [ok]OK[/ok] SubagentStop hook added")
    else:
        console.print("  [dim]SubagentStop hook already configured[/dim]")

    # Notification hook (log + macOS desktop notification)
    notif_hooks = hooks.setdefault("Notification", [])
    notif_cmd = f"{keephive_bin} hook-notification"
    if not any("keephive hook-notification" in _extract_cmds(h) for h in notif_hooks):
        notif_hooks.append(
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": notif_cmd}],
            }
        )
        console.print("  [ok]OK[/ok] Notification hook added")
    else:
        console.print("  [dim]Notification hook already configured[/dim]")

    # Write back
    settings_path.write_text(json.dumps(data, indent=2) + "\n")


def _register_mcp() -> None:
    """Register keephive as an MCP server with Claude Code."""
    import subprocess

    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "--scope", "user", "hive", "--", "keephive", "mcp-serve"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "CLAUDECODE": ""},
        )
        if result.returncode == 0:
            console.print("  [ok]OK[/ok] MCP server registered")
        else:
            msg = result.stderr.strip() or "already registered"
            console.print(f"  [dim]MCP: {msg}[/dim]")
    except FileNotFoundError:
        console.print("  [dim]claude CLI not found, skip MCP registration[/dim]")
    except subprocess.TimeoutExpired:
        console.print("  [dim]MCP registration timed out[/dim]")


def _extract_cmds(hook_entry: dict) -> str:
    """Extract all command strings from a hook entry for matching."""
    parts: list[str] = []
    if hook_entry.get("command"):
        parts.append(hook_entry["command"])
    if "hooks" in hook_entry:
        parts.extend(h.get("command", "") for h in hook_entry["hooks"] if h.get("command"))
    return " ".join(parts)


def _sync_global_install() -> None:
    """Re-install global keephive binary if dependencies are stale.

    Detects when the global tool env (via uv tool install) is missing
    required packages (e.g. anthropic added after initial install).
    Runs `uv tool install --force .` to bring it up to date.
    """
    import subprocess

    if not find_global_keephive():
        console.print("  [dim]No global install found, skipping sync[/dim]")
        return

    missing = check_installed_deps()
    if not missing:
        console.print("  [ok]OK[/ok] global install deps up to date")
        return

    console.print(f"  [warn]STALE[/warn] Missing deps: {', '.join(missing)}")

    # Detect install source: use local path if in keephive repo, otherwise git URL
    pyproject = Path.cwd() / "pyproject.toml"
    if pyproject.exists() and "keephive" in pyproject.read_text()[:500]:
        source = "."
    else:
        source = "keephive@git+https://github.com/joryeugene/keephive.git"

    console.print(f"  [dim]Running: uv tool install --force {source}[/dim]")

    try:
        result = subprocess.run(
            ["uv", "tool", "install", "--force", source],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            console.print("  [ok]OK[/ok] global install updated")
        else:
            console.print(f"  [err]FAILED[/err] {result.stderr.strip()[:200]}")
    except FileNotFoundError:
        console.print("  [err]FAILED[/err] uv not found")
    except subprocess.TimeoutExpired:
        console.print("  [err]FAILED[/err] uv tool install timed out")


def _uninstall() -> None:
    console.print("[bold]Uninstall keephive[/bold]")
    console.print()
    console.print("[warn]This will remove hook configuration but NOT your data.[/warn]")
    console.print(f"  Data lives in: {hive_dir()}")
    console.print()

    # Remove hooks from settings
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            hooks = data.get("hooks", {})

            for event in [
                "SessionStart",
                "PreCompact",
                "PostToolUse",
                "UserPromptSubmit",
                "Stop",
                "SessionEnd",
                "TaskCompleted",
                "SubagentStop",
                "Notification",
            ]:
                if event in hooks:
                    cleaned = []
                    for h in hooks[event]:
                        cmds = _extract_cmds(h)
                        if "keephive" in cmds or "hive" in cmds:
                            continue
                        cleaned.append(h)
                    if cleaned:
                        hooks[event] = cleaned
                    else:
                        del hooks[event]

            settings_path.write_text(json.dumps(data, indent=2) + "\n")
            console.print("  [ok]OK[/ok] Hooks removed from settings.json")
        except (json.JSONDecodeError, KeyError):
            console.print("  [warn]Could not clean settings.json[/warn]")

    console.print()
    console.print(
        "  To fully remove: rm -rf ~/.keephive ~/.claude/hive (remove legacy symlink if present)"
    )
