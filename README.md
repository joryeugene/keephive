# keephive

A knowledge sidecar for Claude Code. Captures what you learn, verifies it stays true, surfaces it when relevant.

<img src="https://raw.githubusercontent.com/joryeugene/keephive/main/assets/mascot.png" width="200" />

Claude Code forgets everything between sessions. keephive rides alongside it using hooks, an MCP server, and CLAUDE.md injection to give it persistent, verified memory.

## Install

One line (requires [uv](https://docs.astral.sh/uv/)):

```bash
curl -fsSL https://raw.githubusercontent.com/joryeugene/keephive/main/install.sh | bash
```

This installs the package, registers the MCP server, and configures Claude Code hooks.

Or manually:

```bash
uv tool install keephive@git+https://github.com/joryeugene/keephive.git
keephive setup
```

From a local clone:

```bash
git clone https://github.com/joryeugene/keephive.git
cd keephive && uv tool install . && keephive setup
```

## Quick start

```bash
hive r "FACT: Auth service uses JWT with RS256"   # remember something
hive v                                             # verify stale facts
hive go                                            # launch interactive session
hive todo                                          # open TODOs
```

## How it works

keephive uses the three extension points Claude Code exposes:

1. **Hooks** fire on events (session start, conversation compact, user prompt). They capture insights and inject context without any agent action.
2. **MCP server** gives Claude Code native tool access (`hive_remember`, `hive_recall`, etc.) so the agent can read and write memory directly.
3. **CLAUDE.md** injection puts behavioral rules and verified facts into every session's system prompt.

### The loop

```
  capture --> store --> verify --> correct
     ^                               |
     +-------------------------------+
```

- **Capture**: PreCompact hook extracts FACT/DECISION/TODO/INSIGHT entries when conversations compact
- **Store**: Entries land in daily logs, get promoted to working memory
- **Verify**: Facts carry `[verified:YYYY-MM-DD]` timestamps. After 30 days they're flagged stale. `hive v` checks them against the codebase with LLM analysis.
- **Correct**: Invalid facts get replaced. Valid facts get re-stamped.

### Memory tiers

| Tier             | Path                                 | Purpose                           |
| ---------------- | ------------------------------------ | --------------------------------- |
| Working memory   | `~/.claude/hive/working/memory.md`   | Core facts, loaded every session  |
| Rules            | `~/.claude/hive/working/rules.md`    | Behavioral rules for the agent    |
| Knowledge guides | `~/.claude/hive/knowledge/guides/`   | Deep reference on specific topics |
| Daily logs       | `~/.claude/hive/daily/YYYY-MM-DD.md` | Append-only session logs          |
| Archive          | `~/.claude/hive/archive/`            | Old daily logs after gc           |

### Hooks

| Hook             | Trigger               | What it does                                           |
| ---------------- | --------------------- | ------------------------------------------------------ |
| SessionStart     | New session           | Injects memory, rules, TODOs, stale warnings           |
| PreCompact       | Conversation compacts | Extracts insights from transcript, writes to daily log |
| PostToolUse      | After Edit/Write      | Periodic nudge to record decisions                     |
| UserPromptSubmit | User sends prompt     | Periodic nudge + auto-captures TODO: from prompts      |

## Commands

| Command                | Short             | What                                       |
| ---------------------- | ----------------- | ------------------------------------------ |
| `hive status`          | `hive s`          | Status overview                            |
| `hive remember "text"` | `hive r "text"`   | Save to daily log                          |
| `hive recall <query>`  | `hive rc <query>` | Search all tiers                           |
| `hive verify`          | `hive v`          | Verify stale facts                         |
| `hive session [mode]`  | `hive go`         | Launch interactive session                 |
| `hive todo`            | `hive td`         | Open TODOs with ages                       |
| `hive todo done <pat>` |                   | Mark TODO complete                         |
| `hive reflect`         | `hive rf`         | Pattern scan across daily logs             |
| `hive audit`           | `hive a`          | Quality Pulse: 3 perspectives + synthesis  |
| `hive standup`         | `hive su`         | Standup summary with GitHub PR integration |
| `hive stats`           | `hive st`         | Usage statistics                           |
| `hive log [date]`      | `hive l`          | View daily log                             |
| `hive note`            | `hive n`          | Multi-slot scratchpad ($EDITOR)            |
| `hive knowledge`       | `hive k`          | List/view knowledge guides                 |
| `hive doctor`          | `hive dr`         | Health check                               |
| `hive gc`              | `hive g`          | Archive old logs                           |

### Sessions

`hive go` launches an interactive Claude session with your full keephive context pre-loaded.

| Command                 | What                                           |
| ----------------------- | ---------------------------------------------- |
| `hive go`               | General session with full memory and warnings  |
| `hive session todo`     | Walk through open TODOs one by one             |
| `hive session verify`   | Check stale facts against the codebase         |
| `hive session learn`    | Active recall quiz on recent decisions         |
| `hive session reflect`  | Pattern discovery from daily logs              |
| `hive session <prompt>` | Load a custom prompt from `knowledge/prompts/` |

### MCP tools

All commands are also available as MCP tools for Claude Code to call directly:

`hive_remember`, `hive_recall`, `hive_status`, `hive_todo`, `hive_todo_done`, `hive_knowledge`, `hive_knowledge_write`, `hive_prompt_write`, `hive_mem`, `hive_rule`, `hive_log`, `hive_audit`, `hive_recurring`, `hive_stats`

## Configuration

| Variable              | Default          | Description                                         |
| --------------------- | ---------------- | --------------------------------------------------- |
| `HIVE_HOME`           | `~/.claude/hive` | Data directory                                      |
| `HIVE_STALE_DAYS`     | `30`             | Days before a fact is flagged stale                 |
| `HIVE_CAPTURE_BUDGET` | `4000`           | Characters to extract from transcripts              |
| `ANTHROPIC_API_KEY`   | (unset)          | Enables direct API calls (works inside Claude Code) |
| `NO_COLOR`            | (unset)          | Disable terminal colors                             |

When `ANTHROPIC_API_KEY` is set, keephive calls the Anthropic API directly. Without it, keephive falls back to `claude -p` subprocess (terminal only). Inside Claude Code without an API key, LLM calls fail fast with guidance instead of hanging.

## Development

```bash
uv run pytest              # all tests (~660, <3s)
uv run pytest -m llm -v    # LLM E2E tests (slow, real API calls)
uv run pytest -x           # stop on first failure
```

See [CLAUDE.md](CLAUDE.md) for architecture details.

## License

MIT
