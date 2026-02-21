# Import private recipes if present (no error when absent)
import? '.just/release.just'

# hivedev: always runs the LOCAL dev build (not the global `hive` install)
hivedev := "uv run python -m keephive"

# List available recipes
default:
    @just --list

# ── Dev ──────────────────────────────────────────────────────────────────────

# Run tests (fast, no LLM)
test:
    uv run pytest

# Run terminal E2E tests (real tmux, ~65s)
test-e2e:
    uv run pytest -m terminal -v -o "addopts="

# Regenerate golden file baselines
test-golden:
    uv run pytest -m terminal --update-golden -o "addopts="

# Run LLM integration tests (slow, requires claude CLI)
test-llm:
    uv run pytest -m llm -v -o "addopts="

# Run integration (multi-step state machine) tests
test-integration:
    uv run pytest -m integration -v -o "addopts="

# Run a single test file or pattern (e.g. just test-one tests/test_smoke.py)
test-one target:
    uv run pytest {{target}} -xvs

# Run terminal driver self-tests only (fast sanity check for tmux driver)
test-driver:
    uv run pytest tests/test_terminal_driver.py -xvs -o "addopts="

# Run TODO/nudge E2E tests only
test-todo:
    uv run pytest tests/test_e2e_todo_nudge.py -v -o "addopts="

# Run adversarial E2E tests only
test-adversarial:
    uv run pytest tests/test_e2e_adversarial.py -v -o "addopts="

# Run only previously-failing tests (for debugging regressions)
test-fail:
    uv run pytest --lf -xvs -o "addopts="

# Run all non-LLM tests (unit + integration + terminal E2E)
test-all: test test-e2e

# Coverage report (html in htmlcov/)
test-cov:
    uv run pytest --cov=keephive --cov-report=term-missing --cov-report=html -o "addopts=-m 'not llm and not terminal' --strict-markers"

# Run quick smoke tests only
test-smoke:
    uv run pytest tests/test_smoke.py tests/test_cli_dispatch.py -xvs

# Profile test timing (find slow tests)
test-timing:
    uv run pytest --durations=20 -q

# Run tests and save results to .test-results.txt (for Claude Code / CI output capture)
test-save:
    #!/usr/bin/env bash
    set -euo pipefail
    ts=$(date +%Y%m%d_%H%M%S)
    echo "=== Test run: $ts ===" | tee .test-results.txt
    uv run pytest -q 2>&1 | tee -a .test-results.txt
    echo "EXIT:$?" >> .test-results.txt
    echo "Results saved to .test-results.txt"

# Run specific tests and save results (e.g. just test-save-one tests/test_stats.py)
test-save-one target:
    #!/usr/bin/env bash
    set -euo pipefail
    ts=$(date +%Y%m%d_%H%M%S)
    echo "=== Test run: $ts — {{target}} ===" | tee .test-results.txt
    uv run pytest {{target}} -xvs 2>&1 | tee -a .test-results.txt
    echo "EXIT:$?" >> .test-results.txt
    echo "Results saved to .test-results.txt"

# Run E2E tests and save results (terminal tests need -o "addopts=" to override pyproject)
test-save-e2e:
    #!/usr/bin/env bash
    set -euo pipefail
    ts=$(date +%Y%m%d_%H%M%S)
    echo "=== E2E run: $ts ===" | tee .test-results.txt
    uv run pytest -m terminal -v --tb=short -o "addopts=" 2>&1 | tee -a .test-results.txt
    echo "EXIT:$?" >> .test-results.txt
    echo "Results saved to .test-results.txt"

# Lint with ruff
lint:
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/

# Format in place
fmt:
    uv run ruff format src/ tests/

# ── Quality Gates ────────────────────────────────────────────────────────────

# Fast pre-flight: format + lint + secrets (runs in ~2s, catches most issues)
pre-flight: fmt lint check-private

# Run all checks (test + lint + privacy scan)
check: test lint check-private

# Full pre-release check: pre-flight + all tests including terminal E2E
check-release: pre-flight test test-e2e

# Check for accidentally committed secrets / private data
check-private:
    @echo "Scanning for secrets..."
    @! grep -rn "sk-ant-api" src/ tests/ 2>/dev/null || (echo "ERROR: Anthropic key found" && exit 1)
    @! grep -rn "-----BEGIN.*PRIVATE KEY" src/ tests/ 2>/dev/null || (echo "ERROR: Private key found" && exit 1)
    @! grep -rn "ANTHROPIC_API_KEY\s*=" src/ tests/ 2>/dev/null || (echo "ERROR: API key assignment found" && exit 1)
    @echo "OK: no secrets found"

# ── Install & Sync ───────────────────────────────────────────────────────────

# Upgrade global install + sync hooks/MCP (run after release or pulling updates)
upgrade:
    uv tool upgrade keephive
    keephive setup
    @echo "Global install upgraded and synced"

# Sync dev environment (deps + local editable install)
sync:
    uv sync
    @echo "Dev environment synced"

# ── Dashboard ────────────────────────────────────────────────────────────────

# Live dashboard with hot reload
serve:
    {{hivedev}} serve --hot

# ── Watch Mode ─────────────────────────────────────────────────────────────

# Status in live-watch mode (auto-refresh on changes)
watch:
    {{hivedev}} s --watch

# Log in live-watch mode
watch-log:
    {{hivedev}} l --watch

# TODOs in live-watch mode
watch-todo:
    {{hivedev}} todo --watch

# ── Demo Assets ────────────────────────────────────────────────────────────

# Reset demo profile with rich seed data (60 days)
demo-seed:
    HIVE_HOME="$HOME/.claude/hive-demo" {{hivedev}} seed --force --days 60

# Record CLI demo GIF (requires vhs: brew install charmbracelet/tap/vhs)
demo-gif: demo-seed
    HIVE_HOME="$HOME/.claude/hive-demo" vhs assets/demo.tape
    gifsicle --optimize=3 --lossy=80 --colors=128 assets/cli-demo.gif -o assets/cli-demo.gif
    @ls -lh assets/cli-demo.gif

# Take dashboard screenshots (requires shot-scraper: uv tool install shot-scraper)
demo-screenshots: demo-seed
    #!/usr/bin/env bash
    set -euo pipefail
    export HIVE_HOME="$HOME/.claude/hive-demo"
    # Start serve in background
    {{hivedev}} serve 13847 &
    SERVER_PID=$!
    # Wait for ready
    for i in $(seq 1 20); do
        curl -sf http://localhost:13847/ > /dev/null && break || sleep 0.5
    done
    # Capture screenshots
    shot-scraper http://localhost:13847/ -o assets/dashboard-home.png --width 1200 --height 900
    shot-scraper http://localhost:13847/stats -o assets/dashboard-stats.png --width 1200 --height 900
    shot-scraper http://localhost:13847/know -o assets/dashboard-knowledge.png --width 1200 --height 900
    # Cleanup
    kill $SERVER_PID 2>/dev/null || true
    @echo "Screenshots captured:"
    @ls -lh assets/dashboard-*.png

# Regenerate all demo assets (GIF + screenshots)
demo-assets: demo-gif demo-screenshots
    @echo "All demo assets regenerated from demo profile"
