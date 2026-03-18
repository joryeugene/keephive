"""Stop hook handler.

Called by Claude Code when the agent's turn ends.
Increments a turn counter per session and fires a periodic micro-nudge
to encourage capturing wrap-up items (TODOs, decisions, facts).

Loop mode: if a `.loop-*.json` file exists for this session, intercepts the
stop to either continue the loop (exit 2) or complete it (exit 0).
Exit code 2 tells Claude Code to inject additionalContext and continue the session
rather than ending it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

from keephive.clock import get_now


def hook_stop(_args: list[str]) -> None:
    """Main entry point for Stop hook."""
    raw = sys.stdin.read()
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        return

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    # ── Loop intercept: MUST come before nudge logic ──────────────────────────
    # If a loop is active for this session, drive the iteration state machine.
    try:
        from keephive.commands.loop import _find_loop_for_session, _loop_done_path, _write_iter_log
        from keephive.nudge import build_nudge_output
        from keephive.storage import hive_dir

        req, loop_file = _find_loop_for_session(session_id)
        if req is not None:
            loop_id = req["loop_id"]
            # iter tracks completions so far; iter_n is what we just finished
            iter_n = req["iter"] + 1
            done_path = _loop_done_path(loop_id)

            # Time-based done check (max_seconds=None means no limit)
            max_seconds = req.get("max_seconds")
            if max_seconds:
                started = req.get("created_at", "")
                try:
                    elapsed = (datetime.now() - datetime.fromisoformat(started)).total_seconds()
                    time_done = elapsed >= max_seconds
                except (ValueError, TypeError):
                    time_done = False
            else:
                time_done = False

            done = done_path.exists() or time_done

            if done:
                # Capture early-exit state before unlink (unlink destroys the evidence)
                was_early = done_path.exists()
                # Loop complete — clean up, spawn extractor, emit completion nudge
                loop_file.unlink(missing_ok=True)
                done_path.unlink(missing_ok=True)
                (hive_dir() / f".loop-prompt-{loop_id}.txt").unlink(missing_ok=True)
                _write_iter_log(loop_id, iter_n, "complete")

                env = {
                    k: v
                    for k, v in os.environ.items()
                    if k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}
                }
                try:
                    extract_cmd = [sys.executable, "-m", "keephive", "loop-extract", loop_id]
                    # Pass task so extractor can auto-close matching TODOs
                    if req.get("task"):
                        extract_cmd.append(req["task"])
                    subprocess.Popen(
                        extract_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        env=env,
                    )
                except Exception as spawn_err:
                    try:
                        debug_log = hive_dir() / ".hook-debug.log"
                        with open(debug_log, "a") as dbg:
                            dbg.write(
                                f"[{get_now().isoformat(timespec='seconds')}] "
                                f"loop-extract spawn error: {spawn_err}\n"
                            )
                    except Exception:
                        pass

                early = "cancelled" if was_early else f"{iter_n} iterations"
                W = 62
                completion_msg = (
                    "╔" + "═" * W + "╗\n"
                    "║  🐝 Loop Complete" + " " * (W - 17) + "║\n"
                    "╠" + "═" * W + "╣\n"
                    f"║  {loop_id:<{W - 2}}║\n"
                    f"║  ✓ {early:<{W - 4}}║\n"
                    "║  ✓ Facts queued for review" + " " * (W - 26) + "║\n"
                    "╠" + "═" * W + "╣\n"
                    "║  Next: hive run review" + " " * (W - 22) + "║\n"
                    "╚" + "═" * W + "╝"
                )
                sys.stdout.write(build_nudge_output(completion_msg, event_name="Stop"))
                sys.exit(0)

            else:
                # Loop continues — update state, write heartbeat, emit continuation, exit 2
                req["iter"] = iter_n
                req["last_stop_at"] = get_now().isoformat(timespec="seconds")
                loop_file.write_text(json.dumps(req))
                _write_iter_log(loop_id, iter_n, f"iter {iter_n}")

                next_iter = iter_n + 1

                # Time-remaining hint for agent awareness
                time_hint = ""
                max_seconds = req.get("max_seconds")
                if max_seconds:
                    started = req.get("created_at", "")
                    try:
                        from keephive.commands.loop import _format_duration

                        elapsed = (
                            datetime.now() - datetime.fromisoformat(started)
                        ).total_seconds()
                        remaining = int(max_seconds - elapsed)
                        if remaining > 0:
                            time_hint = f"  Time remaining: ~{_format_duration(remaining)}\n"
                    except (ValueError, TypeError, ImportError):
                        pass

                maintenance = (
                    "SELF-MAINTENANCE: hive todo → close what's done. hive s → note warnings.\n"
                    + "─" * 64
                    + "\n"
                )
                continuation = maintenance + (
                    f"─── ITERATION {next_iter} " + "─" * 49 + "\n"
                    f"PROGRESS CHECK: In one line — what did iteration {iter_n} accomplish?\n"
                    f"TASK: {req['task']}\n"
                    + time_hint
                    + "─" * 64
                )
                sys.stdout.write(build_nudge_output(continuation, event_name="Stop"))
                sys.exit(2)

    except SystemExit:
        raise  # Re-raise exit(0) and exit(2) — do not swallow
    except Exception as loop_err:
        # Never let loop errors crash the stop hook
        try:
            from keephive.storage import hive_dir

            debug_log = hive_dir() / ".hook-debug.log"
            with open(debug_log, "a") as dbg:
                dbg.write(
                    f"[{get_now().isoformat(timespec='seconds')}] loop stop error: {loop_err}\n"
                )
        except Exception:
            pass
    # ── End loop intercept ────────────────────────────────────────────────────

    # Track hook event
    try:
        from keephive.storage import track_event

        track_event("hooks", "stop", source="hook")
    except Exception:
        pass

    # Increment turn counter for this session
    try:
        from keephive.storage import track_session_event

        track_session_event(session_id, "stop")
    except Exception:
        pass

    # Check UI feedback queue — inject at end of every turn, persist to daily log
    try:
        from keephive.storage import drain_ui_queue

        result = drain_ui_queue(input_data.get("cwd", ""), event_name="Stop")
        if result:
            sys.stdout.write(result)
            return  # Queue consumed; skip nudge this turn
    except Exception:
        pass  # Never block the turn

    # Periodic nudge (every 12 turns by default)
    try:
        from keephive.nudge import build_nudge_output, get_stop_nudge, should_nudge

        fire, count = should_nudge("stop", session_id)
        if fire:
            nudge_text = get_stop_nudge(count, session_id=session_id)
            output = build_nudge_output(nudge_text, event_name="Stop")
            sys.stdout.write(output)
    except Exception as e:
        try:
            from keephive.storage import hive_dir

            debug_log = hive_dir() / ".hook-debug.log"
            with open(debug_log, "a") as f:
                f.write(f"[{get_now().isoformat(timespec='seconds')}] stop error: {e}\n")
        except Exception:
            pass
