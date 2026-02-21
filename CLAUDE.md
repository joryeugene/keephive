# keephive

A knowledge sidecar for Claude Code.

## Dev Commands

Prefer `just <recipe>` over raw commands. See `just --list` or the `justfile` for all recipes.

**`hivedev` vs `hive`**: During development, always use `hivedev` (or `uv run python -m keephive`) to run the local dev build. The global `hive`/`keephive` commands point to the last `uv tool install` and may lag behind. The justfile defines `hivedev` as a variable for this reason. Terminal E2E tests use `python -m keephive` because they run inside an activated venv.

| Command                                    | What                                               |
| ------------------------------------------ | -------------------------------------------------- |
| `just test`                                | Run all unit/integration tests (<35s)               |
| `just test-e2e`                            | Terminal E2E tests (real tmux, ~65s)                |
| `just test-golden`                         | Regenerate golden file baselines                    |
| `just test-llm`                            | LLM E2E tests (real claude -p, slow)               |
| `just test-one tests/test_X.py`            | Single file, stop on first failure, verbose         |
| `just test-one "-k test_verify"`           | Run by name pattern                                |
| `just test-integration`                    | Multi-step state machine tests                     |
| `just lint`                                | ruff check + format check                          |
| `just fmt`                                 | ruff format in place                               |
| `just serve`                               | Live dashboard with hot reload                     |
| `just check`                               | All checks (test + lint + secrets)                  |
| `hivedev s`                                | Status against real data (local dev build)          |
| `hivedev v`                                | Verify stale facts (needs claude -p, 10-20s)        |

## Architecture

- `cli.py`: Dispatch table. Maps command names to (module, function) tuples. `_CANONICAL` maps aliases to canonical names (used for stats tracking and help).
- `claude.py`: ALL Anthropic API interaction. One function, Pydantic validation. THE critical module. Models: haiku 4.5, sonnet 4.6.
- `clock.py`: Centralized time functions. `get_today()`/`get_now()` respect `HIVE_DATE` env var for time-travel testing.
- `models.py`: Pydantic models for every structured response (VerifyResponse, PreCompactResponse, ReflectAnalyzeResponse, VaultPerspective, CleanerPerspective, StrategistPerspective, AuditSynthesis).
- `output.py`: Console markup, `prompt_yn()`, `prompt_choice()`. Shared output helpers.
- `nudge.py`: Shared nudge infrastructure. Priority-based lifecycle state machine (TODOs > stale facts > pending facts > unreflected logs > context-specific). Intervals: prompt/tool every 5, stop every 8.
- `storage.py`: All file I/O for ~/.claude/hive/ directory. Includes stats tracking, profiles, session metrics.
- `insights.py`: Pure deterministic aggregation of Claude Code `/insights` session data. Reads `~/.claude/usage-data/facets/` + `session-meta/`, joins on session_id, produces outcome/type/satisfaction/goal/friction distributions and cross-field pattern detection. No LLM.
- `commands/audit.py`: Three-perspective LLM audit (parallel) + Cook synthesis. Uses `run_claude_pipe()` for all 4 calls.
- `commands/memory.py`: `hive mem` and `hive rule` (add/remove/learn/review). `rule learn` reads `/insights` friction data from `~/.claude/usage-data/facets/`, maps to behavioral rules, deduplicates via trigram overlap, queues in `.pending-rules.md`.
- `commands/edit.py`: `hive e` targets (memory, rules, claude, today, todo, etc.). Opens `$EDITOR`.
- `commands/knowledge.py`: List, view, create/edit knowledge guides and prompt templates.
- `commands/note.py`: Multi-slot scratchpad. Open, copy, clear, list, restore, template start. `hive n todo` extracts TODOs via edit-buffer (full note, candidates pre-marked `- `, mtime cancel detection). `hive 4 "text"` quick-appends without editor.
- `commands/profile.py`: Profile CRUD (create/list/use/delete). Sibling directories: `~/.claude/hive-{name}/`.
- `commands/seed.py`: Demo data seeder. Deterministic RNG (`Random(42)`), loads from `data/demo/entries.json`.
- `commands/session.py`: Interactive session launcher. Reuses `build_context()` from sessionstart, replaces process with `claude` via `os.execvpe`.
- `commands/skill.py`: Plugin/skill system for extensible commands.
- `commands/stats.py`: Usage statistics with per-project breakdown, streaks, and activity sparklines.
- `commands/todo.py`: TODO lifecycle: list, add, done, edit, recurring. Fuzzy dedup at 0.8 similarity threshold.
- `commands/transfer.py`: Export/import hive data as tar.gz with manifest.json.
- `commands/verify.py`: LLM-powered fact verification. Checks facts against codebase, auto-corrects when deterministic.
- `commands/reflect.py`: Five-stage flow: scan (deterministic) → analyze (LLM) → apply (interactive review) → draft (generate guide) → insights (session quality patterns from /insights facets, no LLM).
- `commands/standup.py`: Standup generation from daily logs + GitHub PR data. Weekend-aware cutoff, clipboard copy.
- `commands/doctor.py`: Health check (hooks, MCP, deps, data). Uses LLM for semantic TODO deduplication.
- `commands/setup.py`: Registers MCP server in ~/.claude.json and hooks in ~/.claude/settings.json. Auto-syncs global install.
- `hooks/sessionstart.py`: Injects context at session start (memory, rules, TODOs, matched guides, cross-project hints). No LLM call.
- `hooks/precompact.py`: Layer 1 extraction (deterministic) + Layer 2 auto-write to daily log with project attribution (claude -p). TODO discipline: max 2 user-requested TODOs per compaction, speculative TODOs demoted to FACT. Auto-close: passes open TODOs to LLM, writes DONE for resolved items. Dedup threshold 0.8.
- `hooks/posttooluse.py`: Counter-based periodic nudge after Edit/Write tool use.
- `hooks/userpromptsubmit.py`: Counter-based periodic nudge. Injects `.ui-queue` content before nudge when present.
- `hooks/stop.py`: Stop hook. Increments turn counter per session, periodic micro-nudge (interval 8) to capture decisions or mark TODOs done.
- `hooks/sessionend.py`: SessionEnd hook. Finalizes session stats with accurate end timestamp. No stdout.
- `hooks/taskcompleted.py`: TaskCompleted hook. Auto-logs DONE entry to daily log when a task is marked complete.
- `commands/serve.py`: Live web dashboard (HTTP server, 8 views, markdown rendering, auto-refresh, `/ui-feedback` POST endpoint). Zero external deps.
- `commands/ui.py`: UI feedback queue CLI (`hive ui`/`ui-install`/`ui-clear`) + bookmarklet source as `javascript:` URL.

## The Rule

Every claude -p callsite uses `run_claude_pipe()` with a Pydantic response model. No raw JSON parsing anywhere else. If you add a new claude -p call, it goes through claude.py.

## Test Philosophy

Tests must catch real bugs. `test_claude_pipe.py` uses the ACTUAL response format from production (including system init messages in the array). If a test passes but production fails, the test is wrong, not the code.

## Test Quality Standards

Every test must answer: "What bug would this catch?"

### The 3-Question Gate
Before committing any test, it must pass all three:
1. **Can this test fail?** If the SUT has a bug, will this test actually catch it? If the test passes regardless of SUT correctness (e.g., asserting a mock returns what you told it), delete it.
2. **Is this test unique?** Does it exercise a different code path than existing tests? If two tests differ only in input values but hit the same branch, keep only the boundary case.
3. **Does this test assert correctness?** `assert result` is not a test. `assert result == expected_value` is. `assert "keyword" in output` is acceptable only when exact output is non-deterministic.

### Anti-patterns (auto-reject)
- Testing that a mock returns what you configured it to return
- 3+ tests for the same function with trivially different inputs (keep 1 representative + 1 boundary)
- Tests that only assert "no crash" without checking the actual result
- Tests where the assertion is weaker than the function's contract (e.g., `assert len(result) > 0` when you know the exact expected length)

### Required patterns
- Every test class needs at least one negative test (error input, missing data, corrupt state)
- State-changing functions need a "roundtrip" test (write -> read back -> verify)
- Functions with thresholds/boundaries need tests AT the boundary, not just well within it

## LLM Test Rule

LLM-dependent tests use `llm_hive_env` fixture + `@pytest.mark.llm`.
Run: `just test-llm`
`HIVE_SKIP_LLM=1` is ONLY for fast-path fixtures. NEVER use it to "test" an LLM feature — that skips the feature entirely and proves nothing.

## Editor Mock Pattern

Functions that open `$EDITOR` via `subprocess.run([editor, path])` use mtime to detect cancel (no write = mtime unchanged). Test mocks must account for this:

```python
# WRONG — no-op mock looks like cancel, 0 TODOs added
monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)

# RIGHT — touch updates mtime, content (already written) is read back
def accept_all(*args, **kwargs):
    Path(args[0][1]).touch()
monkeypatch.setattr("subprocess.run", accept_all)

# RIGHT — test cancel explicitly with no-op
monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)
# assert nothing was added

# RIGHT — delete a specific line
def delete_first_todo(*args, **kwargs):
    path = Path(args[0][1])
    lines = [ln for ln in path.read_text().splitlines() if not ln.startswith("- ")][:1_000]
    path.write_text("\n".join(lines))
```

`args[0]` is the command list `[editor, str(path)]`, so `args[0][1]` is the file path.

## Three-Tier Test Strategy

keephive has three test tiers. Each answers different questions.

### Tier 1: Unit/Integration (1373 tests, <35s)

```bash
just test                           # all tests
just test-one tests/test_X.py       # single file
```

Fast, isolated, mocked. Uses `hive_env` fixture (temp dir + `HIVE_HOME`). Tests individual functions, data transformations, file I/O. No real terminal, no real LLM.

**Use for:** Pure logic, parsing, storage operations, model validation, error paths, edge cases where you control all inputs.

### Tier 2: Terminal E2E (64 tests, ~65s, requires tmux)

```bash
just test-e2e                                                      # run all
just test-one "-m terminal -k test_single_fact -v -o addopts="     # one test
just test-golden                                                   # regen baselines
```

Real terminal sessions via tmux. Types actual commands, reads actual screen output. `HIVE_DATE` env var enables time-travel without mocking. Rich renders real ANSI to a real TTY.

**Use for:** Multi-command workflows, output format validation, time-travel scenarios (staleness, lifecycle), CLI argument handling, profile isolation, anything where the user experience matters.

**Fixtures:** `term` (empty hive), `term_seeded` (45 days of demo data), `save_terminal_output` (JSON artifact), `update_golden` (baseline flag).

**Golden files:** `tests/e2e_outputs/golden/*.txt` stores baseline output. Tests compare against baselines and fail with unified diff on mismatch. `--update-golden` regenerates them.

### Tier 3: LLM E2E (11 tests, real claude -p, slow)

```bash
just test-llm
```

Real LLM calls. Tests the full pipeline: prompt -> claude -p -> Pydantic validation -> CLI output. Expensive and slow. Uses `llm_hive_env` fixture.

**Use for:** Verifying LLM prompt quality, response parsing, model behavior changes. Run before releases or after changing prompts/models.

### When to Use Which Tier

| Scenario | Tier |
|----------|------|
| Testing a pure function | Unit (Tier 1) |
| Testing file read/write logic | Unit (Tier 1) |
| Testing Pydantic model validation | Unit (Tier 1) |
| Testing CLI output format | Terminal (Tier 2) |
| Testing multi-day workflow | Terminal (Tier 2) |
| Testing Rich rendering/colors | Terminal (Tier 2) |
| Testing time-sensitive behavior (staleness) | Terminal (Tier 2) |
| Testing command interaction sequences | Terminal (Tier 2) |
| Verifying LLM prompt produces valid output | LLM (Tier 3) |
| Testing after changing a prompt template | LLM (Tier 3) |
| Regression testing after model upgrade | LLM (Tier 3) |

### Emulator vs Direct: The Decision

**Use the terminal emulator** (Tier 2) when testing things the user sees and interacts with. The emulator gives you a real shell with persistent env vars, real Rich/ANSI rendering, real command sequencing. Use it for:
- Verifying CLI output text and formatting
- Multi-command workflows (remember -> recall -> verify staleness)
- Time-travel with `HIVE_DATE` across multiple commands
- Testing that commands create the right files

**Use direct commands** (`hive a`, `hive v`, `hive stats`) when you need to verify something works against real user data, not test data. Direct commands hit your actual `~/.claude/hive/` directory. Use them for:
- Smoke-testing a fix against real accumulated data
- Verifying LLM-dependent features (audit, verify) actually call the model
- Checking serve dashboard renders with real content
- Quick validation before committing

**Use unit tests** (Tier 1) for everything that doesn't need a terminal or real LLM. Logic, parsing, storage, validation. These run in <35s and catch 80% of bugs.

## Feature Development Workflow

Write the terminal test first, then make it pass. This is the standard approach for any feature that affects CLI behavior.

### 1. Write the terminal E2E test

```python
@pytest.mark.terminal
class TestNewFeature:
    def test_basic_workflow(self, term, save_terminal_output):
        """New feature does X when user does Y."""
        term.type("python -m keephive new-command arg").has("expected output")
        save_terminal_output("new_feature/basic", term)
```

### 2. Run it, watch it fail

```bash
just test-one "-m terminal -k test_basic_workflow -v -o addopts="
```

### 3. Implement the feature until the test passes

### 4. Add unit tests for edge cases

Cover error paths, validation, boundary conditions in Tier 1 tests where mocking is faster.

### 5. Generate golden baseline

```bash
just test-golden
```

### 6. Run all tiers to confirm no regressions

```bash
just test && just test-e2e
```

## Terminal Driver Reference

The tmux driver lives at `tests/terminal.py`. Key patterns:

```python
# Basic: type command, assert output
term.type("python -m keephive s").has("keephive")

# Chain assertions
term.type("python -m keephive todo").has("Task A").lacks("completed")

# Time travel
term.set_date("2026-01-01")
term.type("python -m keephive r 'FACT: past event'")
term.set_date("2026-02-01")
term.type("python -m keephive s").has("stale")

# Read files created by commands
content = term.read_file("daily/2026-01-01.md")
assert "past event" in content

# Check ANSI rendering
term.type("python -m keephive s").has_ansi()

# Regex match
term.type("python -m keephive --version").matches(r"keephive v\d+\.\d+")

# Line count range
term.type("seq 1 50").line_count_between(49, 51)

# Save history artifact
save_terminal_output("scenario_name", term)
```

**Gotchas:**
- TODO text must be distinct enough to survive fuzzy dedup (0.8 SequenceMatcher threshold). "Task A"/"Task B" will dedup. Use descriptive names.
- Single quotes in `send-keys` args need care. Prefer double quotes for fact text.
- `HIVE_HOME` isolation means commands never touch real `~/.claude/hive/`.
- Each `term` fixture creates a fresh tmux session with unique name. Cleanup is automatic.

