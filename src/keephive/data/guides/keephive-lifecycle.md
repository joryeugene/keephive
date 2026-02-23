---
tags: keephive
---

# keephive Information Hierarchy

Tiers from most stable (top) to most ephemeral (bottom).

## The Hierarchy

```
+------------------------------------------------------------+
| GLOBAL CLAUDE.MD  ~/.claude/CLAUDE.md                      |
| Universal invariants across ALL projects/sessions          |
| "always use uv", "no em-dashes", skill trigger rules       |
| Changes: rarely (monthly). Edited by: user only            |
+============================================================+
| PROJECT CLAUDE.MD  <repo>/CLAUDE.md                        |
| Repo conventions, dev commands, architecture notes         |
| "just test", "release flow: dev->main rebase"              |
| Edited by: user or Claude (with ask)                       |
+============================================================+
| JUSTFILE  <repo>/justfile                                  |
| Executable dev commands. The "how to run it" scratchpad.   |
| `just test`, `just serve`, `just lint`, `just check`       |
| Edited by: user or Claude. Self-documenting via comments.  |
+============================================================+
| AGENT IDENTITY  ~/.claude/hive/working/SOUL.md             |
| Persistent cross-project identity. Managed by KingBee.     |
| Specialized skills, strengths, weaknesses, personality.    |
| Updated via soul-update task / PreCompact throttle.        |
+============================================================+
| WORKING MEMORY  ~/.claude/hive/working/memory.md           |
| Verified cross-session facts. 30-90d TTL.                  |
| "auth uses JWT 15min", "keephive v0.13.2 released"         |
| Updated via hive_mem, verified by hive v                   |
+============================================================+
| RULES  ~/.claude/hive/rules.md                             |
| Behavioral nudges for Claude this session                  |
| hive rule "text", rm, learn (from /insights), review       |
+============================================================+
| KNOWLEDGE GUIDES  ~/.../knowledge/guides/<name>.md         |
| Rich structured docs per topic, injected by tag (or opt-in always) |
| hive ke <name> to edit, hive k <name> to view              |
+============================================================+
| DAILY LOG  ~/.claude/hive/YYYY-MM-DD.md                    |
| Raw capture stream. FACT/TODO/DONE/DECISION/INSIGHT        |
| hive r, precompact hook, hive t, hive td                   |
+============================================================+
| TODOS  (from daily log, 7-day stale TTL)                   |
| hive t "text", hive todo, hive todo done                   |
+------------------------------------------------------------+
| NOTES  ~/.../notes/slot-N.md  (slots 1-4)                  |
| Scratchpad. Copied to clipboard. NOT injected.             |
+------------------------------------------------------------+
```

## Decision Table: What Goes Where?

| Situation | Destination | Command |
|-----------|-------------|---------|
| Learned a fact this session | Daily log | `hive r "FACT: ..."` |
| Verified a fact is still true | Working memory | `hive mem` |
| Made an architecture decision | Daily log first | `hive r "DECISION: ..."` |
| Identified an action item | Daily log as TODO | `hive t "fix the thing"` |
| Previous assumption was wrong | Daily log | `hive r "CORRECTION: ..."` |
| Pattern worth remembering long-term | Working memory | `hive mem` |
| Agent learned a new core skill | Agent Identity | Managed by KingBee |
| Claude should behave differently | Rules | `hive rule "..."` |
| Friction patterns from /insights | Pending rules | `hive rule learn` |
| Rich structured doc for a topic | Knowledge guide | `hive ke <name>` |
| Universal style/tool preference | Global CLAUDE.md | Edit manually |
| Project-specific invariant | Project CLAUDE.md | Edit manually |
| Repeatable dev command | justfile | `just <recipe>` |

## Hook Roles

- **SessionStart**: Injects working memory, rules, **Agent Identity**, matched knowledge guides,
  stale warnings, open TODOs, and cross-project activity hints. Also triggers Morning Briefing.
- **PreCompact**: Layer 1 deterministic scan + Layer 2 claude -p haiku.
  Writes classified entries (FACT/DECISION/etc.) to daily log.
  Triggers throttled `soul-update` (min 1h interval).
  Auto-close: passes open TODOs to LLM, writes DONE entries for resolved items.
- **PostToolUse**: Counter-based nudge after Edit/Write to remind
  `hive_remember` of key changes.
- **UserPromptSubmit**: Counter-based nudge, UI queue injection.
- **Stop**: Increments turn counter. Periodic micro-nudge to capture wrap-up items.
- **SessionEnd**: Finalizes session stats. Spawns `soul-update` and `self-improve` tasks.
- **TaskCompleted**: Auto-logs DONE to daily log. Tracks task completion count in meta stats.

## Promotion Path (ephemeral to durable)

- `daily log` + `hive rf` -> review patterns -> working memory or knowledge guide
- `daily log` + KingBee `self-improve` -> proposed skills/rules -> `hive improve review` -> SOUL.md or rules.md
- `daily log TODO` + `hive td` -> DONE in log -> archived
- `working memory` + `hive v` -> verified facts -> updated date or auto-corrected
- `knowledge guide` + `hive v` -> fact check -> stale facts flagged
- `/insights` friction data + `hive rule learn` -> `.pending-rules.md` -> `hive rule review` -> `rules.md`

## The Justfile as Workspace Scratchpad

Every repo should have a `justfile` (or Makefile) that captures repeatable commands. This is the executable counterpart to CLAUDE.md: where CLAUDE.md says *what* and *why*, the justfile says *how to run it*.

- Commands you type more than twice belong in the justfile
- `just --list` is self-documenting (comments become descriptions)
- New agents in fresh sessions get the full command vocabulary without reading docs
- Recipes compose: `just check` runs `test + lint + check-private`

When you add a new test tier, build step, or dev server, add a recipe. When CLAUDE.md references a raw `uv run` command, check if a `just` recipe exists first.

## Anti-patterns

- Ephemeral session details in working memory (clutter, slow to verify)
- Universal rules only in project CLAUDE.md (won't apply to other projects)
- Rich structured docs in working memory (wrong tier, use knowledge guides)
- Treating daily log as permanent (promote or it gets archived by `hive gc`)
- Adding to rules what belongs in global CLAUDE.md (rules are per-session nudges)
- Raw `uv run pytest -m X -v -o "addopts="` in CLAUDE.md when a `just` recipe exists
