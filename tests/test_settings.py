"""Tests for the settings system (settings.py + commands/settings.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---- Core settings module ----


class TestReadSettings:
    def test_defaults_when_no_file(self, hive_env: Path):
        from keephive.settings import DEFAULTS, read_settings

        result = read_settings()
        assert result == DEFAULTS
        assert result["sound"] is False
        assert result["sound_success"] == "Glass"
        assert result["sound_error"] == "Basso"

    def test_reads_stored_values(self, hive_env: Path):
        from keephive.settings import read_settings, settings_file

        sf = settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({"sound": True}))

        result = read_settings()
        assert result["sound"] is True

    def test_ignores_unknown_keys(self, hive_env: Path):
        from keephive.settings import DEFAULTS, read_settings, settings_file

        sf = settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({"sound": True, "bogus": 42}))

        result = read_settings()
        assert "bogus" not in result
        assert result["sound"] is True
        assert set(result.keys()) == set(DEFAULTS.keys())

    def test_corrupt_file_returns_defaults(self, hive_env: Path):
        from keephive.settings import DEFAULTS, read_settings, settings_file

        sf = settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text("not json{{{")

        result = read_settings()
        assert result == DEFAULTS

    def test_non_dict_file_returns_defaults(self, hive_env: Path):
        from keephive.settings import DEFAULTS, read_settings, settings_file

        sf = settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps([1, 2, 3]))

        result = read_settings()
        assert result == DEFAULTS


class TestGetSetting:
    def test_returns_default(self, hive_env: Path):
        from keephive.settings import get_setting

        assert get_setting("sound") is False

    def test_returns_stored(self, hive_env: Path):
        from keephive.settings import get_setting, set_setting

        set_setting("sound", True)
        assert get_setting("sound") is True

    def test_unknown_key_returns_none(self, hive_env: Path):
        from keephive.settings import get_setting

        assert get_setting("nonexistent") is None


class TestSetSetting:
    def test_creates_file(self, hive_env: Path):
        from keephive.settings import set_setting, settings_file

        sf = settings_file()
        assert not sf.exists()

        set_setting("sound", True)
        assert sf.exists()
        stored = json.loads(sf.read_text())
        assert stored["sound"] is True

    def test_preserves_other_keys(self, hive_env: Path):
        from keephive.settings import set_setting, settings_file

        sf = settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({"sound": False}))

        set_setting("sound", True)
        stored = json.loads(sf.read_text())
        assert stored["sound"] is True

    def test_unknown_key_raises(self, hive_env: Path):
        from keephive.settings import set_setting

        with pytest.raises(KeyError, match="bogus"):
            set_setting("bogus", "whatever")

    def test_roundtrip(self, hive_env: Path):
        from keephive.settings import get_setting, set_setting

        set_setting("sound", True)
        assert get_setting("sound") is True
        set_setting("sound", False)
        assert get_setting("sound") is False

    def test_sound_success_roundtrip(self, hive_env: Path):
        from keephive.settings import get_setting, set_setting

        assert get_setting("sound_success") == "Glass"
        set_setting("sound_success", "Pop")
        assert get_setting("sound_success") == "Pop"

    def test_sound_error_roundtrip(self, hive_env: Path):
        from keephive.settings import get_setting, set_setting

        assert get_setting("sound_error") == "Basso"
        set_setting("sound_error", "Funk")
        assert get_setting("sound_error") == "Funk"


# ---- CLI command ----


class TestCmdSet:
    def test_no_args_shows_all(self, hive_env: Path, capsys):
        from keephive.commands.settings import cmd_set

        cmd_set([])
        out = capsys.readouterr().out
        assert "sound" in out
        assert "off" in out

    def test_show_single_key(self, hive_env: Path, capsys):
        from keephive.commands.settings import cmd_set

        cmd_set(["sound"])
        out = capsys.readouterr().out
        assert "sound" in out
        assert "off" in out

    def test_set_on(self, hive_env: Path, capsys):
        from keephive.commands.settings import cmd_set
        from keephive.settings import get_setting

        cmd_set(["sound", "on"])
        assert get_setting("sound") is True
        out = capsys.readouterr().out
        assert "on" in out

    def test_set_off(self, hive_env: Path, capsys):
        from keephive.commands.settings import cmd_set
        from keephive.settings import get_setting, set_setting

        set_setting("sound", True)
        cmd_set(["sound", "off"])
        assert get_setting("sound") is False

    def test_unknown_key_error(self, hive_env: Path, capsys):
        from keephive.commands.settings import cmd_set

        cmd_set(["bogus"])
        out = capsys.readouterr().out
        assert "Unknown setting" in out
        assert "sound" in out  # shows valid keys

    def test_invalid_bool_error(self, hive_env: Path, capsys):
        from keephive.commands.settings import cmd_set

        cmd_set(["sound", "maybe"])
        out = capsys.readouterr().out
        assert "Invalid value" in out

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("on", True),
            ("off", False),
            ("true", True),
            ("false", False),
            ("1", True),
            ("0", False),
            ("yes", True),
            ("no", False),
            ("ON", True),
            ("OFF", False),
            ("True", True),
            ("False", False),
        ],
    )
    def test_parse_bool_variants(self, hive_env: Path, val, expected):
        from keephive.commands.settings import cmd_set
        from keephive.settings import get_setting

        cmd_set(["sound", val])
        assert get_setting("sound") is expected


# ---- notify_sound ----


class TestNotifySound:
    def test_silent_when_sound_off(self, hive_env: Path, monkeypatch):
        """Should not call Popen when sound setting is off."""
        import platform
        import shutil
        import subprocess

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/afplay")

        notify_sound(True)
        assert len(calls) == 0

    def test_plays_when_sound_on(self, hive_env: Path, monkeypatch):
        """Should call Popen with Glass.aiff when sound=on and on macOS."""
        import platform
        import shutil
        import subprocess

        from keephive.settings import set_setting

        set_setting("sound", True)

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/afplay")
        monkeypatch.setattr(Path, "exists", lambda self: True)

        notify_sound(True)
        assert len(calls) == 1
        assert "Glass.aiff" in calls[0][0][1]

    def test_plays_basso_on_failure(self, hive_env: Path, monkeypatch):
        """Should play Basso.aiff for success=False."""
        import platform
        import shutil
        import subprocess

        from keephive.settings import set_setting

        set_setting("sound", True)

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/afplay")
        monkeypatch.setattr(Path, "exists", lambda self: True)

        notify_sound(False)
        assert len(calls) == 1
        assert "Basso.aiff" in calls[0][0][1]

    def test_silent_on_linux(self, hive_env: Path, monkeypatch):
        """Should not play on non-Darwin platforms."""
        import platform
        import subprocess

        from keephive.settings import set_setting

        set_setting("sound", True)

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Linux")

        notify_sound(True)
        assert len(calls) == 0

    def test_silent_when_no_afplay(self, hive_env: Path, monkeypatch):
        """Should not play when afplay is missing."""
        import platform
        import shutil
        import subprocess

        from keephive.settings import set_setting

        set_setting("sound", True)

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(shutil, "which", lambda x: None)

        notify_sound(True)
        assert len(calls) == 0

    def test_uses_custom_sound_name(self, hive_env: Path, monkeypatch):
        """Should use sound_success setting for the sound file."""
        import platform
        import shutil
        import subprocess

        from keephive.settings import set_setting

        set_setting("sound", True)
        set_setting("sound_success", "Pop")

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/afplay")
        monkeypatch.setattr(Path, "exists", lambda self: True)

        notify_sound(True)
        assert len(calls) == 1
        assert "Pop.aiff" in calls[0][0][1]

    def test_uses_custom_error_sound(self, hive_env: Path, monkeypatch):
        """Should use sound_error setting for error sounds."""
        import platform
        import shutil
        import subprocess

        from keephive.settings import set_setting

        set_setting("sound", True)
        set_setting("sound_error", "Funk")

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/afplay")
        monkeypatch.setattr(Path, "exists", lambda self: True)

        notify_sound(False)
        assert len(calls) == 1
        assert "Funk.aiff" in calls[0][0][1]

    def test_custom_file_path(self, hive_env: Path, monkeypatch):
        """Should use raw file path when name not in BUILTIN_SOUNDS."""
        import platform
        import shutil
        import subprocess

        from keephive.settings import set_setting

        set_setting("sound", True)
        set_setting("sound_success", "/tmp/my-sound.aiff")

        from keephive.output import notify_sound

        calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/afplay")
        monkeypatch.setattr(Path, "exists", lambda self: True)

        notify_sound(True)
        assert len(calls) == 1
        assert calls[0][0][1] == "/tmp/my-sound.aiff"


# ---- Settings panel in serve.py ----


class TestServeSettings:
    def test_settings_panel_renders(self, hive_env: Path):
        from keephive.commands.serve import _get_settings_data, _render_settings_panel

        data = _get_settings_data()
        html = _render_settings_panel(data)
        assert "sound" in html
        assert "setting-toggle" in html
        assert "Settings" in html

    def test_settings_panel_has_dropdowns(self, hive_env: Path):
        from keephive.commands.serve import _get_settings_data, _render_settings_panel

        data = _get_settings_data()
        html = _render_settings_panel(data)
        assert "setting-select" in html
        assert "sound_success" in html
        assert "sound_error" in html
        # Default Glass should be selected
        assert 'value="Glass" selected' in html
        assert 'value="Basso" selected' in html

    def test_settings_panel_has_test_buttons(self, hive_env: Path):
        from keephive.commands.serve import _get_settings_data, _render_settings_panel

        data = _get_settings_data()
        html = _render_settings_panel(data)
        assert "sound-test-btn" in html
        # Two play buttons: one for success, one for error
        assert html.count("sound-test-btn") == 2
        assert 'data-sound-type=""' in html  # success button
        assert 'data-sound-type="error"' in html  # error button

    def test_settings_data_includes_builtin_sounds(self, hive_env: Path):
        from keephive.commands.serve import _get_settings_data

        data = _get_settings_data()
        assert "builtin_sounds" in data
        assert "Glass" in data["builtin_sounds"]
        assert "Basso" in data["builtin_sounds"]
        assert len(data["builtin_sounds"]) == 14

    def test_settings_data_has_three_keys(self, hive_env: Path):
        from keephive.commands.serve import _get_settings_data

        data = _get_settings_data()
        settings = data["settings"]
        assert "sound" in settings
        assert "sound_success" in settings
        assert "sound_error" in settings

    def test_settings_in_panels(self):
        from keephive.commands.serve import PANELS

        assert "settings" in PANELS

    def test_settings_in_views(self):
        from keephive.commands.serve import VIEWS

        assert "settings" in VIEWS
        assert VIEWS["settings"]["path"] == "/settings"


# ---- BUILTIN_SOUNDS constant ----


class TestBuiltinSounds:
    def test_builtin_sounds_list(self):
        from keephive.settings import BUILTIN_SOUNDS

        assert isinstance(BUILTIN_SOUNDS, list)
        assert len(BUILTIN_SOUNDS) == 14
        assert "Glass" in BUILTIN_SOUNDS
        assert "Basso" in BUILTIN_SOUNDS
        assert "Pop" in BUILTIN_SOUNDS

    def test_defaults_reference_builtin_sounds(self):
        from keephive.settings import BUILTIN_SOUNDS, DEFAULTS

        assert DEFAULTS["sound_success"] in BUILTIN_SOUNDS
        assert DEFAULTS["sound_error"] in BUILTIN_SOUNDS


# ---- sound-test command ----


class TestSoundTestCommand:
    def test_sound_test_dispatches(self, hive_env: Path):
        from keephive.cli import COMMANDS

        assert "sound-test" in COMMANDS

    def test_sound_test_in_help(self):
        from keephive.cli import HELP

        assert "sound-test" in HELP
