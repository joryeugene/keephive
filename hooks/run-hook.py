#!/usr/bin/env python3
"""Thin wrapper that forwards to the keephive binary.

Used by hooks.json so that /plugin install registers hooks automatically.
If keephive is not installed, exits cleanly (does not block the session).
"""

import shutil
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        sys.exit(0)

    hook_name = sys.argv[1]
    binary = shutil.which("keephive")

    if not binary:
        # keephive not installed yet. Exit cleanly.
        sys.exit(0)

    stdin_data = sys.stdin.buffer.read()
    result = subprocess.run(
        [binary, f"hook-{hook_name}"],
        input=stdin_data,
        capture_output=False,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
