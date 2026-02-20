"""hive setup: initial setup and hook configuration."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from keephive import __version__
from keephive.health import check_installed_deps, find_global_keephive
from keephive.identity import render_default_memory, render_default_rules
from keephive.output import console
from keephive.storage import ensure_dirs, hive_dir


def cmd_setup(args: list[str]) -> None:
    if args and args[0] == "uninstall":
        _uninstall()
        return

    console.print(f"[bold]keephive setup v{__version__}[/bold]")
    console.print()

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

    # 5. Configure hooks
    console.print()
    console.print("  Configuring hooks...")
    _setup_hooks()

    # 6. Register MCP server
    console.print()
    console.print("  Registering MCP server...")
    _register_mcp()

    # 7. Sync global install if stale
    console.print()
    console.print("  Checking global install...")
    _sync_global_install()

    console.print()
    console.print("[ok]Setup complete![/ok]")
    console.print()
    console.print("  -> [dim]hive s[/dim] to check status")
    console.print("  -> [dim]hive doctor[/dim] to verify everything")


def _seed_bundled_content(quiet: bool = False) -> None:
    """Copy bundled guides and prompts into hive dir if targets don't exist."""
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
                    elif dest.read_text() != bundled_content:
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

            for event in ["SessionStart", "PreCompact", "PostToolUse", "UserPromptSubmit"]:
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
    console.print("  To fully remove: rm -rf ~/.claude/hive")
