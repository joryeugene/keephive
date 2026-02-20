"""Check PyPI for a newer version of keephive and run upgrade if confirmed."""

import json
import subprocess
import urllib.request

from keephive import __version__
from keephive.output import console, prompt_yn

PYPI_URL = "https://pypi.org/pypi/keephive/json"


def cmd_update(_args: list[str]) -> int:
    """Check PyPI for a newer version and offer to upgrade."""
    console.print("[dim]Checking PyPI for updates...[/dim]")
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=5) as resp:
            data = json.loads(resp.read())
        latest = data["info"]["version"]
    except Exception as e:
        console.print(f"[err]Could not reach PyPI:[/err] {e}")
        return 1

    if latest == __version__:
        console.print(f"[ok]Up to date[/ok] (v{__version__})")
        return 0

    console.print(f"  Current: v{__version__}")
    console.print(f"  Latest:  v{latest}")
    console.print()

    if not prompt_yn("Upgrade now?"):
        console.print("  [dim]Run manually: uv tool upgrade keephive && keephive setup[/dim]")
        return 0

    console.print()
    console.print("[dim]Running: uv tool upgrade keephive...[/dim]")
    result = subprocess.run(["uv", "tool", "upgrade", "keephive"])
    if result.returncode != 0:
        console.print("[err]Upgrade failed.[/err] Run manually: uv tool upgrade keephive")
        return result.returncode

    console.print()
    console.print("[dim]Running: keephive setup...[/dim]")
    result = subprocess.run(["keephive", "setup"])
    if result.returncode != 0:
        console.print("[warn]Setup step failed.[/warn] Run manually: keephive setup")
        return result.returncode

    console.print()
    console.print(f"[ok]Updated to v{latest}[/ok]")
    return 0
