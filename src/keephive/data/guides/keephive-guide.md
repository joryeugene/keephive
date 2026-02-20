---
tags: [keephive, memory, verification, guide, help]
projects: [keephive]
---

# keephive

A knowledge sidecar for Claude Code. Commands work as both `keephive <cmd>` and `hive <cmd>`.

## Commands

### Core

| Command                | Shorthand         | What it does                                                     |
| ---------------------- | ----------------- | ---------------------------------------------------------------- |
| `hive status`          | `hive s`          | Status at a glance (stale warnings, recent entries, suggestions) |
| `hive remember "fact"` | `hive r "fact"`   | Save to today's daily log                                        |
| `hive recall <query>`  | `hive rc <query>` | Search all memory tiers (recency-weighted)                       |
| `hive verify`          | `hive v`          | Check and fix stale facts against codebase                       |
| `hive reflect`         | `hive rf`         | Scan daily logs for patterns                                     |
| `hive edit [target]`   | `hive e [target]` | Quick-edit (memory, rules, claude, settings, local, note)        |
| `hive log`             | `hive l`          | View today's entries                                             |
| `hive note`            | `hive n`          | Multi-slot scratchpad ($EDITOR, auto-copy)                       |
| `hive todo`            |                   | List open TODOs with ages                                        |
| `hive todo done <pat>` |                   | Mark a TODO complete                                             |
| `hive todo repeat`     |                   | List due recurring tasks                                         |
| `hive audit`           |                   | Quality Pulse analysis (score, suggestions)                      |
| `hive stats`           |                   | Usage statistics (sessions, commands, projects)                  |
| `hive doctor`          | `hive dr`         | Health check (hooks, MCP, data integrity)                        |
| `hive standup`         |                   | Generate standup summary from recent activity                    |
| `hive gc`              |                   | Archive old logs, rebuild index                                  |

### Knowledge

| Command                      | Shorthand        | What it does                              |
| ---------------------------- | ---------------- | ----------------------------------------- |
| `hive knowledge`             | `hive k`         | List all guides                           |
| `hive knowledge <name>`      | `hive k <name>`  | View a guide (supports prefix matching)   |
| `hive knowledge edit <name>` | `hive ke <name>` | Create/edit a guide                       |
| `hive prompt`                | `hive p`         | List prompt templates                     |
| `hive prompt <name>`         | `hive p <name>`  | Output prompt to stdout (prefix matching) |
| `hive prompt edit <name>`    | `hive pe <name>` | Create/edit a prompt                      |

### Note (Multi-Slot Scratchpad)

| Command                | Shorthand           | What it does                            |
| ---------------------- | ------------------- | --------------------------------------- |
| `hive note`            | `hive n`            | Open active slot in $EDITOR (auto-copy) |
| `hive n.3`             |                     | Switch to slot 3, open editor           |
| `hive n.3 show`        |                     | Show slot 3 content                     |
| `hive n.3 copy`        | `hive n.3c`         | Copy slot 3 to clipboard                |
| `hive n.3 clear`       |                     | Archive and clear slot 3                |
| `hive note copy`       | `hive nc`           | Copy active slot to clipboard           |
| `hive note show`       | `hive n show`       | Print active slot with line/byte stats  |
| `hive note clear`      | `hive n clear`      | Archive and clear active slot           |
| `hive note list`       | `hive n list`       | Show all slots + archived notes         |
| `hive note <N>`        | `hive n <N>`        | Restore the Nth archived note           |
| `hive note <template>` | `hive n <template>` | Start note from a prompt template       |
| `hive d` / `hive dc`   |                     | Aliases (backward compat)               |

## How It Works

### Auto-capture (no agent action needed)

When Claude Code compacts a conversation, keephive's PreCompact hook:

1. Reads the full transcript from disk (JSONL)
2. Extracts messages with budget distribution (50% user / 50% assistant)
3. Pipes excerpts to `claude -p` for structured classification (DECISION/FACT/CORRECTION/TODO/INSIGHT)
4. Writes classified insights directly to the daily log

### Smart context injection

When a new session starts, keephive's SessionStart hook injects:

- Working memory (core verified facts)
- Behavioral rules
- Stale fact warnings
- Matching knowledge guides (by project name/tag)
- Open TODOs and recent activity

### Verification

Facts have `[verified:YYYY-MM-DD]` timestamps. After 30 days (configurable), they're flagged as stale. Run `hive verify` to check against the codebase:

- VALID facts auto-update their verified date
- STALE facts auto-correct when the right value is deterministic
- UNCERTAIN facts are flagged but not modified

## MCP Tools

When keephive runs as an MCP server, Claude Code calls these natively:

| MCP Tool                              | CLI Equivalent         | What it does               |
| ------------------------------------- | ---------------------- | -------------------------- |
| `hive_remember(text)`                 | `hive r "text"`        | Save insight to daily log  |
| `hive_recall(query)`                  | `hive rc <query>`      | Search all memory tiers    |
| `hive_status()`                       | `hive s`               | Status overview            |
| `hive_todo()`                         | `hive todo`            | List open TODOs            |
| `hive_todo_done(pattern)`             | `hive todo done <pat>` | Mark TODO complete         |
| `hive_knowledge(name)`                | `hive k [name]`        | List/view knowledge guides |
| `hive_knowledge_write(name, content)` | `hive ke <name>`       | Create/update guide        |
| `hive_prompt_write(name, content)`    | `hive pe <name>`       | Create/update prompt       |
| `hive_mem(action, text)`              | `hive mem`             | Manage memory facts        |
| `hive_rule(action, text)`             | `hive rule`            | Manage rules               |
| `hive_log(day)`                       | `hive l`               | View daily log             |
| `hive_audit()`                        | `hive audit`           | Quality Pulse analysis     |
| `hive_recurring()`                    | `hive todo repeat`     | List due recurring tasks   |
| `hive_stats(project, date)`           | `hive stats`           | Usage statistics           |

MCP tools are preferred when available. Use CLI for: verify, reflect, edit, note, gc, setup.

## Configuration

| Variable              | Default          | Description                            |
| --------------------- | ---------------- | -------------------------------------- |
| `HIVE_HOME`           | `~/.claude/hive` | Data directory                         |
| `HIVE_STALE_DAYS`     | `30`             | Days before a fact is flagged          |
| `HIVE_CAPTURE_BUDGET` | `4000`           | Characters to extract from transcripts |
| `HIVE_SKIP_LLM`       | (unset)          | Skip LLM calls in hooks (testing)      |
| `KEEPHIVE_PLUGIN_DIR` | (auto)           | Override skill/plugin directory        |
| `NO_COLOR`            | (unset)          | Disable terminal colors                |

## Workflows

### Starting Work

Check what you already know before diving in:

1. hive_recall(topic) or `hive rc <topic>` for previous insights
2. hive_todo() or `hive todo` for open items
3. Check status for stale facts or overdue items

### During Work

Record as you go. Use hive_remember(text) or `hive r`:

- FACT: something learned
- DECISION: chose X over Y because Z
- CORRECTION: old assumption was wrong, truth is X
- TODO: what needs doing
- INSIGHT: non-obvious pattern

### Code Hygiene

Before writing new utility functions or patterns:

1. hive_recall(concept) to check if it already exists
2. Search the codebase for existing implementations
3. Consolidate rather than duplicate

After finishing work:

- Remove any dead code you introduced
- Check for empty catch blocks (log or handle, never swallow)
- Verify no orphaned imports or unused variables

### Maintenance (CLI only)

- `hive v` to verify stale facts against codebase
- `hive rf` to find patterns in daily logs
- `hive k edit <name>` to create a knowledge guide

## LLM Features and Costs

### Default is always free

From a terminal or a hook, keephive uses `claude -p` (Claude Code subscription).
`ANTHROPIC_API_KEY` is ignored — setting it for other tools does NOT bill keephive.

The API path is taken only when INSIDE a Claude Code session (`CLAUDECODE` is set) AND `ANTHROPIC_API_KEY` is present. Hooks do not run inside Claude Code, so they are always free.

### LLM-powered commands

| Command | Model | Cost |
|---------|-------|------|
| `hive a` | 3× haiku + 1× sonnet | 4 calls — use intentionally |
| `hive v` | sonnet + tools | Multi-turn — use intentionally |
| `hive rf`, `hive su`, `hive l summarize` | haiku | Light |
| PreCompact hook | haiku | Automatic, always free |

### Free commands

`hive r`, `hive rc`, `hive s`, `hive todo`, `hive m`, `hive rule`, `hive e`, `hive n`, `hive k`, `hive p`, `hive st`, `hive l`, `hive gc`, `hive sk`

### Agent rule

Use free commands for routine work. Reserve `hive a` and `hive v` for intentional quality checks.
