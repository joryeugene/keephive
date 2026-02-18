# Changelog

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
