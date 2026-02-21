# Import private recipes if present (no error when absent)
import? '.just/release.just'

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

# Run a single test file or pattern (e.g. just test-one tests/test_smoke.py)
test-one target:
    uv run pytest {{target}} -xvs

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
    uv run python -m keephive serve --hot
