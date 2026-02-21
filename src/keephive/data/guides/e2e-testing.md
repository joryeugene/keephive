---
tags: [keephive, testing, e2e, terminal, tmux]
---

# E2E Terminal Testing Guide

keephive has a custom tmux-based terminal driver (`tests/terminal.py`) for end-to-end testing. Unlike mock-based unit tests, these tests type real commands into a real shell and read real screen output.

## When to Write a Terminal E2E Test

Use terminal E2E tests when the behavior involves:
- CLI output formatting (Rich, ANSI, layout)
- Multi-command workflows (remember, then recall, then verify staleness)
- Time-travel scenarios (set date, observe staleness aging)
- Hook JSON output (sessionstart, precompact, nudges)
- Profile isolation (separate hive directories)
- File creation side effects (daily logs, counter files)

Use unit tests (Tier 1) when testing:
- Pure functions, parsing, storage operations
- Pydantic model validation
- Error paths with controlled inputs

## Terminal Driver API

```python
from terminal import Terminal

# Create a session (auto-creates temp hive directory)
t = Terminal(tmp_path)

# Type commands and assert on output
t.type("python -m keephive s").has("keephive")
t.type("python -m keephive todo").has("Task A").lacks("completed")

# Chain assertions (returns Screen for fluent chaining)
screen = t.type("python -m keephive --version")
screen.matches(r"keephive v\d+\.\d+")
screen.has_ansi()

# Time travel (HIVE_DATE overrides date, not time)
t.set_date("2026-01-01")
t.type("python -m keephive r 'FACT: past event'")
t.set_date("2026-02-01")
t.type("python -m keephive s").has("stale")

# Advance days from a base date
t.advance_days(30, "2026-01-01")  # sets HIVE_DATE to 2026-01-31

# Read files created by commands
content = t.read_file("daily/2026-01-01.md")
assert "past event" in content

# Check file existence
assert t.file_exists("daily/2026-01-01.md")

# Screen read without typing (inspect current state)
screen = t.screen()

# Wait for text (for long-running or watch commands)
t.wait_for("expected text", timeout=5.0)

# Send single character (for y/n prompts)
t.send_char("y")

# Send special keys (Ctrl+C, Escape, etc.)
t.send_keys("C-c")

# Seed demo data (deterministic RNG)
t.seed(45)  # 45 days of demo data

# Save test history as JSON artifact
t.save_history(Path("artifacts/scenario.json"))

# Cleanup
t.close()
```

## Screen Assertions

| Method | What it checks |
|--------|---------------|
| `.has("text")` | Text appears in plain output |
| `.lacks("text")` | Text does NOT appear |
| `.has_ansi()` | ANSI escape codes present |
| `.matches(r"pattern")` | Regex found in output |
| `.line_count_between(lo, hi)` | Output line count in range |
| `"text" in screen` | `__contains__` check |

All assertion methods return `self` for chaining:
```python
t.type("cmd").has("A").has("B").lacks("C").has_ansi()
```

## Fixtures

```python
@pytest.fixture
def term(tmp_path):
    """Fresh terminal with empty hive directory."""
    t = Terminal(tmp_path)
    yield t
    t.close()

@pytest.fixture
def term_seeded(tmp_path):
    """Terminal with 45 days of seeded demo data."""
    t = Terminal(tmp_path)
    t.seed(45)
    yield t
    t.close()
```

Use `save_terminal_output` fixture to persist test history as JSON:
```python
def test_workflow(self, term, save_terminal_output):
    term.type("python -m keephive r 'FACT: test'")
    save_terminal_output("scenario_name", term)
```

## Hook Testing Patterns

Hook commands pipe JSON to stdin and produce JSON output. The terminal wraps long JSON lines at 120 chars, so use these helpers:

```python
def _hook_has(screen, *texts):
    """Assert texts in hook output, ignoring line wraps."""
    unwrapped = screen.plain.replace("\n", "")
    for t in texts:
        assert t in unwrapped, f"Expected {t!r}:\n{screen.plain}"

def _hook_lacks(screen, *texts):
    """Assert texts NOT in hook output."""
    unwrapped = screen.plain.replace("\n", "")
    for t in texts:
        assert t not in unwrapped, f"Unexpected {t!r}:\n{screen.plain}"

def _hook_json(screen) -> dict:
    """Parse JSON from hook output, handling line wrapping."""
    unwrapped = screen.plain.replace("\n", "").strip()
    start = unwrapped.find("{")
    end = unwrapped.rfind("}") + 1
    return json.loads(unwrapped[start:end])
```

Hook pipe commands MUST end with `; echo` to force a trailing newline before the END marker:
```python
# WRONG: JSON output may not have trailing newline, END marker stuck on same line
term.type("echo '{}' | python -m keephive hook-sessionstart")

# RIGHT: ; echo forces newline so END marker appears on its own line
term.type("echo '{}' | python -m keephive hook-sessionstart; echo")
```

## Gotchas

### TODO Text Must Be Distinct
Fuzzy dedup at 0.8 SequenceMatcher threshold eats similar names. "Fix auth bug" and "Fix auth error" will dedup to one. Use descriptive, distinct text:
```python
# BAD: too similar
term.type("python -m keephive t 'Task A'")
term.type("python -m keephive t 'Task B'")

# GOOD: distinct enough to survive dedup
term.type("python -m keephive t 'Fix authentication bug in login endpoint'")
term.type("python -m keephive t 'Write database migration for user profiles'")
```

### HIVE_DATE Overrides Date, Not Time
`set_date("2026-01-15")` makes `get_today()` return Jan 15, but `get_now()` still uses the real wall-clock time. Timestamps in log entries will have today's HH:MM:SS. Golden file comparison handles this via `_normalize_golden()`.

### SessionStart Output Contains Knowledge Guide Text
When checking `_hook_lacks(screen, "Error")`, beware that injected guides (like "Error Ownership" in agent-principles) will match. Check for `"Traceback"` instead of `"Error"`:
```python
# BAD: "Error Ownership" in guides causes false positive
_hook_lacks(screen, "Error")

# GOOD: Traceback indicates actual Python error
_hook_lacks(screen, "Traceback")
```

### Single Quotes in Commands
tmux `send-keys` can mangle single quotes. Prefer double quotes for text arguments:
```python
# Safer
term.type('python -m keephive r "FACT: something important"')
```

### HIVE_HOME Isolation
Every `term` fixture creates a fresh hive directory in a temp path. Commands never touch `~/.claude/hive/`. But `active_profile()` always returns None in HIVE_HOME mode (bypasses profile system).

### Prompt Interference
The terminal driver uses a three-layer defense against starship prompt interference:

1. **STARSHIP_CONFIG** set via `tmux -e` to a temp file with `format = "> "` (not `"$ "` which starship interprets as a variable reference)
2. **Nuclear prompt override**: `precmd_functions=(); preexec_functions=(); PROMPT='> '; RPROMPT=''` clears all zsh prompt hooks after shell init (starship registers `precmd` hooks that run the starship binary on every prompt render, 0.5-2s each)
3. **Scrollback wipe**: `clear` + `tmux clear-history` at end of `_start()` removes any starship parse errors from the initial shell startup

Without all three: starship renders a multi-line prompt with git status, Python version, battery %, etc. Commands pile up in the input buffer, END markers never appear as clean standalone lines, and `wait_for()` captures residual `[ERROR]` messages from scrollback.

If tests timeout, check that the prompt override is executing in `_start()`. Use `"Traceback"` in assertions instead of `"Error"` since starship emits `[ERROR]` during init.

### Claude Code Data Isolation
The terminal driver sets `HIVE_CC_META_DIR` to an empty temp directory, isolating stats/status output from real `~/.claude/usage-data/session-meta/` data. Without this, the Sessions section of stats/status reflects the host machine's actual Claude Code activity, making golden file comparison non-deterministic. Both `storage.cc_session_data()` and `insights.read_session_meta()` respect this env var.

## Golden File Testing

Golden files live in `tests/e2e_outputs/golden/*.txt`. They store baseline output for regression testing.

```python
from conftest import assert_golden

def test_status_golden(self, term_seeded, update_golden):
    screen = term_seeded.type("python -m keephive s")
    assert_golden(screen, "status_seeded", update=update_golden)
```

Non-deterministic values are normalized by `_normalize_golden()` in `conftest.py`:
- `HH:MM:SS` and `HH:MM` for wall-clock timestamps
- `pytest-NNN` for temp directory numbers
- `vX.Y.Z` for version strings
- Sparkline bars (`▁▂▃▄▅▆▇█`) collapsed to `▓`
- Stats/Status volatile counters: prompts/convo, streak, cmds today/week
- Session Quality: achieved %, session count, friction text
- Trends: all values (entirely from real usage data)
- Activity: day rows, source percentages, hook counts
- Tool usage percentages and change arrows
- Project line counts and recency

The normalizer ensures golden files don't break when real `~/.claude/usage-data/` changes between runs. If you add a new volatile field to stats/status output, add a corresponding normalization pattern.

Regenerate baselines: `just test-golden`

## Test File Organization

| File | Tests |
|------|-------|
| `test_terminal_driver.py` | Driver self-tests (echo, env, timeout, scrollback) |
| `test_e2e_scenarios.py` | Multi-command workflows, golden file comparisons |
| `test_e2e_adversarial.py` | Corrupt data, empty states, boundary conditions |
| `test_e2e_todo_nudge.py` | TODO discipline, nudge lifecycle, hook mechanics |
| `test_e2e_flows.py` | Unit-level flow tests (direct function calls, not terminal) |
| `test_e2e_verify_outputs.py` | Verify command output format tests |
| `test_e2e_llm.py` | Real LLM integration tests (slow, needs claude -p) |

## Running Tests

```bash
just test-e2e                    # all terminal E2E tests
just test-golden                 # regenerate golden baselines
just test-driver                 # terminal driver self-tests only

# Single test
just test-one "-m terminal -k test_near_duplicate -v -o addopts="

# Single file
just test-one "tests/test_e2e_todo_nudge.py -v -o addopts="
```

## Writing Quality E2E Tests

Every E2E test should answer: "What user-visible behavior would break if this code changed?"

1. **Tell a story**: Each test simulates a real user workflow (capture, recall, verify, age, reflect)
2. **Verify side effects**: Check files created, counters incremented, state changed
3. **Test across time**: Use `set_date()` to verify aging, staleness, lifecycle progression
4. **Save artifacts**: Use `save_terminal_output` for post-run analysis and debugging
5. **Include negative tests**: Verify that wrong inputs produce helpful errors, not crashes
6. **Assert data consistency**: After operations, read the underlying files and verify structure
