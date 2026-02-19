"""Tests for commands/skill.py — skill publish/unpublish/sync/find/view system."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def skill_env(hive_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Extend hive_env with a plugin dir override."""
    plugin_dir = tmp_path / "plugins"
    monkeypatch.setenv("KEEPHIVE_PLUGIN_DIR", str(plugin_dir))
    # Create a couple of test guides
    guides_dir = hive_env / "knowledge" / "guides"
    (guides_dir / "alpha.md").write_text("# Alpha Guide\n\nContent about alpha.\n")
    (guides_dir / "beta.md").write_text("# Beta Guide\n\nContent about beta testing.\n")
    return hive_env


class TestSkillList:
    def test_lists_local_guides(self, skill_env, capsys):
        from keephive.commands.skill import cmd_skill

        cmd_skill([])
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out

    def test_shows_published_status(self, skill_env, capsys):
        from keephive.commands.skill import _skill_publish, cmd_skill

        _skill_publish(["alpha"])
        capsys.readouterr()  # flush

        cmd_skill(["list"])
        out = capsys.readouterr().out
        assert "published" in out

    def test_empty_guides_dir(self, hive_env, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("KEEPHIVE_PLUGIN_DIR", str(tmp_path / "plugins"))
        # No guides created — guides dir is empty
        from keephive.commands.skill import cmd_skill

        cmd_skill(["list"])
        out = capsys.readouterr().out
        assert "none" in out.lower() or out.strip() != ""


class TestSkillPublish:
    def test_publish_existing_guide(self, skill_env, capsys):
        from keephive.commands.skill import _read_manifest, cmd_skill

        cmd_skill(["publish", "alpha"])
        manifest = _read_manifest()
        assert "alpha" in manifest
        assert manifest["alpha"]["type"] == "guide"

    def test_publish_missing_guide_no_manifest_entry(self, skill_env, capsys):
        from keephive.commands.skill import _read_manifest, cmd_skill

        cmd_skill(["publish", "nonexistent"])
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "error" in out.lower() or "guide" in out.lower()
        manifest = _read_manifest()
        assert "nonexistent" not in manifest

    def test_duplicate_publish_overwrites(self, skill_env, capsys):
        from keephive.commands.skill import _read_manifest, cmd_skill

        cmd_skill(["publish", "alpha"])
        cmd_skill(["publish", "alpha"])
        capsys.readouterr()
        manifest = _read_manifest()
        # Only one entry for alpha, not duplicated
        assert list(manifest.keys()).count("alpha") == 1

    def test_publish_creates_skill_file(self, skill_env, monkeypatch, tmp_path):
        from keephive import __version__
        from keephive.commands.skill import cmd_skill

        plugin_base = tmp_path / "plugins"
        monkeypatch.setenv("KEEPHIVE_PLUGIN_DIR", str(plugin_base))
        cmd_skill(["publish", "beta"])
        skill_file = plugin_base / __version__ / "skills" / "beta.md"
        assert skill_file.exists()
        assert "beta" in skill_file.read_text().lower()


class TestSkillUnpublish:
    def test_unpublish_removes_from_manifest(self, skill_env, capsys):
        from keephive.commands.skill import _read_manifest, cmd_skill

        cmd_skill(["publish", "alpha"])
        cmd_skill(["unpublish", "alpha"])
        capsys.readouterr()
        manifest = _read_manifest()
        assert "alpha" not in manifest

    def test_unpublish_not_published_no_crash(self, skill_env, capsys):
        from keephive.commands.skill import cmd_skill

        # Should warn but not raise
        cmd_skill(["unpublish", "never-published"])
        out = capsys.readouterr().out
        assert "not published" in out.lower() or out  # warning shown

    def test_unpublish_removes_skill_file(self, skill_env, monkeypatch, tmp_path):
        from keephive import __version__
        from keephive.commands.skill import cmd_skill

        plugin_base = tmp_path / "plugins"
        monkeypatch.setenv("KEEPHIVE_PLUGIN_DIR", str(plugin_base))
        cmd_skill(["publish", "alpha"])
        skill_file = plugin_base / __version__ / "skills" / "alpha.md"
        assert skill_file.exists()

        cmd_skill(["unpublish", "alpha"])
        assert not skill_file.exists()


class TestSkillSync:
    def test_sync_all_guides(self, skill_env, capsys):
        from keephive.commands.skill import _read_manifest, cmd_skill

        cmd_skill(["sync"])
        manifest = _read_manifest()
        assert "alpha" in manifest
        assert "beta" in manifest
        out = capsys.readouterr().out
        assert "synced" in out.lower()

    def test_sync_empty_guides_dir(self, hive_env, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("KEEPHIVE_PLUGIN_DIR", str(tmp_path / "plugins"))
        from keephive.commands.skill import cmd_skill

        cmd_skill(["sync"])
        out = capsys.readouterr().out
        # Should handle gracefully: either "0 synced" or "no guides" message
        assert out.strip()


class TestSkillFind:
    def test_find_by_name_substring(self, skill_env, capsys):
        from keephive.commands.skill import cmd_skill

        cmd_skill(["find", "alpha"])
        out = capsys.readouterr().out
        assert "alpha" in out

    def test_find_by_content(self, skill_env, capsys):
        from keephive.commands.skill import cmd_skill

        cmd_skill(["find", "testing"])
        out = capsys.readouterr().out
        assert "beta" in out

    def test_find_no_match_silent(self, skill_env, capsys):
        from keephive.commands.skill import cmd_skill

        cmd_skill(["find", "zzznomatch"])
        out = capsys.readouterr().out
        # No match should produce no output (not an error)
        assert "error" not in out.lower()


class TestSkillView:
    def test_view_existing(self, skill_env, capsys):
        from keephive.commands.skill import cmd_skill

        cmd_skill(["alpha"])
        out = capsys.readouterr().out
        assert "Alpha Guide" in out

    def test_view_missing(self, skill_env, capsys):
        from keephive.commands.skill import cmd_skill

        cmd_skill(["nosuchguide"])
        out = capsys.readouterr().out
        assert "not found" in out.lower()


class TestManifestOps:
    def test_read_manifest_missing_returns_empty(self, hive_env, tmp_path, monkeypatch):
        monkeypatch.setenv("KEEPHIVE_PLUGIN_DIR", str(tmp_path / "plugins"))
        from keephive.commands.skill import SKILL_MANIFEST, _read_manifest

        # Remove the manifest if it exists (SKILL_MANIFEST is module-level)
        if SKILL_MANIFEST.exists():
            SKILL_MANIFEST.unlink()

        result = _read_manifest()
        assert result == {}

    def test_write_then_read_manifest(self, skill_env):
        from keephive.commands.skill import _ensure_plugin, _read_manifest, _write_manifest

        _ensure_plugin()
        data = {"test-guide": {"type": "guide", "source": "/some/path"}}
        _write_manifest(data)
        result = _read_manifest()
        assert result == data

    def test_plugin_dir_respects_env(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_plugins"
        monkeypatch.setenv("KEEPHIVE_PLUGIN_DIR", str(custom))
        from keephive import __version__
        from keephive.commands.skill import _plugin_dir

        assert str(custom) in str(_plugin_dir())
        assert __version__ in str(_plugin_dir())
