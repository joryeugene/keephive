"""Tests for migration behavior in keephive.commands.setup."""

from __future__ import annotations

import os
from pathlib import Path


def _prepare_home(tmp_path: Path, *, create_legacy: bool = True) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    legacy_working = home / ".claude" / "hive" / "working"
    if create_legacy:
        legacy_working.mkdir(parents=True)
        (legacy_working / "memory.md").write_text("# legacy memory\n")
    else:
        legacy_working.parent.mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text("{}")
    (home / ".claude.json").write_text("{}")
    return home


class TestSetupMigration:
    def test_migrates_legacy_hive_when_confirmed(self, tmp_path, monkeypatch):
        home = _prepare_home(tmp_path)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("HIVE_HOME", raising=False)

        import keephive.commands.setup as setup

        monkeypatch.setattr(setup, "_register_mcp", lambda: None)
        monkeypatch.setattr(setup, "find_global_keephive", lambda: False)
        monkeypatch.setattr(setup, "check_installed_deps", lambda: [])

        setup.cmd_setup(["--yes"])

        preferred = home / ".keephive" / "hive"
        legacy = home / ".claude" / "hive"
        assert (preferred / "working" / "memory.md").read_text() == "# legacy memory\n"

        if os.name == "nt":
            assert legacy.exists()
        else:
            assert legacy.is_symlink()
            assert legacy.resolve() == preferred

    def test_skips_migration_when_declined(self, tmp_path, monkeypatch):
        home = _prepare_home(tmp_path)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("HIVE_HOME", raising=False)

        import keephive.commands.setup as setup

        monkeypatch.setattr(setup, "_register_mcp", lambda: None)
        monkeypatch.setattr(setup, "find_global_keephive", lambda: False)
        monkeypatch.setattr(setup, "check_installed_deps", lambda: [])
        monkeypatch.setattr(setup, "prompt_yn", lambda *_args, **_kwargs: False)

        setup.cmd_setup([])

        preferred = home / ".keephive" / "hive"
        legacy = home / ".claude" / "hive"
        assert legacy.exists()
        assert not preferred.exists()
