"""Shared watch infrastructure for --watch / -w flag.

Clear-and-reprint loop with mtime-based change detection.
Used by status, log, and todo commands.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable

from keephive.clock import get_now


def parse_watch_args(args: list[str]) -> tuple[list[str], bool, float]:
    """Strip --watch/-w and --interval N from args.

    Returns (remaining_args, watch_mode, interval_seconds).
    """
    remaining: list[str] = []
    watch = False
    interval = 2.0
    i = 0
    while i < len(args):
        if args[i] in ("--watch", "-w"):
            watch = True
        elif args[i] == "--interval" and i + 1 < len(args):
            try:
                interval = float(args[i + 1])
                if interval < 0.5:
                    interval = 0.5
            except ValueError:
                remaining.append(args[i])
                remaining.append(args[i + 1])
                i += 2
                continue
            i += 1  # skip the value
        else:
            remaining.append(args[i])
        i += 1
    return remaining, watch, interval


def _collect_mtimes(paths: list[Path]) -> dict[str, float]:
    """Stat a list of paths, skip missing ones.

    For directories, uses the directory's own mtime (detects new/deleted files).
    """
    mtimes: dict[str, float] = {}
    for p in paths:
        try:
            mtimes[str(p)] = p.stat().st_mtime
        except OSError:
            pass
    return mtimes


def watch_loop(
    render_fn: Callable[[], None],
    watch_paths_fn: Callable[[], list[Path]],
    interval: float = 2.0,
) -> None:
    """Clear-and-reprint loop that re-renders when watched files change.

    Args:
        render_fn: Callable that prints the command output.
        watch_paths_fn: Callable returning paths to watch (re-evaluated each cycle).
        interval: Seconds between mtime checks.
    """
    if not sys.stdout.isatty():
        print("[keephive] --watch requires an interactive terminal", file=sys.stderr)
        return

    prev_mtimes: dict[str, float] = {}

    try:
        while True:
            # Clear terminal (ANSI escape: clear screen + move cursor to top)
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

            # Header
            ts = get_now().strftime("%H:%M:%S")
            sys.stdout.write(
                f"\033[2m watching (every {interval:.0f}s) \u00b7 {ts} \u00b7 ctrl+c to stop\033[0m\n\n"
            )
            sys.stdout.flush()

            # Render the command output
            render_fn()

            # Snapshot mtimes
            current_paths = watch_paths_fn()
            prev_mtimes = _collect_mtimes(current_paths)

            # Poll for changes
            while True:
                time.sleep(interval)
                current_paths = watch_paths_fn()
                new_mtimes = _collect_mtimes(current_paths)
                if new_mtimes != prev_mtimes:
                    break

    except KeyboardInterrupt:
        # Clean exit: move to new line, print message
        sys.stdout.write("\n\033[2mWatch stopped\033[0m\n")
        sys.stdout.flush()
