"""Tests for export/import round-trip."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def transfer_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up hive dir with some data for export tests."""
    hive_dir = tmp_path / "hive"
    hive_dir.mkdir()
    monkeypatch.setenv("HIVE_HOME", str(hive_dir))
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")

    # Create directory structure
    for sub in [
        "working",
        "daily",
        "knowledge/guides",
        "knowledge/prompts",
        "working/notes",
        "archive",
    ]:
        (hive_dir / sub).mkdir(parents=True, exist_ok=True)

    # Add test data
    (hive_dir / "working" / "memory.md").write_text(
        "# Working Memory\n\n- FACT: test export [verified:2026-01-01]\n"
    )
    (hive_dir / "daily" / "2026-01-15.md").write_text(
        "# Daily Log: 2026-01-15\n\n- [10:00:00] FACT: export test entry\n"
    )
    (hive_dir / "knowledge" / "guides" / "test-guide.md").write_text(
        "# Test Guide\n\nSome content.\n"
    )
    (hive_dir / ".stats.json").write_text('{"days": {}}')

    return hive_dir


def test_export_creates_archive(transfer_env, tmp_path, capsys):
    """Export creates a tar.gz file."""
    from keephive.commands.transfer import cmd_export

    out_path = str(tmp_path / "export.tar.gz")
    cmd_export([out_path])

    assert Path(out_path).exists()
    assert Path(out_path).stat().st_size > 0
    out = capsys.readouterr().out
    assert "Exported" in out


def test_export_contains_manifest(transfer_env, tmp_path):
    """Archive contains manifest.json with metadata."""
    from keephive.commands.transfer import cmd_export

    out_path = str(tmp_path / "export.tar.gz")
    cmd_export([out_path])

    with tarfile.open(out_path, "r:gz") as tar:
        mf = tar.extractfile("manifest.json")
        assert mf is not None
        manifest = json.loads(mf.read())
        assert "version" in manifest
        assert "date" in manifest
        assert "profile" in manifest


def test_export_contains_data(transfer_env, tmp_path):
    """Archive contains memory, daily logs, guides, stats."""
    from keephive.commands.transfer import cmd_export

    out_path = str(tmp_path / "export.tar.gz")
    cmd_export([out_path])

    with tarfile.open(out_path, "r:gz") as tar:
        names = tar.getnames()
        assert any("memory.md" in n for n in names)
        assert any("2026-01-15.md" in n for n in names)
        assert any("test-guide.md" in n for n in names)
        assert ".stats.json" in names


def test_roundtrip_export_import(transfer_env, tmp_path, monkeypatch):
    """Export -> import into new location -> data matches."""
    from keephive.commands.transfer import cmd_export, cmd_import

    # Export
    out_path = str(tmp_path / "roundtrip.tar.gz")
    cmd_export([out_path])

    # Import into a new directory
    import_dir = tmp_path / "import-hive"
    import_dir.mkdir()
    for sub in [
        "working",
        "daily",
        "knowledge/guides",
        "knowledge/prompts",
        "working/notes",
        "archive",
    ]:
        (import_dir / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HIVE_HOME", str(import_dir))
    # Auto-confirm
    monkeypatch.setattr("keephive.output.prompt_yn", lambda *a, **kw: True)
    cmd_import([out_path])

    # Verify data
    mem = (import_dir / "working" / "memory.md").read_text()
    assert "test export" in mem

    daily = (import_dir / "daily" / "2026-01-15.md").read_text()
    assert "export test entry" in daily

    guide = (import_dir / "knowledge" / "guides" / "test-guide.md").read_text()
    assert "Test Guide" in guide


def test_import_rejects_path_traversal(transfer_env, tmp_path, monkeypatch):
    """Archives with '..' paths are rejected."""
    # Create a malicious archive
    evil_path = tmp_path / "evil.tar.gz"
    with tarfile.open(str(evil_path), "w:gz") as tar:
        import io

        data = b"malicious content"
        info = tarfile.TarInfo(name="../../../etc/evil.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with pytest.raises(SystemExit):
        from keephive.commands.transfer import cmd_import

        cmd_import([str(evil_path)])


def test_import_with_profile(transfer_env, tmp_path, monkeypatch):
    """Import with --profile creates and imports into new profile."""
    from keephive.commands.transfer import cmd_export, cmd_import

    # Export current data
    out_path = str(tmp_path / "profile-test.tar.gz")
    cmd_export([out_path])

    # Import into new profile
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    keephive_dir = tmp_path / ".keephive"
    monkeypatch.setattr("keephive.storage._claude_dir", lambda: claude_dir)
    monkeypatch.setattr("keephive.storage._keephive_dir", lambda: keephive_dir)
    monkeypatch.delenv("HIVE_HOME", raising=False)
    monkeypatch.setattr("keephive.output.prompt_yn", lambda *a, **kw: True)

    cmd_import([out_path, "--profile", "imported"])

    # Verify profile directory was created with data
    imported_dir = keephive_dir / "hive-imported"
    assert imported_dir.exists()
    assert (imported_dir / "working" / "memory.md").exists()


def test_export_no_data(tmp_path, monkeypatch):
    """Export with empty hive dir shows error."""
    empty_dir = tmp_path / "empty-hive"
    monkeypatch.setenv("HIVE_HOME", str(empty_dir))

    from keephive.commands.transfer import cmd_export

    with pytest.raises(SystemExit):
        cmd_export([str(tmp_path / "empty.tar.gz")])


def test_import_missing_file(tmp_path, monkeypatch):
    """Import with nonexistent file shows error."""
    monkeypatch.setenv("HIVE_HOME", str(tmp_path))

    from keephive.commands.transfer import cmd_import

    with pytest.raises(SystemExit):
        cmd_import(["/nonexistent/path.tar.gz"])
