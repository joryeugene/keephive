"""Check PyPI for a newer version of keephive and print upgrade instructions if behind."""

import json
import urllib.request

from keephive import __version__
from keephive.output import console

PYPI_URL = "https://pypi.org/pypi/keephive/json"


def cmd_update(_args: list[str]) -> int:
    """Check PyPI for a newer version and print upgrade instructions if behind."""
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
    console.print("  [bold]uv tool upgrade keephive && keephive setup[/bold]")
    return 0
