"""Tests for demo data seeder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def seed_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Clean hive dir for seeding."""
    hive_dir = tmp_path / "hive"
    hive_dir.mkdir()
    monkeypatch.setenv("HIVE_HOME", str(hive_dir))
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")
    return hive_dir


def test_seed_creates_daily_logs(seed_env):
    """Seeder creates daily log files."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "10"])
    daily = seed_env / "daily"
    assert daily.exists()
    logs = list(daily.glob("*.md"))
    assert len(logs) >= 5  # Some days may be skipped (weekends with 0 entries)


def test_seed_creates_memory(seed_env):
    """Seeder writes memory.md with verified facts."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "10"])
    mem = seed_env / "working" / "memory.md"
    assert mem.exists()
    content = mem.read_text()
    assert "[verified:" in content
    assert "FACT:" in content


def test_seed_creates_stats(seed_env):
    """Seeder writes .stats.json that read_stats() can parse."""
    from keephive.commands.seed import cmd_seed
    from keephive.storage import read_stats

    cmd_seed(["--force", "--days", "30"])
    data = read_stats()
    assert "days" in data
    assert len(data["days"]) >= 15  # Most of 30 days should have data


def test_seed_creates_guides(seed_env):
    """Seeder writes knowledge guides."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "5"])
    guides = seed_env / "knowledge" / "guides"
    assert guides.exists()
    guide_files = list(guides.glob("*.md"))
    assert len(guide_files) >= 2


def test_seed_creates_recurring(seed_env):
    """Seeder writes recurring.md."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "5"])
    rf = seed_env / "working" / "recurring.md"
    assert rf.exists()
    content = rf.read_text()
    assert "[weekly]" in content or "[daily]" in content


def test_seed_creates_notes(seed_env):
    """Seeder writes note slot files."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "5"])
    assert (seed_env / "working" / "note-1.md").exists()
    assert (seed_env / "working" / "note-3.md").exists()


def test_seed_creates_evidence(seed_env):
    """Seeder writes evidence.json."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "5"])
    ef = seed_env / "working" / "evidence.json"
    assert ef.exists()
    data = json.loads(ef.read_text())
    assert len(data) >= 5


def test_seed_deterministic(seed_env):
    """Running seed twice produces identical output."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "15"])

    # Read all generated files
    files_first: dict[str, str] = {}
    for f in sorted(seed_env.rglob("*")):
        if f.is_file():
            key = str(f.relative_to(seed_env))
            files_first[key] = f.read_text()

    # Seed again
    cmd_seed(["--force", "--days", "15"])

    # Compare
    for f in sorted(seed_env.rglob("*")):
        if f.is_file():
            key = str(f.relative_to(seed_env))
            assert f.read_text() == files_first.get(key, ""), f"File {key} differs on re-seed"


def test_seed_status_no_crash(seed_env, capsys):
    """Status command runs without error on seeded data."""
    from keephive.commands.seed import cmd_seed
    from keephive.commands.status import cmd_status

    cmd_seed(["--force", "--days", "30"])
    cmd_status([])
    out = capsys.readouterr().out
    assert "keephive" in out


def test_seed_stats_no_crash(seed_env, capsys):
    """Stats command runs without error on seeded data."""
    from keephive.commands.seed import cmd_seed
    from keephive.commands.stats import cmd_stats

    cmd_seed(["--force", "--days", "30"])
    cmd_stats([])
    out = capsys.readouterr().out
    assert len(out) > 0


def test_load_entries():
    """entries.json loads correctly from package data."""
    from keephive.commands.seed import _load_entries

    entries = _load_entries()
    assert "facts" in entries
    assert "decisions" in entries
    assert "memory_facts" in entries
    assert len(entries["facts"]) >= 15
    assert len(entries["memory_facts"]) >= 10


# ---- Profile safety guardrails ----


def test_seed_shows_profile_target(seed_env, capsys):
    """Seed prints active profile target before writing data."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "5"])
    # Strip Rich line-wrapping newlines for path matching
    out = capsys.readouterr().out.replace("\n", " ")
    # HIVE_HOME is set by seed_env, so label shows that
    assert "Target:" in out
    assert "HIVE_HOME=" in out


def test_seed_force_shows_target(seed_env, capsys):
    """Even with --force, the profile target line is printed."""
    from keephive.commands.seed import cmd_seed

    # First seed to create data
    cmd_seed(["--force", "--days", "5"])
    capsys.readouterr()  # clear

    # Second seed with --force should still show target
    cmd_seed(["--force", "--days", "5"])
    out = capsys.readouterr().out.replace("\n", " ")
    assert "Target:" in out
    assert "HIVE_HOME=" in out


def test_seed_default_profile_label(seed_env, capsys):
    """Seed on default profile shows 'HIVE_HOME' label (since tests use HIVE_HOME)."""
    from keephive.commands.seed import cmd_seed

    cmd_seed(["--force", "--days", "5"])
    out = capsys.readouterr().out
    assert "HIVE_HOME=" in out
