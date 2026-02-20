# Changelog

## v0.12.x

### New Commands

- **`hive up`** (`hive update`): Check PyPI for a newer version and upgrade in place. Runs `keephive setup` after upgrading to sync hooks and MCP registration.
- **`hive ps`**: Local hive map — active Claude Code sessions (via lsof), project activity, git branch + worktree count.

### Features

- **Session flags**: `hive go -c` (compact mode), `-r` (resume last session), stdin piping for scripted launch.
- **Auto-copy**: `hive l`, `hive k`, `hive p` copy output to clipboard automatically in TTY mode.
- **Pipe mode**: Content commands output plain text when stdout is piped.
- **Recall context**: `hive rc` shows surrounding log lines for each match.
- **Knowledge attribution**: Guides display their source file path.
- **Todo undo**: `hive todo done` shows an undo hint; undo within the same session is supported.
- **Recurring visibility**: Completed recurring tasks remain visible in `hive todo repeat`.
- **LLM cost docs**: `hive k keephive-guide` documents model and cost for each LLM-powered feature.

### Developer

- **justfile**: `just test`, `just lint`, `just fmt`, `just check-private`, `just check` for contributors.
- **Release pipeline**: `.just/release.just` (gitignored) with `just release <version> "<desc>"` — version bump, sync, check, commit, build, PyPI publish, GitHub release, local upgrade.
- **ruff**: Added as dev dependency. Full codebase linted and formatted.

### Fixes

- **Ctrl+C in prompts**: `tty.setraw()` disables ISIG so Ctrl+C sent `\x03` as data rather than raising SIGINT — now caught explicitly, prints `cancelled`, exits cleanly.
- **`hive l summarize`**: Header-only log (`# Daily Log: DATE`) was falsely treated as having entries. Now checks for `- ` lines.
- **Test suite hang**: `test_empty_log_exits_gracefully` was removing `HIVE_SKIP_LLM` and triggering a real API call when `CLAUDECODE` and `ANTHROPIC_API_KEY` were both set.
- **PS session tracking**: Uses lsof to map Claude processes to actual working directories, excluding `-p`, Electron helpers, and grep.
- **Doctor content drift**: Detects when installed hooks or MCP config diverge from the source package.

## v0.11.0

### Features

- **Smart Recall (FTS5)**: `hive rc <query>` uses SQLite FTS5 for ranked full-text search over daily logs and archive. `hive gc` rebuilds the index. Falls back to grep when the index is absent.
- **Log Summarize**: `hive l summarize` pipes today's log to claude-haiku and prints 3-5 bullet highlights.
- **Memory Decay Scoring**: `hive gc` scores all working memory facts (recency + references + importance) and lists the bottom 5 as archive candidates with interactive (a)rchive/(k)eep prompts.
- **Path-Aware Guide Injection**: Knowledge guides with `paths: [/fragment]` front matter now inject only when the session cwd matches, not just on project name.
- **Anthropic Memory Awareness**: `hive dr` and `hive s` detect active Anthropic official memory and surface it as informational context.
- **MCP `hive_fts_search`**: New MCP tool exposes FTS5 search directly to Claude Code.

### Fixes

- `check_data()` no longer requires `rules.md` to exist (was a false negative in `hive dr`)
- `backup_and_write()` is now atomic: writes to `.tmp` then `os.replace()` (was a non-atomic overwrite)
- `apply_verdicts()` and `_auto_reverify()` now strip-then-append verified tags, making them idempotent regardless of starting state (was producing duplicate tags on repeated runs)

### Tests

- 728 tests total (up from 669)
- New: `tests/test_skill.py` (17 tests, full skill system coverage)
- New: `tests/test_recurring.py` (19 tests, `parse_freq`, `due_recurring`, `mark_recurring_done`)
- New: `tests/test_log_summarize.py` (3 fast + 1 real LLM test via `@pytest.mark.llm`)
- Extended: `tests/test_reflect_logic.py` (reflect apply loop with `--auto` flag)
- Extended: `tests/test_stats.py` (display function coverage)
- Extended: `CLAUDE.md` with LLM Test Rule (fast vs LLM test patterns)

## v0.10.0

### UX

- Y/n confirmation prompts on all destructive and LLM operations
- Per-command `--help` for every command
- `hive e todo` opens the TODO file with diff-on-save to detect external changes
- `hive v` now supports `--all` to verify every fact (not just stale ones)
- `hive rf apply` interactive review with accept/skip/edit per pattern
- Knowledge guide prefix matching (`hive k str` matches `strategy`)

### Fixes

- `keephive setup` auto-syncs global install when source is newer
- `prompt_yn()` returns default when stdin is not a TTY (fixes scripted usage)

### Docs

- README rewrite with feature descriptions, status example, install options
- CLAUDE.md architecture section expanded

## v0.9.0

First public release.

A knowledge sidecar for Claude Code. Captures what you learn, verifies it stays true, surfaces it when relevant.

### Core

- **Memory tiers**: Working memory, rules, knowledge guides, daily logs, archive
- **Auto-capture**: PreCompact hook extracts insights from conversations, writes classified entries to daily log
- **Context injection**: SessionStart hook loads memory, rules, TODOs, stale warnings, and matching knowledge guides
- **Verification**: Facts carry `[verified:YYYY-MM-DD]` timestamps. `hive v` checks stale facts against the codebase with LLM analysis.
- **Two-tier LLM routing**: `ANTHROPIC_API_KEY` set -> direct Anthropic API (works inside Claude Code); unset -> `claude -p` subprocess (terminal only)
- **MCP server**: 14 tools exposed natively to Claude Code via `keephive mcp-serve`

### Commands

- `hive status`, `hive remember`, `hive recall`, `hive verify`, `hive reflect`
- `hive session` / `hive go`: Interactive sessions with built-in modes (todo, verify, learn, reflect) and custom prompt support
- `hive todo`, `hive todo done`, `hive todo repeat` (recurring tasks)
- `hive audit`: Quality Pulse with three-perspective LLM analysis + synthesis
- `hive standup`: GitHub PR integration, weekend-aware cutoff, Slack clipboard copy
- `hive stats`, `hive log`, `hive note`, `hive knowledge`, `hive doctor`, `hive gc`

### Hooks

- **SessionStart**: Context injection, auto-reverify stale facts against recent logs, accumulation warnings
- **PreCompact**: Insight extraction, quality filters (garbage rejection, dedup, secret redaction), memory auto-update
- **PostToolUse**: Periodic nudge after edits
- **UserPromptSubmit**: Periodic nudge + TODO detection from user prompts

### Install

- `keephive setup` registers MCP server and hooks automatically
- Doctor detects version drift and stale deps in the global install
- Setup auto-syncs by running `uv tool install --force .` when needed
- `hive setup uninstall` removes hooks while preserving data
