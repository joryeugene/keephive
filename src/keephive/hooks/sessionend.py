"""SessionEnd hook handler.

Called by Claude Code when a session terminates.
Finalizes session stats with an accurate end timestamp.
No stdout output. Pure stats finalization.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def hook_sessionend(_args: list[str]) -> None:
    """Main entry point for SessionEnd hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        return

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    reason = input_data.get("reason", "")

    # Track hook event
    try:
        from keephive.storage import track_event

        track_event("hooks", "sessionend", source="hook")
    except Exception:
        pass

    # Finalize session with accurate end timestamp
    try:
        from keephive.storage import track_session_event

        track_session_event(session_id, "end", reason=reason)
    except Exception:
        pass

    # Fire non-blocking KingBee tasks (soul-update always, self-improve weekly-throttled)
    # Both default to enabled: true in daemon.json. Neither requires the daemon process.
    # start_new_session=True detaches child from parent FDs — no stdout contamination.
    try:
        from keephive.storage import daemon_task_enabled

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        if daemon_task_enabled("soul-update"):
            subprocess.Popen(
                [sys.executable, "-m", "keephive", "daemon", "run", "soul-update"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        if daemon_task_enabled("self-improve"):
            subprocess.Popen(
                [sys.executable, "-m", "keephive", "daemon", "run", "self-improve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
    except Exception:
        pass

    # Loop cleanup: remove orphaned loop files owned by this session.
    # Runs silently — sessionend must never produce stdout.
    try:
        from keephive.storage import hive_dir

        for loop_file in hive_dir().glob(".loop-*.json"):
            try:
                req = json.loads(loop_file.read_text())
                if req.get("session_id") == session_id:
                    loop_file.unlink(missing_ok=True)
                    loop_id = req.get("loop_id", "")
                    done_path = hive_dir() / f".loop-done-{loop_id}"
                    done_path.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                pass
    except Exception:
        pass
