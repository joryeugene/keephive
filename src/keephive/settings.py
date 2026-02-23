"""Persistent user settings for keephive.

Settings file: ~/.keephive/hive/.settings.json (hidden, peers with .stats.json).
"""

from __future__ import annotations

import json

from keephive.storage import hive_dir

# Single source of truth for valid keys and defaults.
DEFAULTS: dict[str, bool | str | int] = {
    "sound": False,
    "sound_success": "Glass",
    "sound_error": "Basso",
    "llm_backend": "",
}

DESCRIPTIONS: dict[str, str] = {
    "sound": "Audio notification after LLM commands",
    "sound_success": "Sound for successful completion",
    "sound_error": "Sound for errors",
    "llm_backend": "Preferred LLM backend (blank = auto detect)",
}

BUILTIN_SOUNDS: list[str] = [
    "Basso",
    "Blow",
    "Bottle",
    "Frog",
    "Funk",
    "Glass",
    "Hero",
    "Morse",
    "Ping",
    "Pop",
    "Purr",
    "Sosumi",
    "Submarine",
    "Tink",
]


def settings_file():
    """Path to the settings JSON file."""
    return hive_dir() / ".settings.json"


def read_settings() -> dict:
    """Read all settings, merging stored values over defaults.

    Corrupt or missing file returns defaults silently.
    """
    result = dict(DEFAULTS)
    sf = settings_file()
    if not sf.exists():
        return result
    try:
        stored = json.loads(sf.read_text())
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in DEFAULTS:
                    result[k] = v
    except (json.JSONDecodeError, OSError):
        pass
    return result


def get_setting(key: str):
    """Read a single setting value with default fallback."""
    return read_settings().get(key, DEFAULTS.get(key))


def set_setting(key: str, value) -> None:
    """Read-modify-write a single setting key."""
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting: {key!r}")
    sf = settings_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    current: dict = {}
    if sf.exists():
        try:
            current = json.loads(sf.read_text())
            if not isinstance(current, dict):
                current = {}
        except (json.JSONDecodeError, OSError):
            current = {}
    current[key] = value
    sf.write_text(json.dumps(current, indent=2))
