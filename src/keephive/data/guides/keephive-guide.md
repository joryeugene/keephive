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
| `hive recall <query>`  | `hive rc <query>` | Search all memory tiers (BM25 relevance ranking)                 |
| `hive flow`            |                   | **Guided maintenance flow**: drain all queues in one pass        |
| `hive verify`          | `hive v`          | Check and fix stale facts against codebase                       |
| `hive reflect`         | `hive rf`         | Scan daily logs for patterns                                     |
| `hive edit [target]`   | `hive e [target]` | Quick-edit (memory, rules, claude, settings, local, soul)        |
| `hive log`             | `hive l`          | View today's entries                                             |
| `hive note`            | `hive n`          | Multi-slot scratchpad ($EDITOR, auto-copy)                       |
| `hive todo`            |                   | List open TODOs with ages                                        |
| `hive todo done <pat>` |                   | Mark a TODO complete                                             |
| `hive todo repeat`     |                   | List due recurring tasks                                         |
| `hive audit`           |                   | Quality Pulse analysis (score, suggestions)                      |
| `hive stats`           |                   | Usage statistics (sessions, commands, projects)                  |
| `hive doctor`          | `hive dr`         | Health check (hooks, MCP, data integrity)                        |
| `hive standup`         |                   | Generate standup summary from recent activity                    |
| `hive rule learn`      |                   | Learn rules from /insights friction data                         |
| `hive rule review`     |                   | Accept/reject pending rule suggestions                           |
| `hive improve review`  |                   | Review KingBee self-improvement proposals                        |
| `hive daemon start`    |                   | Start background daemon (KingBee)                                |
| `hive gc`              |                   | Archive old logs, rebuild index                                  |
| `hive serve [port]`    | `hive ws`         | Live web dashboard (localhost:3847)                              |
| `hive ui`              |                   | Show pending UI feedback queue                                   |
| `hive ui-install`      |                   | Print bookmarklet URL (drag to bookmarks bar)                    |
| `hive ui-clear`        |                   | Discard pending UI feedback                                      |

### Knowledge

| Command                      | Shorthand        | What it does                              |
| ---------------------------- | ---------------- | ----------------------------------------- |
| `hive knowledge`             | `hive k`         | List all guides                           |
| `hive knowledge <name>`      | `hive k <name>`  | View a guide (supports prefix matching)   |
| `hive knowledge edit <name>` | `hive ke <name>` | Create/edit a guide                       |
| `hive prompt`                | `hive p`         | List prompt templates                     |
| `hive prompt <name>`         | `hive p <name>`  | Output prompt to stdout (prefix matching) |
| `hive prompt edit <name>`    | `hive pe <name>` | Create/edit a prompt                      |

### Sessions

Launch a Claude session with full keephive context pre-loaded.

| Command                        | Short             | What                                      |
|--------------------------------|-------------------|-------------------------------------------|
| `hive session`                 | `hive go`         | General session (all context, open prompt) |
| `hive session todo`            | `hive go todo`    | Triage open TODOs                         |
| `hive session verify`          | `hive go verify`  | Check stale facts against codebase        |
| `hive session learn`           | `hive go learn`   | Active recall quiz on recent decisions    |
| `hive session reflect`         | `hive go reflect` | Pattern discovery from daily logs         |
| `hive session <prompt>`        | `hive go <prompt>`| Load a custom prompt by name or prefix    |

Prompt names resolve by prefix: `hive go pr-re` finds `pr-review-git-staged-diff-analysis.md` from `knowledge/prompts/`.

Pipe content into a session: `cat spec.yaml | hive go review`

Continue or resume: `hive go -c` (last conversation), `hive go -r <id>` (specific session).

### Note (Multi-Slot Scratchpad)

| Command                | Shorthand           | What it does                            |
| ---------------------- | ------------------- | --------------------------------------- |
| `hive note`            | `hive n`            | Open active slot in $EDITOR (auto-copy)         |
| `hive n.3`             | `hive 3`            | Switch to slot 3, open editor                   |
| `hive n.3 show`        |                     | Show slot 3 content                             |
| `hive n.3 copy`        | `hive n.3c`         | Copy slot 3 to clipboard                        |
| `hive n.3 clear`       |                     | Archive and clear slot 3                        |
| `hive note copy`       | `hive nc`           | Copy active slot to clipboard                   |
| `hive note show`       | `hive n show`       | Print active slot with line/byte stats          |
| `hive note clear`      | `hive n clear`      | Archive and clear active slot                   |
| `hive note list`       | `hive n list`       | Show all slots + archived notes                 |
| `hive note todo`       | `hive n todo`       | Extract action items, offer to add as TODOs     |
| `hive 4`               |                     | Open slot 4 in $EDITOR (bare-digit shorthand)   |
| `hive 4 todo`          |                     | Extract TODOs from slot 4                       |
| `hive 4 "text"`        |                     | Append text to slot 4 (no editor)               |
| `hive note <N>`        | `hive n <N>`        | Restore the Nth archived note                   |
| `hive note <template>` | `hive n <template>` | Start note from a prompt template               |
| `hive d` / `hive dc`   |                     | Aliases (backward compat)                       |

## How It Works

### Auto-capture (no agent action needed)

When Claude Code compacts a conversation, keephive's PreCompact hook:

1. Reads the full transcript from disk (JSONL)
2. Extracts messages with budget distribution (50% user / 50% assistant)
3. Pipes excerpts to `claude -p` for structured classification (DECISION/FACT/CORRECTION/TODO/INSIGHT)
4. Writes classified insights to the daily log with project attribution (`[project:name]` tag)
5. Triggers a throttled `soul-update` (min 1h) to refresh the agent's identity.

### KingBee (Identity & Maintenance)

The **KingBee** daemon manages a persistent identity for your agent in `~/.keephive/hive/working/SOUL.md`.

- **Agent Identity**: Manages a cross-project summary of specialized skills and patterns.
- **Morning Briefing**: Your first session of the day receives a briefing of pending tasks.
- **Self-Improvement**: Periodically scans logs to propose new skills, rules, or task optimizations.
- **Stale Check**: Background scan for facts needing verification.

### Smart context injection

When a new session starts, keephive's SessionStart hook injects:

- Working memory (core verified facts)
- Behavioral rules
- **Agent Identity** (summary from SOUL.md)
- Stale fact warnings
- Matching knowledge guides (by project name/tag, or opt-in `always: true` for universal guides)
- Open TODOs and recent activity
- Cross-project activity hints (when insights from other projects exist in recent logs)

When `hive go` launches a session, it writes a timestamp file (`.session-launched`) that the SessionStart hook reads. If the timestamp is <15 seconds old, the hook skips injection to avoid doubling context. This prevents the 2× duplication that would otherwise occur.

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
| `hive_log(day)`                       | `hive l`               | View daily log (entries may include [project:name] tags) |
| `hive_audit()`                        | `hive audit`           | Quality Pulse analysis     |
| `hive_recurring(action, freq, text)`  | `hive todo repeat`     | Manage recurring tasks (list/add/rm/done) |
| `hive_stats(project, date)`           | `hive stats`           | Usage statistics           |
| `hive_fts_search(query)`              | `hive rc <query>`      | FTS5-ranked search over logs + archive |
| `hive_standup(use_llm)`               | `hive su`              | Generate standup from logs + GitHub PRs |
| `hive_prompt(name)`                   | `hive p <name>`        | Get prompt template by name |
| `hive_ps()`                           | `hive ps`              | Active sessions + recent project activity |

MCP tools are preferred when available. Use CLI for: verify, reflect, edit, note, gc, setup.

## Configuration

| Variable              | Default             | Description                                              |
| --------------------- | ------------------- | -------------------------------------------------------- |
| `HIVE_HOME`           | `~/.keephive/hive`  | Data directory                                           |
| `HIVE_STALE_DAYS`     | `30`                | Days before a fact is flagged                            |
| `HIVE_CAPTURE_BUDGET` | `4000`              | Characters to extract from transcripts                   |
| `HIVE_SKIP_LLM`       | (unset)             | Skip LLM calls in hooks (testing only)                   |
| `KEEPHIVE_PLUGIN_DIR` | (auto)              | Override skill/plugin directory                          |
| `ANTHROPIC_API_KEY`   | (unset)             | Enables Anthropic API backend inside sessions            |
| `OPENAI_API_KEY`      | (unset)             | Enables OpenAI backend fallback inside sessions          |
| `GEMINI_API_KEY`      | (unset)             | Enables Gemini API backend fallback inside sessions      |
| `NO_COLOR`            | (unset)             | Disable terminal colors                                  |

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

- `hive flow` to run a guided maintenance pass
- `hive v` to verify stale facts against codebase
- `hive rf` to find patterns in daily logs
- `hive k edit <name>` to create a knowledge guide
- `hive rule learn` to generate rules from /insights friction data
- `hive rule review` to accept/reject queued rule suggestions
- `hive improve review` to review self-improvement proposals

## Dashboard

`hive serve` (or `hive ws`) launches a live web dashboard at localhost:3847.

### When to use each view

| View     | Path        | Use when you need                                        |
| -------- | ----------- | -------------------------------------------------------- |
| Home     | `/`         | Full picture: status, log, TODOs, knowledge, memory      |
| Dev      | `/dev`      | Quick reference while coding                             |
| Brain    | `/brain`    | High-density: working memory, rules, TODOs, telemetry    |
| Know     | `/know`     | Deep-read knowledge guides                               |
| Stats    | `/stats`    | Usage patterns, command breakdown                        |
| Settings | `/settings` | Profile management, agent identity, daemon status        |

### Layout principle

Dynamic content (log, TODOs) sits at the top. Static content (standup, knowledge, memory) sits at the bottom. Real-time status is always the first row.

### Tips

- **Cmd+K** opens search across all memory tiers
- **Auto-refresh** interval is configurable via the dropdown (5s to 60s, or off)
- **Date navigation** in `/daily` view lets you browse past days
- **Bookmarklet** (`hive ui-install`) captures UI feedback from any page and queues it for your next Claude Code prompt

#### Bookmarklet feedback loop

1. `hive ui-install` prints a `javascript:` URL and copies it to your clipboard
2. Create a bookmark and paste the URL, or drag it to your bookmarks bar
3. Click the bookmarklet on any page: a crosshair overlay appears
4. Hover to highlight elements, click to select one
5. Type your feedback in the dialog, hit Submit
6. The bookmarklet POSTs to `localhost:3847/ui-feedback`, which queues it in `.ui-queue`
7. On your next prompt, the UserPromptSubmit hook injects `[UI Feedback]` context automatically
8. The queue clears after injection (one-shot)

No manual copy-paste. The feedback appears in Claude Code's context on the very next prompt.

### Architecture

```
             +---------+
             | Browser |
             +----+----+
                  |
          GET / POST (localhost:3847)
                  |
          +-------v--------+
          |  _HiveHandler  |
          +--+-----+--+---+
             |     |   |
     GET /   | /api/  POST /ui-feedback
     (page)  | fragment   |
             |            v
     +-------v-------+  +-----------+
     | render_page   |  | .ui-queue |
     | render_fragment|  +-----------+
     +-------+-------+       |
             |          UserPromptSubmit
     +-------v-------+  hook injects as
     | PANELS dict   |  [UI Feedback]
     | 18 panels     |
     +---------------+

  Bookmarklet (hive ui-install):
    click → select element → POST /ui-feedback → .ui-queue → next prompt
```

### View layouts

```
Home (/)                Brain (/brain)
+------------------+    +------------------+
| status  |   ps   |    | memory  | rules  |
+------------------+    +------------------+
| log-home         |    | todos   | soul   |
+------------------+    +------------------+
| todos            |    | platform telemetry|
+------------------+    +------------------+
| knowledge-limited|
+------------------+    Dev (/dev)
| memory           |
+------------------+    +------------------+
Stats (/stats)          | status-brief     |
+------------------+    +------------------+
| stats   |   ps   |    | todos  | log     |
+------------------+    +------------------+
| stats-commands   |    | facts            |
+------------------+    +------------------+
                        | knowledge| memory|
Settings (/settings)    +------------------+
+------------------+
| profiles         |    Know (/know)
+------------------+    +------------------+
| daemon status    |    | status-brief     |
+------------------+    +------------------+
| soul preview     |    | knowledge guides |
+------------------+    +------------------+
```

## Multi-Backend LLM Routing

keephive uses a priority-ordered backend chain. All LLM commands (`hive a`, `hive v`, etc.)
go through this layer automatically:

| Priority | Backend         | When active                          |
|----------|----------------|--------------------------------------|
| 10       | `anthropic_cli` | `claude` binary on PATH (default)    |
| 20       | `anthropic_api` | `ANTHROPIC_API_KEY` + inside session |
| 25       | `gemini_api`    | `GEMINI_API_KEY` set                 |
| 30       | `openai_api`    | `OPENAI_API_KEY` set                 |
| 99       | `none`          | Always — offline mode                |

**Failover**: If a backend fails with a generic `ClaudePipeError`, the router
automatically tries the next backend (in auto mode). The `none` backend always runs
last and raises `ClaudePipeError("No LLM backend available")` if reached.

**Non-retriable errors**: A `BackendTimeoutError` (subprocess timed out) propagates
immediately — no retry. A different model answering the same prompt would silently
change behavior, which is not acceptable for verification or audit tasks.

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
| `hive daemon run` | haiku / sonnet | Task-dependent |
| PreCompact hook | haiku | Automatic, always free |

### Free commands

`hive r`, `hive rc`, `hive s`, `hive todo`, `hive m`, `hive rule`, `hive rule learn`, `hive e`, `hive n`, `hive k`, `hive p`, `hive st`, `hive l`, `hive gc`, `hive sk`, `hive serve`, `hive ui`, `hive flow` (excluding verify stage)

### Agent rule

Use free commands for routine work. Reserve `hive a` and `hive v` for intentional quality checks.
