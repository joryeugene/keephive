"""Health checks for keephive installation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from keephive.storage import hive_dir, memory_file


def find_global_keephive() -> Path | None:
    """Find the globally-installed keephive binary (uv tool install).

    Looks in ~/.local/bin specifically, not the dev venv, since the MCP server
    and hooks run from the global install.
    """
    global_bin = Path.home() / ".local" / "bin" / "keephive"
    if global_bin.exists():
        return global_bin
    return None


def get_installed_version() -> str | None:
    """Get the version of the globally-installed keephive binary.

    Returns None if keephive is not installed globally or can't be queried.
    """
    import subprocess

    keephive_bin = find_global_keephive()
    if not keephive_bin:
        return None
    try:
        result = subprocess.run(
            [str(keephive_bin), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Output format: "keephive vX.Y.Z"
        for word in result.stdout.strip().split():
            if word.startswith("v"):
                return word[1:]  # strip 'v' prefix
            if re.match(r"\d+\.\d+", word):
                return word
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def get_tool_python() -> str | None:
    """Get the Python interpreter for the global keephive tool env."""
    keephive_bin = find_global_keephive()
    if not keephive_bin:
        return None
    try:
        with open(keephive_bin) as f:
            shebang = f.readline().strip()
        if not shebang.startswith("#!"):
            return None
        tool_python = shebang[2:].strip()
        if Path(tool_python).exists():
            return tool_python
    except OSError:
        pass
    return None


def check_installed_deps() -> list[str]:
    """Check if the global keephive install has all required dependencies.

    Returns list of missing package names.
    """
    import subprocess

    tool_python = get_tool_python()
    if not tool_python:
        return []

    missing = []
    for pkg in ["anthropic"]:
        try:
            result = subprocess.run(
                [tool_python, "-c", f"import {pkg}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                missing.append(pkg)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return missing


def check_content_drift() -> bool:
    """Return True if installed cli.py differs from dev source cli.py.

    Detects content drift within the same version: when `uv tool install --force`
    used a cached wheel and didn't pick up file changes.
    """
    import hashlib

    tool_python = get_tool_python()
    if not tool_python:
        return False

    # Find installed cli.py under the tool env's site-packages
    tool_lib = Path(tool_python).parent.parent / "lib"
    installed_cli: Path | None = None
    for p in tool_lib.rglob("keephive/cli.py"):
        installed_cli = p
        break
    if not installed_cli or not installed_cli.exists():
        return False

    # Dev cli.py lives alongside health.py
    dev_cli = Path(__file__).parent / "cli.py"
    if not dev_cli.exists():
        return False

    def md5(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()

    return md5(installed_cli) != md5(dev_cli)


def check_hooks() -> bool:
    """Check if keephive hooks are configured in settings.json."""
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.exists():
        return False
    try:
        data = json.loads(settings.read_text())
        hooks_str = json.dumps(data.get("hooks", {}))
        return (
            "hook-sessionstart" in hooks_str
            and "hook-precompact" in hooks_str
            and "hook-posttooluse" in hooks_str
            and "hook-userpromptsubmit" in hooks_str
        )
    except (json.JSONDecodeError, OSError):
        return False


def check_mcp() -> bool:
    """Check if keephive MCP server is registered in ~/.claude.json."""
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return False
    try:
        data = json.loads(claude_json.read_text())
        servers = data.get("mcpServers", {})
        return "hive" in servers
    except (json.JSONDecodeError, OSError):
        return False


def check_data() -> bool:
    """Check if essential data files exist."""
    return hive_dir().exists() and memory_file().exists()


def check_soul() -> bool:
    """Check if agent identity (SOUL.md) exists."""
    from keephive.storage import soul_file
    return soul_file().exists()


def check_daemon() -> bool:
    """Check if KingBee background daemon is running."""
    from keephive.storage import get_daemon_pid
    pid = get_daemon_pid()
    if not pid:
        return False
    # Check if process exists and is keephive
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def check_anthropic_memory() -> str:
    """Detect if Anthropic official memory is active.

    Returns 'active', 'inactive', or 'unknown'.
    """
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        try:
            data = json.loads(claude_json.read_text())
            if data.get("memories") or data.get("memory"):
                return "active"
        except (json.JSONDecodeError, OSError):
            pass
    memories_dir = Path.home() / ".claude" / "memories"
    if memories_dir.exists() and any(memories_dir.iterdir()):
        return "active"
    return "inactive"


def health_summary() -> tuple[bool, bool, bool]:
    """Return (hooks_ok, mcp_ok, data_ok)."""
    return check_hooks(), check_mcp(), check_data()
