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
| "uv run pytest", "release flow: dev->main rebase"          |
| Edited by: user or Claude (with ask)                       |
+============================================================+
| WORKING MEMORY  ~/.claude/hive/memory.md                   |
| Verified cross-session facts. 30-90d TTL.                  |
| "auth uses JWT 15min", "keephive v0.13.2 released"         |
| Updated via hive_mem, verified by hive v                   |
+============================================================+
| RULES  ~/.claude/hive/rules.md                             |
| Behavioral nudges for Claude this session                  |
| hive rule add "text", hive rule rm "text"                  |
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
| Claude should behave differently | Rules | `hive rule add "..."` |
| Rich structured doc for a topic | Knowledge guide | `hive ke <name>` |
| Universal style/tool preference | Global CLAUDE.md | Edit manually |
| Project-specific invariant | Project CLAUDE.md | Edit manually |

## Hook Roles

- **SessionStart**: Injects working memory, rules, matched knowledge guides,
  stale warnings, open TODOs, and cross-project activity hints.
- **PreCompact**: Layer 1 deterministic scan + Layer 2 claude -p haiku.
  Writes classified entries (FACT/DECISION/etc.) to daily log with project attribution.
- **PostToolUse**: Counter-based nudge after Edit/Write to remind
  `hive_remember` of key changes.
- **UserPromptSubmit**: TODO detection, UI queue injection, counter nudge.

## Promotion Path (ephemeral to durable)

- `daily log` + `hive rf` -> review patterns -> working memory or knowledge guide
- `daily log TODO` + `hive td` -> DONE in log -> archived
- `working memory` + `hive v` -> verified facts -> updated date or auto-corrected
- `knowledge guide` + `hive v` -> fact check -> stale facts flagged

## Anti-patterns

- Ephemeral session details in working memory (clutter, slow to verify)
- Universal rules only in project CLAUDE.md (won't apply to other projects)
- Rich structured docs in working memory (wrong tier, use knowledge guides)
- Treating daily log as permanent (promote or it gets archived by `hive gc`)
- Adding to rules what belongs in global CLAUDE.md (rules are per-session nudges)
