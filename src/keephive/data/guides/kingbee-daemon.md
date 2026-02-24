---
tags: [keephive, daemon, soul, kingbee, maintenance]
---

# KingBee Daemon — Operational Guide

KingBee is the background daemon that manages a persistent agent identity and five
scheduled maintenance tasks. This guide is the operational reference for the daemon
system: SOUL.md, self-improve, checkup, and the full command vocabulary.

---

## Quick Reference

| Command                              | What it does                                      |
|--------------------------------------|---------------------------------------------------|
| `hive daemon start`                  | Launch daemon process                             |
| `hive daemon stop`                   | Kill daemon process                               |
| `hive daemon status`                 | Show task schedule and last-run times             |
| `hive daemon run soul-update`        | Distill sessions into SOUL.md immediately         |
| `hive daemon run self-improve`       | Propose new skills/rules from recent logs         |
| `hive daemon enable <task>`          | Toggle a scheduled task on                        |
| `hive daemon disable <task>`         | Toggle a scheduled task off                       |
| `hive daemon edit`                   | Open daemon.json in $EDITOR                       |
| `hive daemon log`                    | Tail last 50 lines of daemon.log                  |
| `hive checkup`                       | 6-stage production health report                  |
| `hive checkup --snapshot`            | Git-snapshot hive state (for before/after diffs)  |
| `hive checkup --diff`                | Show what mutated since last snapshot             |
| `hive checkup --json`                | Machine-readable health report (scripting/CI)     |
| `hive flow`                          | Guided queue drain (facts, rules, improvements)   |
| `hive flow --skip-verify`            | Skip LLM verify stage (faster routine drain)      |
| `hive improve review`                | Interactive review of KingBee proposals           |
| `hive improve list`                  | List pending proposals with age                   |
| `hive improve clear-stale`           | Remove proposals older than 30 days               |
| `hive wander list`                   | Show recent wander documents                      |
| `hive wander seed <text>`            | Queue a topic for the next wander run             |
| `hive wander show [slug]`            | Show a specific wander doc (default: most recent) |
| `hive wander run`                    | Trigger wander immediately                        |
| `hive daemon enable wander`          | Enable the daily wander task (14:00)              |

---

## SOUL.md — Agent Identity

**Location:** `~/.keephive/hive/working/SOUL.md`

SOUL.md is the persistent cross-project identity file. It is:

- **Distilled** from session logs by the `soul-update` task (haiku LLM call)
- **Injected** as `## Agent Identity` (~300 tokens) at every session start
- **Replaced** (not appended) on each update — the file is rewritten wholesale
- **Hard capped** at 800 tokens; typical content is ~500 tokens

### Update triggers

| Trigger                | Condition                                  |
|------------------------|--------------------------------------------|
| PreCompact hook        | After compaction, if excerpts not empty    |
| `hive daemon run soul-update` | Manual, no throttle gate          |
| Daemon daily tick      | If soul-update task is enabled             |

**Throttle:** The PreCompact hook enforces a 1-hour minimum between soul-update runs
across all callers. If a compaction happens within 1h of the last update, it is
silently skipped (not logged to daemon.log — this is by design).

To force an update regardless of throttle: `hive daemon run soul-update`.

To check SOUL.md age: `hive checkup` (shows days since last update in Stage 4).

---

## self-improve Cycle

The `self-improve` task scans recent daily logs and proposes improvements to the
agent's configuration. Proposals are queued in `.pending-improvements` and reviewed
via `hive improve review`.

### Proposal types

| Type   | What it proposes                                       |
|--------|--------------------------------------------------------|
| rule   | Behavioral nudge to avoid recurring friction           |
| skill  | New specialized capability or tool pattern             |
| task   | Optimization for daily workflow or recurring task      |
| edit   | Direct improvement to a knowledge guide or config file |

### Throttle and queue cap

- **Throttle:** 1 day minimum between self-improve runs
- **Queue cap:** 20 pending proposals maximum — self-improve stops proposing if queue is full

### Review workflow

```bash
hive improve review      # interactive accept/defer/dismiss for each proposal
hive improve list        # view all pending proposals with age in days
hive improve clear-stale # remove proposals older than 30 days
```

Accepted proposals are applied immediately (rules to `rules.md`, edits to guides,
etc.). Dismissed proposals are added to a dismissed list and filtered from future
suggestions.

---

## Daemon Config

The daemon reads `~/.keephive/hive/daemon.json` (or the profile-specific equivalent).
Edit with `hive daemon edit`.

### Task schedule defaults

| Task              | Default | Trigger             | Throttle  | Notes                         |
|-------------------|---------|---------------------|-----------|-------------------------------|
| soul-update       | on      | PreCompact / manual | 1h        |                               |
| self-improve      | on      | daily / manual      | 1 day     |                               |
| morning-briefing  | off     | 07:00               | daily     |                               |
| stale-check       | off     | Monday 08:00        | weekly    |                               |
| standup-draft     | off     | 17:00               | daily     |                               |
| wander            | off     | 14:00               | daily     | WebSearch enabled; 1 doc/day  |

Tasks marked `off` require explicit opt-in:

```bash
hive daemon enable morning-briefing   # activate daily briefing at 07:00
hive daemon enable stale-check        # activate weekly stale scan
hive daemon disable standup-draft     # deactivate (already off by default)
```

---

## Checkup — 6-Stage Health Report

`hive checkup` is a read-only diagnostic that inspects the full keephive system.
No LLM calls, no writes. Runs in under 1 second.

### The 6 stages

| Stage | What it checks                                                        |
|-------|-----------------------------------------------------------------------|
| 1     | Hook pipeline — all 7 hooks registered and pointing to current binary |
| 2     | Daemon task freshness — last-run times vs. expected schedule          |
| 3     | Queue depths — pending facts, pending rules, pending improvements     |
| 4     | SOUL.md age — days since last soul-update, warns if > 7 days          |
| 5     | JSON integrity — .stats.json, daemon.json, settings parseable          |
| 6     | Magic numbers — stale threshold, nudge intervals vs. documented values |

### Warning thresholds

| Item                | Warning threshold     |
|---------------------|-----------------------|
| SOUL.md age         | > 7 days              |
| Pending facts queue | > 10 items            |
| Pending improvements| > 15 items (near cap) |
| Hook binary path    | Mismatch with current install |

### Flags

```bash
hive checkup                 # full report to terminal
hive checkup --json          # machine-readable (pipe to jq, CI scripts)
hive checkup --snapshot      # git-commit current hive state for diffing
hive checkup --diff          # show file-level diff since last snapshot
```

The `--snapshot`/`--diff` pattern is useful for before/after testing:

```bash
hive checkup --snapshot      # snapshot before making changes
# ... make changes, run daemon tasks, etc. ...
hive checkup --diff          # see exactly what mutated
```

---

## Wander Task — Agent Free Thinking

The `wander` task gives KingBee unstructured time. Instead of reacting to a trigger,
it picks a **seed** from memory and thinks freely. The result is a "wander document":
free associations, unexpected connections, one hypothesis, one open question.

The open question surfaces in the next session (injected into SessionStart context).
The hypothesis is included in SOUL.md updates under "What I've Been Wondering".

### Seed selection priority

1. **User-queued** — topics explicitly queued with `hive wander seed <text>`
2. **Cross-pollination** — two memory.md lines with no overlapping significant words
3. **Recurring topic** — a word appearing in 3+ of the last 14 daily logs with freq >= 3
4. **Stale TODO** — the oldest open TODO older than 7 days

If no seed is available from any priority, the task skips silently (returns False, no retry throttle consumed).

### Tool access

Wander uses `WebSearch` (built-in, not MCP). The LLM prompt encourages but does not
require search — KingBee decides whether to look something up. `used_web_search` in the
wander document captures whether it did.

### View wander output

```bash
hive wander list         # recent docs with hypothesis + question
hive wander show         # most recent full doc
hive wander show <slug>  # specific doc by filename prefix
hive serve               # navigate to /play for the dashboard panel
```

---

## Throttle Reference

| Operation        | Throttle    | Enforced by         | On skip     |
|------------------|-------------|---------------------|-------------|
| soul-update      | 1 hour      | PreCompact hook     | Silent skip |
| self-improve     | 1 day       | Daemon tick         | Silent skip |
| morning-briefing | Daily       | Daemon tick (07:00) | Skip        |
| stale-check      | Weekly      | Daemon tick (Mon)   | Skip        |
| wander           | Daily       | Daemon tick (14:00) | Skip        |
| nudge (prompt)   | Every 5     | userpromptsubmit    | No nudge    |
| nudge (tool)     | Every 5     | posttooluse         | No nudge    |
| nudge (stop)     | Every 8     | stop hook           | No nudge    |

**Note on silent throttle skips:** When the PreCompact hook skips soul-update due to
throttle, nothing is written to daemon.log. This is intentional — throttle skips are
expected and frequent; logging them would create noise that obscures real daemon activity.
