# Changelog

## v1.1.0

### Features

- **Wander task (`hive wander`, `/play` view)**: KingBee gains unstructured free-thinking
  time. The daemon picks a seed (user-queued > cross-pollination > recurring-topic >
  stale-todo), runs a WebSearch-enabled free-thinking pass, and writes a wander document
  with thinking, memory connections, a hypothesis, and an open question. Surfaced at
  `/play` in the dashboard with card layout, 30-day sparkline, and seed queue management.
  Enable with `hive daemon enable wander`.

- **`/play` dashboard view**: Card-based wander log with thinking excerpts, memory
  connections, source badges, hypothesis callouts, and seed chip queue (add + remove).

- **`remove_wander_seed(index)` in storage**: New function for indexed seed removal
  via dashboard or API.

### Fixes

- **Anthropic API model IDs**: `anthropic_api.py` used dotted model names
  (`claude-haiku-4.5`, `claude-sonnet-4.6`) which returned 404 from the API. Fixed to
  hyphens (`claude-haiku-4-5`, `claude-sonnet-4-6`).

- **`/play` nav position**: Moved from after `/settings` to after `/brain` in the nav
  tab order (VIEWS dict insertion order).

## v1.0.0

### Features

- **Cross-agent support (Gemini CLI, Codex CLI)**: `keephive setup` now detects Gemini and
  Codex CLIs and installs lightweight hook shims that mirror the Claude lifecycle
  (SessionStart, BeforeTool, AfterModel for Gemini; notify bridge for Codex). All three
  agents write normalized telemetry under `~/.keephive/telemetry/<platform>/`. The `hive
  serve /brain` view shows which agents are active and feeding data back.

- **Multi-backend LLM routing** (`src/keephive/llm/`): New priority-ordered routing layer
  selects backends automatically: claude-cli (priority 10) → anthropic-api (20) → gemini-api
  (25) → openai-api (30) → none (99). Transient errors fall through to the next available
  backend. `claude.py` is now a thin adapter over this layer.

- **Profile storage migration**: All data migrates from `~/.claude/hive/` to
  `~/.keephive/`. Legacy paths remain as compatibility symlinks. `HIVE_HOME` default
  updated throughout.

- **Dashboard masonry layout and Brain view**: CSS masonry replaces fixed-grid layouts.
  `/brain` condenses working memory, rules, TODOs, and platform telemetry into a single
  high-density panel. `/settings` adds profile management and daemon status.

- **Keepbee mascot redesign**: Animated robot-bee rebuilt with component-based rendering
  (body, arms, wings, antennae per frame). 10 frames, deterministic RNG, pixel-art style.
  `make_pixel_bee.py` simplified from 524 to 322 lines.

- **Dashboard data URI consolidation**: Shared `_load_data_uri(filename, mime)` replaces
  duplicated `importlib.resources` + base64 encoding logic. `_get_favicon()` lazy-caches
  the GIF favicon on first request.

### Fixes

- **BackendTimeoutError propagation**: Timeouts in the LLM routing layer now raise
  `BackendTimeoutError` (a `ClaudePipeError` subclass) and are never retried. Previously,
  a timeout from `claude -p` would silently fall through to the next backend if an API key
  was present in the environment.

- **Transfer import crash on corrupt archives**: `tarfile.open()` for non-gzip or corrupt
  files now caught with `ReadError`/`CompressionError`/`OSError`, prints error and exits
  cleanly instead of unhandled traceback.

- **E2E profile delete test isolation**: `test_delete_active_profile` rewritten for correct
  `HIVE_HOME` behavior. The `active_profile()` guard is bypassed in `HIVE_HOME` mode; unit
  test covers it separately.

## v0.23.0

### Features

- **KingBee Daemon**: Background process (`hive daemon start`) that manages agent identity and automated maintenance.
  - **Agent Identity (SOUL.md)**: Persistent cross-project summary of the agent's specialized skills, strengths, and evolving personality. Injected into every `SessionStart`.
  - **Self-Improvement Loop**: Periodically scans daily logs to propose new skills, behavioral rules, or task optimizations. Proposals are queued for interactive review via `hive improve review`.
  - **Morning Briefing**: Synthesized briefing of pending tasks and recent cross-project activity injected into the first session of each day.
  - **Stale Check**: Automated background scan for facts that haven't been verified recently.
- **Guided Maintenance Flow (`hive flow`)**: Orchestrated one-pass maintenance pass. Walks the user through Triage, Fact Review, Rule Review, Improvement Review, and a full Verification pass.
- **Improvement Review (`hive improve`)**: Interactive TUI to accept, defer, or dismiss self-improvement proposals from KingBee. Supports Skill, Rule, Task, and Edit proposal types.
- **BM25 Recall Ranking**: `hive_recall` MCP tool and `hive rc` CLI now use BM25-ranked relevance instead of simple recency weighting for multi-tier search results.
- **Throttled `soul-update`**: PreCompact hook now triggers a throttled background `soul-update` (min 1h interval) to keep the agent's identity current without overhead.

### Fixes

- **PreCompact Soul Trigger**: Identity updates now fire during active work (via PreCompact) rather than only at `SessionEnd`, ensuring the "soul" stays fresh during long-running sessions.
- **Soul Summary Extraction**: Improved regex and LLM patterns for extracting the "Agent Identity" summary from `SOUL.md` for injection.
- **Daemon Resilience**: Atomic writes (tmp+rename), PID tracking, and 60s tick loop with daily/weekly task throttling.

### Tests

- 32 new tests covering KingBee daemon, identity injection, flow orchestration, and improvement review.
- New: `tests/test_soul.py` (10 tests), `tests/test_daemon.py` (22 tests).

## Unreleased

### Features

- **Keepbee mascot spread**: Dancing robot-bee now flanks the mascot in the README hero (bee | mascot | bee), replaces the SVG hexagon favicon with an animated GIF (lazy-loaded, SVG fallback), and appears on the dashboard settings page header.
- **Keepbee redesign**: Robot-bee animation rebuilt with component-based rendering (body, arms, wings, antennae per frame). 10 frames, deterministic RNG, pixel-art style. `make_pixel_bee.py` simplified from 524 to 322 lines.
- **Dashboard data URI consolidation**: Shared `_load_data_uri(filename, mime)` replaces duplicated `importlib.resources` + base64 encoding logic. `_get_favicon()` lazy-caches the GIF favicon on first request.

### Fixes

- **Transfer import crash on corrupt archives**: `tarfile.open()` for non-gzip or corrupt files now caught with `ReadError`/`CompressionError`/`OSError`, prints error and exits cleanly instead of unhandled traceback.
- **E2E profile delete test under HIVE_HOME**: `test_delete_active_profile` rewritten to test interactive prompt cancellation. The `active_profile()` guard is bypassed in `HIVE_HOME` mode; unit test covers it separately.

## v0.22.0

### Features

- **Lifecycle nudge engine**: Priority-based state machine replaces static message rotation. Nudge order: open TODOs > stale facts > pending facts > unreflected logs > context-specific fallback. Intervals: prompt/tool every 5, stop every 8.
- **Stop hook**: Turn counter per session with periodic micro-nudges (interval 8) to capture decisions or mark TODOs done.
- **SessionEnd hook**: Finalizes session stats with accurate end timestamp. Silent (no stdout).
- **TaskCompleted hook**: Auto-logs DONE entry to daily log when a task is marked complete.
- **PreCompact TODO discipline**: Speculative TODO detection demotes LLM narration to FACT, caps at 2 user-requested TODOs per compaction, auto-closes resolved TODOs via `completed_todos` field with match validation.
- **Ghost session filter**: Excludes IDE restart artifacts (0 prompts, 0 tools, <5s) from all session metrics.
- **Dashboard profile badge**: Nav bar shows active profile name when a non-default profile is selected.
- **Dashboard enhancements**: Tool trend arrows, pulse component breakdown, sparkline tooltips, session depth labels.
- **Demo asset pipeline**: `just demo-gif` and `just demo-screenshots` recipes. VHS tape script with 10-command walkthrough. Prompt template seeding in demo data.
- **`h` entrypoint**: Shortest possible alias for keephive CLI.

### Fixes

- **Audit model resilience**: `VaultPerspective`, `CleanerPerspective`, `StrategistPerspective` now default `issues` to `[]`. Prevents Pydantic crash when LLM omits the field.
- **Stats silent exception swallowing**: Session Quality and Session Metrics `except Exception: pass` replaced with stderr logging. Bugs in `insights.py` are now visible.
- **Todo watch hint spam**: `show_hint()` suppressed in `--watch` mode via `functools.partial` kwarg gating.
- **Test isolation for `hive go` sessions**: `hive_env` fixture clears `HIVE_SESSION_LAUNCHED` env var. Prevents session guard from skipping context injection when tests run inside a `hive go` session.
- **`_compute_tool_trends` helper**: Extracted from inline dashboard code (DRY).
- **`is_ghost_session` public API**: Renamed from private to public for reuse.
- **`_match_open_todo` validation**: Added match validation for auto-close TODO flow.
- **Removed dead `stop_hook_active` guard**.
- **Date-validated `_unreflected_log_count`**: Now validates date strings before counting.

### Tests

- 1774 tests total (unit/integration + terminal E2E). New: `test_e2e_todo_nudge.py` (611 lines, full nudge lifecycle). Expanded: `test_nudge.py`, `test_precompact.py`.

### Developer

- Python minimum bumped to 3.13. Dropped 3.12 classifier.
- README restructured with feature table and lifecycle diagram.

## v0.20.0

### Features

- **Watch mode (`--watch`)**: Live-refresh for status, log, and todo. Detects file changes via mtime polling and re-renders automatically. New `just watch`, `just watch-log`, `just watch-todo` recipes.
- **Pending-facts staging queue**: Auto-captured facts from precompact now route to `.pending-facts.md` instead of directly modifying memory. `hive mem review` provides interactive y/N/edit review before promoting to working memory. Status and sessionstart surface pending fact counts.
- **Next-action hints**: `show_hint()` helper adds contextual `→` hints after command output. Added to todo, doctor, profile, stats, seed, gc, and transfer commands.
- **Dashboard keepbee logo**: Animated pixel bee in the nav brand. `_keepbee_data_uri()` loads from package data.
- **GitHub CI + templates**: CI workflow (lint + test on Python 3.12/3.13), bug/feature issue templates, PR template.

### Fixes

- **Output consistency**: Replaced all `->` with `→` across 13 command files. Standardized empty-state messages to `[dim]No {noun} yet[/dim]` pattern.
- **Clearer user-facing messages**: 13 cryptic messages rewritten with actionable hints. "Play" to "action", "stale" to "unverified 30+ days", abbreviated commands expanded.
- **`nudge.py` ZeroDivisionError**: `_nudge_interval()` crashed when `HIVE_NUDGE_INTERVAL=0`. Now floors to `max(1, ...)`.
- **`cmd_mem_review` backup safety**: Single `backup_and_write` pass for corrections + additions (was calling twice, second overwriting first `.bak`).

### Tests

- 1718 tests total (1597 unit/integration + 11 integration sequences + 108 terminal E2E + 11 LLM).
- **Test quality lift**: Removed ~46 tautological/redundant tests, added 28 edge-case tests, 11 multi-step integration tests.
- New: `test_integration_sequences.py` (todo lifecycle, memory normalization, recurring done across days, precompact pipeline).
- New: `test_watch.py` (watch mode parsing, mtime detection, loop behavior).
- **Test quality gate**: 3-Question Gate standard added to CLAUDE.md with anti-patterns and required patterns.
- `integration` pytest marker + `just test-integration` recipe.

### Developer

- **Justfile**: `test-integration`, `watch`, `watch-log`, `watch-todo` recipes.
- **pyproject.toml**: `integration` marker, `Environment :: Console` classifier.

## v0.19.0

### Features

- **Centralized time (`clock.py`)**: All date/time calls route through `get_today()`/`get_now()`. `HIVE_DATE=YYYY-MM-DD` env var overrides both, enabling deterministic time-travel across subprocess boundaries.
- **Profiles (`hive profile`)**: Create, list, use, and delete isolated data profiles. Each lives in `~/.claude/hive-{name}/`. `hive profile create demo --seed` bootstraps with demo data.
- **Demo seeder (`hive seed`)**: `--days N --force` generates realistic history with deterministic RNG. Populates all data files from `data/demo/entries.json`.
- **Export/import**: `hive export` creates tar.gz with manifest.json. `hive import archive.tar.gz --profile staging` restores to a new or existing profile. Path traversal protection.
- **Terminal E2E framework**: tmux-backed driver with Screen assertion API, golden file baselines, and output artifact tracking. `just test-e2e` runs 64 tests in ~65s.
- **Dashboard improvements**: Pipeline alias tracking, accordion state persistence via localStorage, CSS grid layout.

### Fixes

- **Sonnet model ID**: `claude-sonnet-4-5-20250514` (404) -> `claude-sonnet-4-6`.
- **Command stats canonicalization**: Aliases (v, rf, a) now resolve to canonical names in stats tracking.

### Tests

- 1449 tests total (1374 unit/integration + 64 terminal E2E + 11 LLM).
- New: `tests/terminal.py` (tmux driver), `test_e2e_scenarios.py` (45 scenarios), `test_terminal_driver.py` (19 driver tests)
- New: `test_clock.py` (9), `test_profile.py` (21), `test_seed.py` (11), `test_transfer.py` (8)
- Golden baselines: help, status (empty/seeded), stats (empty/seeded)
- Three-tier test strategy documented in CLAUDE.md

### Developer

- **Justfile**: `test-e2e`, `test-golden`, `test-one`, `serve` recipes added.
- **pytest markers**: `terminal` added alongside `llm`, both excluded from default run.

## v0.18.1

### Features

- **`hive rule learn`**: Friction-to-rules pipeline. Reads `/insights` friction data from `~/.claude/usage-data/facets/`, maps repeated friction patterns to behavioral rules, deduplicates via trigram overlap, queues candidates in `.pending-rules.md` for human review.
- **Verify batch processing**: `hive v` processes all facts in batches of 8 with continue/stop prompts between batches. Increased `max_turns` from 12 to 25 to prevent failures on complex fact batches.
- **Cognitive restructure**: Stats pipeline consolidated. Adaptive help shows recently-used commands first with a Discover section for unused commands. Dashboard polish pass.

### Fixes

- **Double `[verified:]` tags**: All memory write paths (precompact auto-correct, precompact auto-add, reflect apply, reflect edit, reflect contradiction update) now strip existing `[verified:]` tags before appending a new one. Prevents `[verified:X] [verified:X]` accumulation.
- **`normalize_memory()` cleanup pass**: Runs automatically after `hive v`. Fixes double tags, removes resolved TODOs, corrects `- - ` malformed prefixes, and deduplicates identical lines. Reports a cleanup summary.

### Tests

- 1335 tests passing.
- New: `tests/test_normalize.py` (16 tests for tag stripping, normalization, and write-path fixes)
- New: `tests/test_rule_learn.py` (23 tests for friction-to-rules pipeline)

## v0.18.0

### Features

- **Settings system**: `hive set <key> <value>` persists user preferences to `~/.claude/hive/.settings.json`. First setting: `sound` on/off for completion notifications on slow LLM commands.
- **Sound notifications**: `notify_sound()` plays a system sound after verify, audit, reflect, standup, doctor, and log summarize. Controlled via `hive set sound off`.
- **Stats dashboard consolidation**: Reduced from 6+ stat panels to 4: Activity, What You Use (with tool breakdown), Trends, Quality. Eliminated duplicate sparklines and KPI overlap.

### Fixes

- **LLM timeout**: Bumped from 120s to 240s for commands with large input (audit, reflect analyze, verify).

## v0.17.0

### Features

- **Session-level productivity metrics**: `session_id` persisted to `~/.claude/hive/sessions.json`. PostToolUse tracks tool name counts, UserPromptSubmit tracks prompt count per session. Stats CLI and serve dashboard show Sessions section with avg prompts/session, duration, tool breakdown, compaction rate, and per-project counts.

## v0.16.0

### Dashboard

- **View consolidation (8 to 4)**: Reduced from 8 views to 4: Home (`/`), Dev (`/dev`), Knowledge (`/know`), Stats (`/stats`). Old paths (`/daily`, `/simple`, `/mem`, `/notes`) redirect to their replacements.
- **Keyboard navigation**: Vim-style keybindings for the dashboard. `j`/`k` between card rows, `h`/`l` between cards in a row, `Enter`/`o` to dive into items, `Escape` to return. `gg`/`G` for top/bottom. `g` prefix with 800ms timeout for view shortcuts (`ga`, `gd`, `gk`, `gs`). `/` to focus search. `?` toggles shortcut overlay.
- **ARIA and tabindex**: Every interactive element has proper `role`, `tabindex`, `aria-label`, and `aria-expanded` attributes. Cards, accordions, todos, log entries, notes, search overlay, nav, and filter buttons all accessible.
- **Superhuman aesthetic**: Tighter spacing, faster transitions (150ms to 100ms), Inter font stack, custom focus ring (blue inset border replacing browser default), active tab enhancement.
- **Search upgrade**: Dashboard search now uses `_search_all_tiers()` (same as CLI `hive rc`), returning scored multi-tier results with context lines, tier badges, and action buttons (Promote to Memory, Copy).
- **Edit-in-dashboard**: `GET /api/content`, `POST /api/edit`, `POST /api/preview` endpoints. Full-screen modal with live markdown preview, `Ctrl+Enter` to save, `Escape` to cancel. Edit memory, guides, notes, and rules from the browser.
- **Smart deduplication**: Knowledge panel detects when a guide maps to a CLI command and shows a unified entry with both the guide name and the command alias.
- **Knowledge tabbed panel**: Guides, Memory, and Notes consolidated into a single tabbed panel on the `/know` view. Client-side tab switching.
- **Log show-more pagination**: Truncated log panels show "show next 25" and "show all" buttons instead of static text. `loadLogMore(limit)` fetches incrementally via `/api/fragment?view=log&limit=N`.

### CLI

- **Auto-classify remember**: `hive r` auto-detects category (FACT, DECISION, TODO, INSIGHT, CORRECTION) from text content when no explicit prefix is given. Shows `[auto: CATEGORY]` in output.
- **Contextual "Next:" in status**: `hive s` now shows a priority-cascaded next action suggestion (stale facts, old TODOs, pending rules, memory bloat, due recurring tasks, etc.).
- **SessionStart next-action hint**: The suggested next action is injected into session context so the agent is aware of maintenance needs.
- **Removed `hive friction` command**: Cut entirely. Friction matrix guide remains as a knowledge artifact.

### Fixes

- **cmd-hint cursor**: Changed from `cursor:copy` (shows as plus icon on macOS) to `cursor:default`.

### Tests

- 1135 tests passing.

## v0.15.1

### Fixes

- **Favicon rendering**: Base64-encode the SVG favicon data URI so the HTML parser no longer truncates it at `<polygon`. Amber honeycomb hexagon now appears in the browser tab.

### Context injection

- **Project attribution**: PreCompact hook now tags each classified insight with `[project:name]` in the daily log. Cross-project search and attribution enabled.
- **`always: true` guides**: Knowledge guides with `always: true` in their YAML front matter are injected into every session regardless of the current project name. Opt-in only; no guides ship with this flag. Each always-inject guide costs one of the three guide slots (1500-word budget).
- **Cross-project hints**: SessionStart hook scans recent daily logs for insights from other projects and injects a one-liner hint, enabling the agent to `hive_recall()` across projects when relevant.

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
