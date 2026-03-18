"""Shared fixtures for keephive tests."""

from __future__ import annotations

import difflib
import json
import os
import re
from datetime import date, datetime, timedelta
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
    monkeypatch.setenv("HIVE_NO_TMUX_SPAWN", "1")  # Never spawn real Claude processes in unit tests
    monkeypatch.delenv("HIVE_SESSION_LAUNCHED", raising=False)
    # Isolate CC session-meta reads from host system data
    cc_meta = tmp_path / "cc-session-meta"
    cc_meta.mkdir()
    monkeypatch.setenv("HIVE_CC_META_DIR", str(cc_meta))
    # Isolate CC projects dir (JSONL conversation files) for live session detection
    cc_projects = tmp_path / "cc-projects"
    cc_projects.mkdir()
    monkeypatch.setenv("HIVE_CC_PROJECTS_DIR", str(cc_projects))

    # Create directories
    (hive_dir / "working").mkdir()
    (hive_dir / "daily").mkdir()
    (hive_dir / "knowledge" / "guides").mkdir(parents=True)
    (hive_dir / "knowledge" / "prompts").mkdir(parents=True)
    (hive_dir / "working" / "notes").mkdir()
    (hive_dir / "archive").mkdir()

    # Create test data
    _fresh1 = (date.today() - timedelta(days=7)).isoformat()
    _fresh2 = (date.today() - timedelta(days=10)).isoformat()
    (hive_dir / "working" / "memory.md").write_text(
        "# Working Memory\n\n"
        "- Python is great [verified:2020-01-01]\n"
        f"- keephive uses Pydantic [verified:{_fresh1}]\n"
        f"- Tests are important [verified:{_fresh2}]\n"
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
    monkeypatch.setenv("HIVE_NO_TMUX_SPAWN", "1")  # Never spawn real Claude processes in tests
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


def _normalize_golden(text: str) -> str:
    """Normalize non-deterministic output for golden file comparison.

    Replaces:
    - Wall-clock timestamps: HIVE_DATE overrides date but not time, so
      [HH:MM:SS] changes every run.
    - Pytest temp paths: /tmp/.../pytest-NNN/... changes every session.
    - Version strings: change on each release.
    - Stats/Status volatile values: read from real ~/.claude/usage-data/
      which is not isolated by HIVE_HOME.
    - Sparkline bars: vary with underlying data.
    """
    # Timestamps
    text = re.sub(r"\d{2}:\d{2}:\d{2}", "HH:MM:SS", text)
    text = re.sub(r"(?<!\d)\d{2}:\d{2}(?!:\d)", "HH:MM", text)
    # Pytest temp paths
    text = re.sub(r"pytest-\d+", "pytest-NNN", text)
    # Version strings
    text = re.sub(r"v\d+\.\d+\.\d+", "vX.Y.Z", text)
    # Sparkline/bar chart characters (volatile with underlying data)
    text = re.sub(r"[▁▂▃▄▅▆▇█]+", "▓", text)
    # --- Stats/Status: volatile counters from real ~/.claude/usage-data/ ---
    text = re.sub(r"\d+ prompts/convo", "N prompts/convo", text)
    text = re.sub(r"median \d+", "median N", text)
    text = re.sub(r"\d+ cmds today", "N cmds today", text)
    text = re.sub(r"\d+ this week", "N this week", text)
    text = re.sub(r"\d+d streak", "Nd streak", text)
    text = re.sub(r"best: \d+d", "best: Nd", text)
    text = re.sub(r"\d+ prompts today", "N prompts today", text)
    # Session quality from real /insights facets
    text = re.sub(r"\d+% achieved", "N% achieved", text)
    text = re.sub(r"\(\d+ sessions\)", "(N sessions)", text)
    # Session size distribution
    text = re.sub(r"\d+ (small|medium|large|huge)", r"N \1", text)
    text = re.sub(r"compaction: \d+%", "compaction: N%", text)
    # Trends values (entirely from real data)
    text = re.sub(
        r"(Prompts|Convos|P/convo|Insights|TODOs done|Verified)"
        r"\s+\d+\s+\d+\s+[-+]?\d+%",
        r"\1  N  N  N%",
        text,
    )
    text = re.sub(
        r"(Prompts|Convos|P/convo|Insights|TODOs done|Verified)"
        r"\s+\d+\s+\d+\s+=",
        r"\1  N  N  =",
        text,
    )
    # Activity day rows: volatile counts and dates
    text = re.sub(
        r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+[▓─]+\s+\d+",
        r"\1  ▓  N",
        text,
    )
    # Source percentages and hook counts
    text = re.sub(r"(claude code|terminal|web)\s+\d+%", r"\1  N%", text)
    text = re.sub(
        r"(precompact|userpromptsubmit|posttooluse|sessionstart"
        r"|stop|sessionend|taskcompleted|subagent_stop)\s+\d+",
        r"\1  N",
        text,
    )
    # Tool usage percentages
    text = re.sub(r"(Read|Edit|Write|Bash|Glob|Grep) \d+%", r"\1 N%", text)
    text = re.sub(r"[▼▲]\d+%", "△N%", text)
    # Project lines: real projects appear alongside seeded ones
    text = re.sub(r"\d+ cmds ·", "N cmds ·", text)
    text = re.sub(r"\d+ sessions ·", "N sessions ·", text)
    text = re.sub(r"\d+d active", "Nd active", text)
    text = re.sub(r"last: \d+d ago", "last: Nd ago", text)
    text = re.sub(r"last: yesterday", "last: recently", text)
    # Session counts in header
    text = re.sub(r"today \d+  ·  week \d+", "today N  ·  week N", text)
    return text


def assert_golden(screen: object, golden_name: str, update: bool = False) -> None:
    """Compare screen output against golden file baseline.

    Run with --update-golden to regenerate baselines:
        uv run pytest -m terminal --update-golden -o "addopts="

    Non-deterministic values (timestamps, temp paths) are normalized before
    comparison so golden files don't break on every run.
    """
    golden_path = GOLDEN_DIR / f"{golden_name}.txt"
    plain = screen.plain  # type: ignore[attr-defined]

    if update or not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(plain)
        return

    expected = _normalize_golden(golden_path.read_text())
    actual = _normalize_golden(plain)
    if actual.strip() != expected.strip():
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile=f"golden/{golden_name}.txt",
                tofile="actual",
                lineterm="",
            )
        )
        raise AssertionError(f"Output differs from golden file:\n{diff}")
