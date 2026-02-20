"""Tests for hive update command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestUpdateUpToDate:
    def test_up_to_date(self, hive_env, capsys):
        """When already on latest, prints 'Up to date' and returns 0."""
        from keephive import __version__
        from keephive.commands.update import cmd_update

        mock_resp = MagicMock()
        mock_resp.read.return_value = f'{{"info": {{"version": "{__version__}"}}}}'.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            rc = cmd_update([])

        out = capsys.readouterr().out
        assert rc == 0
        assert "Up to date" in out
        assert __version__ in out


class TestUpdateAvailable:
    def test_prints_versions(self, hive_env, capsys):
        """When behind, prints current and latest versions."""
        from keephive import __version__
        from keephive.commands.update import cmd_update

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"info": {"version": "99.0.0"}}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("keephive.commands.update.prompt_yn", return_value=False):
            rc = cmd_update([])

        out = capsys.readouterr().out
        assert rc == 0
        assert __version__ in out
        assert "99.0.0" in out

    def test_decline_prints_manual_command(self, hive_env, capsys):
        """When user declines, prints the manual upgrade command."""
        from keephive.commands.update import cmd_update

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"info": {"version": "99.0.0"}}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("keephive.commands.update.prompt_yn", return_value=False):
            rc = cmd_update([])

        out = capsys.readouterr().out
        assert rc == 0
        assert "uv tool upgrade keephive" in out

    def test_confirm_runs_upgrade_and_setup(self, hive_env, capsys):
        """When user confirms, runs uv tool upgrade and keephive setup."""
        from keephive.commands.update import cmd_update

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"info": {"version": "99.0.0"}}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        upgrade_result = MagicMock(returncode=0)
        setup_result = MagicMock(returncode=0)

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("keephive.commands.update.prompt_yn", return_value=True), \
             patch("subprocess.run", side_effect=[upgrade_result, setup_result]) as mock_run:
            rc = cmd_update([])

        out = capsys.readouterr().out
        assert rc == 0
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0] == ["uv", "tool", "upgrade", "keephive"]
        assert mock_run.call_args_list[1][0][0] == ["keephive", "setup"]
        assert "99.0.0" in out

    def test_upgrade_failure_returns_nonzero(self, hive_env, capsys):
        """When uv tool upgrade fails, returns non-zero and prints error."""
        from keephive.commands.update import cmd_update

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"info": {"version": "99.0.0"}}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("keephive.commands.update.prompt_yn", return_value=True), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            rc = cmd_update([])

        out = capsys.readouterr().out
        assert rc == 1
        assert "failed" in out.lower()

    def test_setup_failure_warns(self, hive_env, capsys):
        """When keephive setup fails after successful upgrade, warns but setup is optional."""
        from keephive.commands.update import cmd_update

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"info": {"version": "99.0.0"}}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        upgrade_result = MagicMock(returncode=0)
        setup_result = MagicMock(returncode=1)

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("keephive.commands.update.prompt_yn", return_value=True), \
             patch("subprocess.run", side_effect=[upgrade_result, setup_result]):
            rc = cmd_update([])

        out = capsys.readouterr().out
        assert rc == 1
        assert "setup" in out.lower()


class TestUpdateNetworkError:
    def test_network_failure_returns_nonzero(self, hive_env, capsys):
        """Network error prints message and returns 1."""
        from keephive.commands.update import cmd_update

        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            rc = cmd_update([])

        out = capsys.readouterr().out
        assert rc == 1
        assert "PyPI" in out
