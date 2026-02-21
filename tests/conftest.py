"""Shared fixtures for keephive tests."""

from __future__ import annotations

import difflib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Set NO_COLOR before keephive.output is imported (module-level console init).
# This keeps force_terminal behavior in production while giving plain text in tests.
os.environ.setdefault("NO_COLOR", "1")

E2E_OUTPUT_DIR = Path(__file__).parent / "e2e_outputs"
GOLDEN_DIR = E2E_OUTPUT_DIR / "golden"
TERMINAL_OUTPUT_DIR = E2E_OUTPUT_DIR / "terminal"


def make_daily(hive_env: Path, days_ago: int = 0, entries: list[str] | None = None) -> Path:
    """Create a daily log file with entries. Shared helper for tests."""
    from keephive.clock import get_today

    d = get_today() - timedelta(days=days_ago)
    daily = hive_env / "daily" / f"{d.isoformat()}.md"
    lines = [f"# Daily Log: {d.isoformat()}\n"]
    for e in entries or []:
        lines.append(e)
    daily.write_text("\n".join(lines) + "\n")
    return daily


@pytest.fixture
def hive_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a temporary hive directory for testing.

    Sets HIVE_HOME to a temp dir, creates the directory structure,
    and provides test data.
    """
    hive_dir = tmp_path / "hive"
    hive_dir.mkdir()

    # Set environment
    monkeypatch.setenv("HIVE_HOME", str(hive_dir))
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")

    # Create directories
    (hive_dir / "working").mkdir()
    (hive_dir / "daily").mkdir()
    (hive_dir / "knowledge" / "guides").mkdir(parents=True)
    (hive_dir / "knowledge" / "prompts").mkdir(parents=True)
    (hive_dir / "working" / "notes").mkdir()
    (hive_dir / "archive").mkdir()

    # Create test data
    (hive_dir / "working" / "memory.md").write_text(
        "# Working Memory\n\n"
        "- Python is great [verified:2020-01-01]\n"
        "- keephive uses Pydantic [verified:2026-02-15]\n"
        "- Tests are important [verified:2026-02-14]\n"
    )

    (hive_dir / "working" / "rules.md").write_text(
        '# Working Rules\n\n## When You Learn Something New\n-> hive r "FACT: what you learned"\n'
    )

    return hive_dir


@pytest.fixture
def daily_with_entries(hive_env: Path):
    """Add a daily log with entries to the test environment."""
    from keephive.clock import get_today

    today = get_today().isoformat()
    daily = hive_env / "daily" / f"{today}.md"
    daily.write_text(
        f"# Daily Log: {today}\n\n"
        "- [10:00:00] FACT: Python 3.12 supports type param syntax\n"
        "- [10:05:00] TODO: Add more tests\n"
        "- [10:10:00] DECISION: Use Pydantic for validation\n"
        "- [10:15:00] session [keephive] /home/dev/keephive\n"
        "- [10:20:00] DONE: Add more tests\n"
    )
    return daily


@pytest.fixture
def llm_hive_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Hive environment for LLM tests. No HIVE_SKIP_LLM."""
    hive_dir = tmp_path / "hive"
    hive_dir.mkdir()

    monkeypatch.setenv("HIVE_HOME", str(hive_dir))
    monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)
    # Strip CLAUDECODE so tool-using commands (verify, reflect) don't refuse
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)

    # Auto-approve Y/n prompts so tests don't block on TTY input.
    # Must patch at each import site since modules use `from keephive.output import prompt_yn`.
    _auto_yes = lambda *a, **kw: True  # noqa: E731
    monkeypatch.setattr("keephive.output.prompt_yn", _auto_yes)
    for mod in [
        "keephive.commands.verify",
        "keephive.commands.reflect",
        "keephive.commands.doctor",
        "keephive.commands.audit",
        "keephive.commands.standup",
        "keephive.commands.remember",
        "keephive.commands.knowledge",
        "keephive.commands.note",
    ]:
        try:
            monkeypatch.setattr(f"{mod}.prompt_yn", _auto_yes)
        except AttributeError:
            pass  # Module not yet imported

    (hive_dir / "working").mkdir()
    (hive_dir / "daily").mkdir()
    (hive_dir / "knowledge" / "guides").mkdir(parents=True)
    (hive_dir / "knowledge" / "prompts").mkdir(parents=True)
    (hive_dir / "working" / "notes").mkdir()
    (hive_dir / "archive").mkdir()

    return hive_dir


@pytest.fixture
def save_e2e_output():
    """Returns a callable that saves LLM output for later analysis."""

    def _save(command: str, output: str, metadata: dict | None = None):
        cmd_dir = E2E_OUTPUT_DIR / command
        cmd_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = {
            "timestamp": ts,
            "command": command,
            "output": output,
            "metadata": metadata or {},
        }
        (cmd_dir / f"{ts}.json").write_text(json.dumps(record, indent=2))

    return _save


# ---- Terminal E2E fixtures ----


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Update golden file baselines for terminal tests",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture
def term(tmp_path: Path):
    """Fresh terminal session with empty hive directory."""
    from terminal import Terminal

    t = Terminal(tmp_path)
    yield t
    t.close()


@pytest.fixture
def term_seeded(tmp_path: Path):
    """Terminal session with 45 days of seeded demo data."""
    from terminal import Terminal

    t = Terminal(tmp_path)
    t.seed(45)
    yield t
    t.close()


@pytest.fixture
def save_terminal_output():
    """Save terminal test history as JSON artifact for output tracking."""

    def _save(scenario: str, terminal: object, metadata: dict | None = None) -> None:
        out_dir = TERMINAL_OUTPUT_DIR / scenario
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = {
            "timestamp": ts,
            "scenario": scenario,
            "history": terminal._history,  # type: ignore[attr-defined]
            "metadata": metadata or {},
        }
        (out_dir / f"{ts}.json").write_text(json.dumps(record, indent=2, default=str))

    return _save


def assert_golden(screen: object, golden_name: str, update: bool = False) -> None:
    """Compare screen output against golden file baseline.

    Run with --update-golden to regenerate baselines:
        uv run pytest -m terminal --update-golden -o "addopts="
    """
    golden_path = GOLDEN_DIR / f"{golden_name}.txt"
    plain = screen.plain  # type: ignore[attr-defined]

    if update or not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(plain)
        return

    expected = golden_path.read_text()
    if plain.strip() != expected.strip():
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                plain.splitlines(),
                fromfile=f"golden/{golden_name}.txt",
                tofile="actual",
                lineterm="",
            )
        )
        raise AssertionError(f"Output differs from golden file:\n{diff}")
