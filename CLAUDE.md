# keephive

A knowledge sidecar for Claude Code.

## Dev Commands

| Command                                      | What                                         |
| -------------------------------------------- | -------------------------------------------- |
| `uv run pytest`                              | Run all tests                                |
| `uv run pytest -m llm -v -o "addopts="`      | LLM E2E tests (slow, real claude -p)         |
| `uv run pytest -x`                           | Stop on first failure                        |
| `uv run pytest tests/test_claude_pipe.py -v` | The critical pipe tests                      |
| `uv run pytest -k "test_verify"`             | Run by name pattern                          |
| `uv run python -m keephive s`                | Status against real data                     |
| `uv run python -m keephive v`                | Verify stale facts (needs claude -p, 10-20s) |

## Architecture

- `cli.py`: Dispatch table. Maps command names to (module, function) tuples.
- `claude.py`: ALL claude -p interaction. One function, Pydantic validation. THE critical module.
- `models.py`: Pydantic models for every structured response (VerifyResponse, PreCompactResponse, ReflectAnalyzeResponse, VaultPerspective, CleanerPerspective, StrategistPerspective, AuditSynthesis).
- `output.py`: Console markup, `prompt_yn()`, `prompt_choice()`. Shared output helpers.
- `nudge.py`: Shared nudge infrastructure (counter-based, status-aware, rotating messages).
- `storage.py`: All file I/O for ~/.claude/hive/ directory. Includes stats tracking.
- `commands/audit.py`: Three-perspective LLM audit (parallel) + Cook synthesis. Uses `run_claude_pipe()` for all 4 calls.
- `commands/edit.py`: `hive e` targets (memory, rules, claude, today, todo, etc.). Opens `$EDITOR`.
- `commands/knowledge.py`: List, view, create/edit knowledge guides and prompt templates.
- `commands/note.py`: Multi-slot scratchpad. Open, copy, clear, list, restore, template start.
- `commands/session.py`: Interactive session launcher. Reuses `build_context()` from sessionstart, replaces process with `claude` via `os.execvpe`.
- `commands/skill.py`: Plugin/skill system for extensible commands.
- `commands/stats.py`: Usage statistics with per-project breakdown, streaks, and activity sparklines.
- `commands/todo.py`: TODO lifecycle: list, add, done, edit, recurring.
- `commands/verify.py`: LLM-powered fact verification. Checks facts against codebase, auto-corrects when deterministic.
- `commands/reflect.py`: Four-stage flow: scan (deterministic) → analyze (LLM) → apply (interactive review) → draft (generate guide).
- `commands/standup.py`: Standup generation from daily logs + GitHub PR data. Weekend-aware cutoff, clipboard copy.
- `commands/doctor.py`: Health check (hooks, MCP, deps, data). Uses LLM for semantic TODO deduplication.
- `commands/setup.py`: Registers MCP server in ~/.claude.json and hooks in ~/.claude/settings.json. Auto-syncs global install.
- `hooks/sessionstart.py`: Injects context at session start. No LLM call.
- `hooks/precompact.py`: Layer 1 extraction (deterministic) + Layer 2 auto-write to daily log (claude -p).
- `hooks/posttooluse.py`: Counter-based periodic nudge after Edit/Write tool use.
- `hooks/userpromptsubmit.py`: Counter-based periodic nudge + TODO detection from user prompts.

## The Rule

Every claude -p callsite uses `run_claude_pipe()` with a Pydantic response model. No raw JSON parsing anywhere else. If you add a new claude -p call, it goes through claude.py.

## Test Philosophy

Tests must catch real bugs. `test_claude_pipe.py` uses the ACTUAL response format from production (including system init messages in the array). If a test passes but production fails, the test is wrong, not the code.

## LLM Test Rule

LLM-dependent tests use `llm_hive_env` fixture + `@pytest.mark.llm`.
Run: `uv run pytest -m llm -v -o "addopts="`
`HIVE_SKIP_LLM=1` is ONLY for fast-path fixtures. NEVER use it to "test" an LLM feature — that skips the feature entirely and proves nothing.

