"""Health checks for keephive installation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from keephive.storage import hive_dir, memory_file, rules_file


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
            capture_output=True, text=True, timeout=5,
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
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                missing.append(pkg)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return missing


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
    return (
        hive_dir().exists()
        and memory_file().exists()
        and rules_file().exists()
    )


def health_summary() -> tuple[bool, bool, bool]:
    """Return (hooks_ok, mcp_ok, data_ok)."""
    return check_hooks(), check_mcp(), check_data()
