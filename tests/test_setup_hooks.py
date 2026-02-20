"""Tests for setup command (commands/setup.py)."""

from __future__ import annotations

import json
from pathlib import Path

# ---- _setup_hooks ----


class TestSetupHooks:
    def test_creates_four_hooks(self, tmp_path):
        """Fresh settings.json gets all 4 hooks."""
        settings = tmp_path / "settings.json"
        settings.write_text("{}")

        from keephive.commands.setup import _setup_hooks

        _setup_hooks(settings_path=settings)

        data = json.loads(settings.read_text())
        hooks = data["hooks"]
        assert "SessionStart" in hooks
        assert "PreCompact" in hooks
        assert "PostToolUse" in hooks
        assert "UserPromptSubmit" in hooks

    def test_idempotent(self, tmp_path):
        """Running setup twice doesn't duplicate hooks."""
        settings = tmp_path / "settings.json"
        settings.write_text("{}")

        from keephive.commands.setup import _setup_hooks

        _setup_hooks(settings_path=settings)
        _setup_hooks(settings_path=settings)

        data = json.loads(settings.read_text())
        ss_hooks = data["hooks"]["SessionStart"]
        keephive_count = sum(1 for h in ss_hooks if "keephive" in str(h))
        assert keephive_count == 1

    def test_removes_old_bash_hooks(self, tmp_path):
        """Old bash hive hooks are removed."""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "$HOME/.claude/hive/bin/hive hook-sessionstart",
                                    }
                                ],
                            }
                        ],
                    }
                }
            )
        )

        from keephive.commands.setup import _setup_hooks

        _setup_hooks(settings_path=settings)

        data = json.loads(settings.read_text())
        raw = json.dumps(data)
        assert "bin/hive hook-" not in raw
        assert "keephive hook-sessionstart" in raw

    def test_fixes_flat_format_to_grouped(self, tmp_path):
        """Stray flat-format hooks get wrapped in matcher-grouped format."""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "type": "command",
                                "command": "keephive hook-sessionstart",
                            }
                        ],
                    }
                }
            )
        )

        from keephive.commands.setup import _setup_hooks

        _setup_hooks(settings_path=settings)

        data = json.loads(settings.read_text())
        ss = data["hooks"]["SessionStart"]
        # Should be wrapped in matcher-grouped format
        for entry in ss:
            if "keephive" in str(entry):
                assert "matcher" in entry
                assert "hooks" in entry

    def test_creates_settings_if_missing(self, tmp_path):
        """Creates settings.json if it doesn't exist."""
        settings = tmp_path / "subdir" / "settings.json"

        from keephive.commands.setup import _setup_hooks

        _setup_hooks(settings_path=settings)

        assert settings.exists()
        data = json.loads(settings.read_text())
        assert "hooks" in data

    def test_posttooluse_has_edit_write_matcher(self, tmp_path):
        """PostToolUse hook uses Edit|Write matcher."""
        settings = tmp_path / "settings.json"
        settings.write_text("{}")

        from keephive.commands.setup import _setup_hooks

        _setup_hooks(settings_path=settings)

        data = json.loads(settings.read_text())
        ptu = data["hooks"]["PostToolUse"]
        found = False
        for entry in ptu:
            if entry.get("matcher") == "Edit|Write":
                found = True
        assert found


# ---- _extract_cmds ----


class TestExtractCmds:
    def test_flat_format(self):
        from keephive.commands.setup import _extract_cmds

        result = _extract_cmds({"command": "keephive hook-sessionstart"})
        assert "keephive hook-sessionstart" in result

    def test_grouped_format(self):
        from keephive.commands.setup import _extract_cmds

        result = _extract_cmds(
            {
                "matcher": "*",
                "hooks": [
                    {"type": "command", "command": "keephive hook-sessionstart"},
                ],
            }
        )
        assert "keephive hook-sessionstart" in result

    def test_missing_command_key(self):
        from keephive.commands.setup import _extract_cmds

        result = _extract_cmds({"matcher": "*"})
        assert result == ""


# ---- _seed_bundled_content ----


class TestSeedBundledContent:
    def test_copies_if_not_exists(self, hive_env):
        """Bundled guides are seeded on fresh install."""
        gd = hive_env / "knowledge" / "guides"
        # Remove existing to simulate fresh
        for f in gd.glob("*.md"):
            f.unlink()

        from keephive.commands.setup import _seed_bundled_content

        _seed_bundled_content()

        # Should have at least one guide from bundled data
        guides = list(gd.glob("*.md"))
        assert len(guides) >= 1

    def test_updates_if_content_differs(self, hive_env):
        """Bundled guides are synced on upgrade: stale content is overwritten."""
        gd = hive_env / "knowledge" / "guides"
        # Write an outdated version (simulates stale installed guide)
        (gd / "keephive-guide.md").write_text("# Old version\n")

        from keephive.commands.setup import _seed_bundled_content

        _seed_bundled_content()

        # Should now contain the current bundled content, not the old version
        content = (gd / "keephive-guide.md").read_text()
        assert "Old version" not in content
        assert "keephive" in content.lower()  # bundled guide has keephive content


# ---- _sync_global_install ----


class TestSetupGlobalSync:
    def test_reinstalls_when_deps_stale(self, hive_env, capsys):
        """Setup auto-reinstalls when global install has missing deps."""
        from unittest.mock import MagicMock, patch

        mock_run = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch(
                "keephive.commands.setup.find_global_keephive", return_value=Path("/fake/keephive")
            ),
            patch("keephive.commands.setup.check_installed_deps", return_value=["anthropic"]),
            patch("subprocess.run", return_value=mock_run) as mock_sub,
        ):
            from keephive.commands.setup import _sync_global_install

            _sync_global_install()

        # Must have called uv tool install --force
        mock_sub.assert_called_once()
        cmd = mock_sub.call_args[0][0]
        assert "uv" in cmd
        assert "tool" in cmd
        assert "install" in cmd
        assert "--force" in cmd

        out = capsys.readouterr().out
        assert "updated" in out.lower()

    def test_skips_when_no_global_install(self, hive_env, capsys):
        """No global install means no sync attempt."""
        from unittest.mock import patch

        with patch("keephive.commands.setup.find_global_keephive", return_value=None):
            from keephive.commands.setup import _sync_global_install

            _sync_global_install()

        out = capsys.readouterr().out
        assert "No global install" in out

    def test_skips_when_deps_ok(self, hive_env, capsys):
        """No missing deps means no reinstall."""
        from unittest.mock import patch

        with (
            patch(
                "keephive.commands.setup.find_global_keephive", return_value=Path("/fake/keephive")
            ),
            patch("keephive.commands.setup.check_installed_deps", return_value=[]),
        ):
            from keephive.commands.setup import _sync_global_install

            _sync_global_install()

        out = capsys.readouterr().out
        assert "up to date" in out.lower()


# ---- Health checks (health.py) ----


class TestHealthChecks:
    def test_check_hooks_present(self, tmp_path, monkeypatch):
        """Hooks in settings.json are detected."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {"type": "command", "command": "keephive hook-sessionstart"}
                                ],
                            }
                        ],
                        "PreCompact": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {"type": "command", "command": "keephive hook-precompact"}
                                ],
                            }
                        ],
                        "PostToolUse": [
                            {
                                "matcher": "Edit|Write",
                                "hooks": [
                                    {"type": "command", "command": "keephive hook-posttooluse"}
                                ],
                            }
                        ],
                        "UserPromptSubmit": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {"type": "command", "command": "keephive hook-userpromptsubmit"}
                                ],
                            }
                        ],
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from keephive.health import check_hooks

        assert check_hooks() is True

    def test_check_hooks_missing(self, tmp_path, monkeypatch):
        """Missing settings.json returns False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from keephive.health import check_hooks

        assert check_hooks() is False

    def test_check_hooks_partial(self, tmp_path, monkeypatch):
        """3 of 4 required hooks returns False."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {"type": "command", "command": "keephive hook-sessionstart"}
                                ],
                            }
                        ],
                        "PreCompact": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {"type": "command", "command": "keephive hook-precompact"}
                                ],
                            }
                        ],
                        "PostToolUse": [
                            {
                                "matcher": "Edit|Write",
                                "hooks": [
                                    {"type": "command", "command": "keephive hook-posttooluse"}
                                ],
                            }
                        ],
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from keephive.health import check_hooks

        assert check_hooks() is False

    def test_check_mcp_present(self, tmp_path, monkeypatch):
        """MCP server in .claude.json is detected."""
        (tmp_path / ".claude.json").write_text(
            json.dumps({"mcpServers": {"hive": {"command": "keephive-mcp"}}})
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from keephive.health import check_mcp

        assert check_mcp() is True

    def test_check_mcp_missing(self, tmp_path, monkeypatch):
        """No .claude.json returns False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from keephive.health import check_mcp

        assert check_mcp() is False

    def test_check_mcp_no_hive_key(self, tmp_path, monkeypatch):
        """MCP servers exist but no 'hive' key returns False."""
        (tmp_path / ".claude.json").write_text(
            json.dumps({"mcpServers": {"other": {"command": "other-server"}}})
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from keephive.health import check_mcp

        assert check_mcp() is False

    def test_check_data_present(self, hive_env):
        """Data check passes with hive_env fixture."""
        from keephive.health import check_data

        assert check_data() is True

    def test_check_data_missing_memory(self, hive_env):
        """Missing memory.md fails data check."""
        (hive_env / "working" / "memory.md").unlink()

        from keephive.health import check_data

        assert check_data() is False

    def test_health_summary(self, hive_env, tmp_path, monkeypatch):
        """health_summary returns a 3-tuple of bools."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        from keephive.health import health_summary

        hooks_ok, mcp_ok, data_ok = health_summary()
        # No hooks or MCP configured in tmp_path
        assert hooks_ok is False
        assert mcp_ok is False
        # Data is present via hive_env
        assert data_ok is True
