from __future__ import annotations

from pathlib import Path


def _patch_home_dirs(storage, monkeypatch, tmp_path: Path) -> None:
    keephive_root = tmp_path / ".keephive"
    claude_root = tmp_path / ".claude"
    keephive_root.mkdir()
    claude_root.mkdir()
    monkeypatch.setattr(storage, "_keephive_dir", lambda: keephive_root)
    monkeypatch.setattr(storage, "_claude_dir", lambda: claude_root)


def test_profile_exists_detects_preferred(tmp_path, monkeypatch):
    import importlib

    storage = importlib.import_module("keephive.storage")
    _patch_home_dirs(storage, monkeypatch, tmp_path)

    assert storage.profile_exists("test") is False

    preferred = storage._keephive_dir() / "hive-test"
    preferred.mkdir()
    assert storage.profile_exists("test") is True


def test_profile_exists_detects_legacy(tmp_path, monkeypatch):
    import importlib

    storage = importlib.import_module("keephive.storage")
    _patch_home_dirs(storage, monkeypatch, tmp_path)

    legacy = storage._claude_dir() / "hive-test"
    legacy.mkdir()
    assert storage.profile_exists("test") is True
