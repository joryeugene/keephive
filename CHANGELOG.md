# Changelog

## v0.15.0

### Dashboard

- **`hive serve [port]`** (`hive ws`): Live web dashboard at localhost:3847 with 8 views (all, daily, dev, simple, stats, know, mem, notes). Auto-refresh with configurable interval, dark theme, zero external dependencies.
- **Markdown rendering**: Knowledge guides, prompts, and notes render as formatted HTML (headers, bold, code blocks, tables, lists, links). Pure Python stdlib regex renderer, no CDN calls.
- **Activity merged into Status panel**: Commands today, this week, streak, and hourly heatmap display inline in the Status card. Separate stats-summary panel removed from All view.
- **Knowledge-compact panel**: Flat list with type badges (guide/prompt/skill) for the Dev view. Links to `/know` for full detail.
- **Stats-commands split**: Sparkline+heatmap in the Stats panel, command breakdown table in a separate Stats-commands panel.
- **Dev view expansion**: `todos-brief` + `log-brief` row for active work context alongside facts and reference material.
- **View reordering**: Dynamic content (log, TODOs) at top, static content (standup, knowledge, memory) at bottom. Applied to Daily and Dev views.
- **Sparkline polish**: 14-day bars with day-of-week labels, weekend shading, and today highlight.
- **Loading states**: Opacity transition on refresh, button disable during CRUD operations.
- **Log type filters**: Filter bar appears when >10 entries with multiple types. Client-side show/hide by category.
- **Note slot switcher**: Buttons to switch active slot from the Notes panel. `POST /api/note/switch` endpoint.
- **Note previews**: Collapsed note accordions show a text preview snippet in the header.
- **Web CRUD endpoints**: `POST /api/remember`, `/api/todo/add`, `/api/todo/done`, `/api/note/append`, `/api/note/switch`. Forms embedded in Log, TODO, and Notes panels.
- **Search cleanup**: Session log lines filtered from results, `- [HH:MM:SS] ` prefix stripped from display.
- **`hive ui`**: Show pending UI feedback in queue.
- **`hive ui-install`**: Print bookmarklet `javascript:` URL to drag to bookmarks bar. Click any element on `hive serve` (or any page), add a note via voice or typing, submit — context is queued and injected into your next Claude Code prompt.
- **`hive ui-clear`**: Discard pending UI feedback queue.

### Guides

- **keephive-lifecycle.md**: New bundled guide documenting the information hierarchy (daily log at the base, working memory for verified facts, knowledge guides for deep reference, archive for old logs).

### Hooks

- **UserPromptSubmit**: Reads `.ui-queue` before nudge logic. When a bookmarklet submission is pending, injects `[UI Feedback]` element context as `additionalContext` and clears the queue, skipping the nudge for that turn.

## v0.14.0

### Features

- **Context-aware memory decay**: `score_fact_decay()` weights facts by recency, reference count, and importance tier. `hive gc` uses these scores to surface archive candidates. Frequently recalled facts decay slower.
- **Verification evidence**: `hive v` now records evidence summaries alongside verdicts, showing what codebase data confirmed or contradicted each fact.
- **Recall frequency tracking**: FTS5 search hits bump a per-fact counter. Frequently accessed facts get a decay bonus, keeping actively used knowledge from going stale.
- **Context injection diet**: SessionStart hook no longer injects maintenance noise (Quality Pulse scores, accumulation warnings, data quality notes, guide notifications, recent entry previews). These moved to `hive s` CLI output, reducing token overhead and improving model focus.

### Tests

- 31 new tests in `test_audit_features.py`. 912 tests passing.

## v0.13.2

### Fixes

- **Seed-only session start**: `_seed_bundled_content` no longer overwrites user-customized guides on every session start. Overwrites happen only when the installed guide is absent or when `hive setup` is run explicitly.

## v0.13.1

### Features

- **Auto-sync bundled guides on session start**: Bundled guides are synced to `~/.claude/hive/guides/` on every session start, keeping them current after upgrades without requiring a manual `hive setup`.

## v0.13.0

### Features

- **`hive n todo` (edit-buffer review)**: Extract action items from a note slot. Both plain text lines and bullet lines under a `## todo` section become candidates; items over 120 chars are silently dropped as observations. Single item: `(y/n)` prompt. Multiple items: opens `$EDITOR` with the **full note**, candidates pre-marked with `- `, non-candidate bullets stripped to plain text context. Delete or rephrase any `- ` lines, save to confirm. Exiting without saving (no mtime change) cancels — works with neovim `:q!`.
- **`hive 4 "text"` quick-append**: Append text directly to a note slot without opening an editor. `hive 4 "fix auth bug"` appends to slot 4; multi-word bare args are joined automatically.
- **Bare-digit note dispatch**: `hive 4`, `hive 4 todo`, and `hive 4 "text"` all work — bare digits route to `cmd_note_slot` without needing the `n.` prefix.
- **Active draft indicator**: `hive s` shows a single consolidated "Active draft: slot N · preview (W words)  ->  hive nc" line at the bottom. Removed the duplicate early hint.

### Fixes

- **Input hardening (ESC + unrecognized keys)**: In `prompt_yn` and `prompt_choice`, pressing ESC cancels and returns False/no. Any unrecognized key also cancels (no longer falls through to the default). Only `y`, `n`, Enter, and Space are accepted.
- **4× context duplication in `hive go`**: The `HIVE_SESSION_LAUNCHED` env var guard never worked because Claude Code's daemon inherits its own environment, not the CLI's. Replaced with a file-based signal: `session.py` writes `~/.claude/hive/.session-launched` with a Unix timestamp before `os.execvpe`; `sessionstart.py` reads the file, skips injection if timestamp is <15 seconds old, then deletes it.
- **Guide sync on upgrade**: `_seed_bundled_content` now overwrites installed guides when bundled content differs (not only when missing). Run `hive setup` after upgrading to sync guides to the latest bundled version.
- **Note extraction includes plain lines**: `_extract_structured_items` now collects both plain text task lines and bullet lines from `## todo` sections. Previously only bullet-prefixed lines were extracted, causing plain-line tasks to be missed.

### Tests

- 867 tests total (up from 823)
- New: `tests/test_note_todify.py` (18 tests — structured extraction, LLM fallback, edit-buffer review, bare-digit dispatch, long-item filtering, plain-line extraction, mtime cancel detection, `_build_todo_buffer` helper)
- Extended: `tests/test_note.py` — 5 new tests for quick-append (`hive 4 "text"`, multiword, newline handling, editor fallback, list subcommand)
- Extended: `tests/test_sessionstart_logic.py` — `TestSessionSignal` class (3 tests: recent signal blocks injection, stale signal allows injection, no signal runs normally)
- Updated: `tests/test_note.py`, `tests/test_e2e_flows.py` — note indicator assertions updated to new "Active draft" format
- Updated: `tests/test_setup_hooks.py` — `test_updates_if_content_differs` reflects new guide sync-on-upgrade behavior

## v0.12.16

### Docs

- **README cohesion**: Loop and command table now share one vocabulary — capture / recall / verify / correct — instead of two separate naming schemes.
- **`hive` bare**: Quick start, example block, and command table now all show `hive` (no subcommand) as the canonical status shorthand.

### Developer

- **Release pipeline**: `just release` is now fully rerunnable. The version-file commit is skipped with `git diff --cached --quiet ||` when the files are already committed, preventing a hard fail on re-runs after partial releases.

## v0.12.15

### MCP

- **`hive_standup`**: Generate a standup summary from recent activity via MCP tool.
- **`hive_prompt`**: List and retrieve prompt templates via MCP tool.
- **`hive_ps`**: Expose active session map to Claude Code via MCP tool.
- **`hive_recurring`**: Extended with full CRUD — add, edit, remove, and list recurring tasks.

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
