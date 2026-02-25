---
tags: [keephive, loop, autonomous, run, hive-run]
---

# hive run — Autonomous Iteration Loop

`hive run` lets Claude execute a multi-iteration task loop without human prompting between
steps. Each iteration is one Claude turn. The Stop hook intercepts session end, injects a
continuation banner via `additionalContext` (exit code 2), and the loop repeats until
`max_iter` or an early-stop signal.

---

## Quick Reference

| Command                                      | What it does                                      |
|----------------------------------------------|---------------------------------------------------|
| `hive run "<task>"`                          | Start in-session loop (default 10 iters)          |
| `hive run "<task>" --max 5`                  | Set iteration cap                                 |
| `hive run "<task>" --background`             | Launch in new tmux window                        |
| `hive run "<task>" --at 22:00`               | Schedule via daemon                               |
| `hive run "<task>" --tonight`                | Queue for tonight's scheduled run                 |
| `hive run status`                            | Show active loop files                            |
| `hive run cancel`                            | Cancel all active loops                           |
| `hive run cancel <loop-id>`                  | Cancel a specific loop                            |
| `hive run history`                           | Show recent completed loops                       |
| `hive run review`                            | Accept/reject extracted facts from `.pending-facts.md` |

---

## Three Modes

### in-session
Start inside a Claude Code session:

```bash
hive run "Audit all 7 dashboard views in serve.py for DOM-swap compliance"
```

Creates a `.loop-{id}.json` state file. Prints a first-iteration banner to stdout that
Claude reads immediately. The Stop hook detects the loop on session end and injects the
continuation banner as `additionalContext`, causing Claude to continue rather than stop.

### background
Start outside Claude Code, or force a new window:

```bash
hive run "<task>" --background
```

Launches `claude --dangerously-skip-permissions` in a new tmux window with `HIVE_LOOP_ID`
set. Runs without human supervision. Check progress with `hive run status` or
`hive daemon log`.

### scheduled
Queue for daemon execution at a specific time:

```bash
hive run "<task>" --at 22:00      # tonight at 10pm
hive run "<task>" --tonight       # daemon's configured overnight window
```

The daemon picks up queued loops and runs them in background mode.

---

## Writing Effective Task Prompts

The task prompt is the only instruction Claude has for the entire loop. Quality here
determines whether the loop converges or wanders.

### Be specific about the target

**Bad:**
```
fix the dashboard
```

**Good:**
```
Audit all 7 dashboard views in serve.py for DOM-swap compliance. Each view must
emit panel-update SSE events rather than calling location.reload(). Complete when
all 7 views pass the reload-free check.
```

### State the done condition

The loop ends at `max_iter` regardless — but a clear done condition helps Claude know
when to stop producing partial work and consolidate.

```
Complete when: all 7 views use panel-update events, none use location.reload(),
and just test passes.
```

### Anchor to the codebase

Include file names, function names, and constraints Claude would otherwise have to
discover:

```
Target: serve.py, _dashboard_view(), _brain_view(), _dev_view(). Constraint: no
external deps, no full-page reload. Test: just test-e2e.
```

### Size to 3–5 meaningful iterations

Ten iterations of vague exploration produces 10x the noise of 5 focused ones.
`--max 5` for a bounded task is almost always better than the default 10.

---

## The Iteration Lifecycle

Each iteration follows this sequence:

```
╔══════════════════════════════════════════════╗
║  LOOP: {loop-id}  TASK: {first 54 chars}    ║
║  MAX: N                                      ║
╚══════════════════════════════════════════════╝

WISDOM (from KingBee's memory):
  · ...

SELF-MAINTENANCE (complete before task work):
  1. hive todo          → review open items; close what's done
  2. hive s             → health snapshot; note warnings
  3. hive rc "..."      → pull prior context for this task
────────────────────────────────────────────────

─── ITERATION 1/N ───────────────────────────────
TASK: ...
  Early stop: touch ~/.keephive/hive/.loop-done-{id}  (omit = auto-continue)
────────────────────────────────────────────────
```

After the SELF-MAINTENANCE block, Claude does the task work. On session end:

1. Stop hook fires
2. Increments `.loop-{id}.json` iter counter
3. If `iter_n >= max_iter` or done file exists → loop completion
4. Otherwise → injects continuation banner as `additionalContext`, exits with code 2
5. Claude continues to next iteration

After the final iteration, a `loop-extract` subprocess runs, extracting facts, decisions,
and TODOs into `.pending-facts.md`. Review them with `hive run review`.

---

## Early Stop vs Natural Completion

**Natural completion:** The loop reaches `max_iter`. Completion box shows `N/M iter`.

**Early stop:** Touch the done file to abort before `max_iter`:

```bash
touch ~/.keephive/hive/.loop-done-{loop-id}
```

Completion box shows `early exit`. Use this when the task is done before the cap, or
when the loop is clearly spinning without progress.

**Cancel (immediate):** Removes the loop state files entirely, no extract run:

```bash
hive run cancel              # cancel all active
hive run cancel {loop-id}    # cancel specific
```

Cancel vs early-stop: cancel removes the loop immediately; early-stop lets the current
iteration finish and runs extract before closing.

---

## After the Loop: `hive run review`

After completion, `loop-extract` writes extracted insights to `.pending-facts.md`.
Review them:

```bash
hive run review
```

Each item is shown with accept/skip controls. Accepted items are written to the daily
log and queued for `memory.md`. Skipped items are discarded.

Run `hive run review` before starting a new loop on the same topic — otherwise
extracted facts from the previous run accumulate unreviewed.

---

## Troubleshooting

**Loop terminated after exactly 1 iteration**

The first-iteration banner says "Early stop: touch ..." — if Claude creates this file
during its task work (interpreting it as "signal I'm done with this task"), the Stop hook
reads `done_path.exists()` as True and terminates. This was a known bug fixed in v0.x:
the instruction now reads "Early stop: ... (omit = auto-continue)" to make the
emergency-abort semantics unambiguous.

If you see 1-iteration loops on an older install: `uv tool upgrade keephive`.

**Loop not starting (no banner output)**

Check for orphaned state files from a crashed session:

```bash
hive run status
ls ~/.keephive/hive/.loop-*.json
```

If a stale loop file exists for a session that no longer exists, cancel it:

```bash
hive run cancel {loop-id}
```

**Background loop not visible**

```bash
tmux ls         # look for a window named after the loop-id
hive daemon log # daemon-launched loops write to daemon.log
```

**Completion box shows wrong label (N/M iter instead of "early exit")**

Pre-fix versions ran `done_path.unlink()` before the `done_path.exists()` check,
making the "early exit" branch unreachable. Fixed in v0.x alongside the Signal done bug.

---

## Max Iter Guidance

| Scenario                          | Recommended `--max` |
|-----------------------------------|---------------------|
| Exploratory / unknown scope       | 5–7                 |
| Bounded refactor (file/function)  | 3–5                 |
| Multi-file audit                  | 5–7                 |
| Complex feature implementation    | 7–10                |
| Quick fix with verification       | 3                   |

Default (`--max 10`) is generous. Most refactoring tasks hit diminishing returns after
7 iterations. Each iteration costs roughly $0.02–0.10 in Claude Code credits depending
on context size and tool use.

Use `hive run status` to see how many iterations a running loop has consumed before
committing to a high cap.
