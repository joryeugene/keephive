# Import private recipes if present (no error when absent)
import? '.just/release.just'

# List available recipes
default:
    @just --list

# ── Dev ──────────────────────────────────────────────────────────────────────

# Run tests (fast, no LLM)
test:
    uv run pytest

# Run LLM integration tests (slow, requires claude CLI)
test-llm:
    uv run pytest -m llm -v -o "addopts="

# Lint with ruff
lint:
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/

# Format in place
fmt:
    uv run ruff format src/ tests/

# Check for accidentally committed secrets / private data
check-private:
    @echo "Scanning for secrets..."
    @! grep -rn "sk-ant-api" src/ tests/ 2>/dev/null || (echo "ERROR: Anthropic key found" && exit 1)
    @! grep -rn "-----BEGIN.*PRIVATE KEY" src/ tests/ 2>/dev/null || (echo "ERROR: Private key found" && exit 1)
    @! grep -rn "ANTHROPIC_API_KEY\s*=" src/ tests/ 2>/dev/null || (echo "ERROR: API key assignment found" && exit 1)
    @echo "OK: no secrets found"

# Run all checks (test + lint + privacy scan)
check: test lint check-private
