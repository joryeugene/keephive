"""Notification hook handler.

Called by Claude Code when a desktop notification is triggered.
Logs the notification to today's daily log and fires a macOS notification
via osascript when running on Darwin.

No stdout output. Silent on all errors.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys


def hook_notification(_args: list[str]) -> None:
    """Main entry point for Notification hook."""
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except json.JSONDecodeError:
        return

    if not req.get("session_id"):
        return

    message = req.get("message", "").strip()
    title = req.get("title", "Claude Code").strip()
    if not message:
        return

    label = f"{title}: {message}" if title else message

    try:
        from keephive.storage import append_to_daily

        append_to_daily(f"[Notification] {label}")
    except Exception:
        pass

    if platform.system() == "Darwin":
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}"',
                ],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass
