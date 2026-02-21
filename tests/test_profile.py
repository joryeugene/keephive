"""Tests for profile lifecycle: create, list, use, delete."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def profile_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a clean ~/.claude dir for profile tests.

    Uses monkeypatching to redirect _claude_dir() to tmp_path/.claude
    so we never touch the real home directory.
    """
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # Create default hive dir
    default_hive = claude_dir / "hive"
    default_hive.mkdir()
    for sub in [
        "working",
        "daily",
        "knowledge/guides",
        "knowledge/prompts",
        "working/notes",
        "archive",
    ]:
        (default_hive / sub).mkdir(parents=True, exist_ok=True)

    # Patch _claude_dir to use our temp dir
    monkeypatch.setattr("keephive.storage._claude_dir", lambda: claude_dir)
    # Clear HIVE_HOME so profiles are active
    monkeypatch.delenv("HIVE_HOME", raising=False)
    # Clear any existing profile file
    (claude_dir / ".hive-profile").unlink(missing_ok=True)

    return claude_dir


def test_default_profile_is_none(profile_env):
    """No profile file means default profile (None)."""
    from keephive.storage import active_profile

    assert active_profile() is None


def test_hive_dir_default(profile_env):
    """Without a profile, hive_dir() points to default."""
    from keephive.storage import hive_dir

    assert hive_dir() == profile_env / "hive"


def test_set_and_get_profile(profile_env):
    """set_active_profile persists across calls."""
    from keephive.storage import active_profile, set_active_profile

    set_active_profile("demo")
    assert active_profile() == "demo"


def test_hive_dir_with_profile(profile_env):
    """hive_dir() resolves to hive-<name> when profile is active."""
    from keephive.storage import hive_dir, set_active_profile

    set_active_profile("demo")
    assert hive_dir() == profile_env / "hive-demo"


def test_clear_profile(profile_env):
    """set_active_profile(None) removes profile file."""
    from keephive.storage import active_profile, set_active_profile

    set_active_profile("work")
    assert active_profile() == "work"
    set_active_profile(None)
    assert active_profile() is None


def test_hive_home_overrides_profile(profile_env, monkeypatch):
    """HIVE_HOME env var takes priority over active profile."""
    from keephive.storage import active_profile, hive_dir, set_active_profile

    custom = profile_env.parent / "custom-hive"
    custom.mkdir()
    monkeypatch.setenv("HIVE_HOME", str(custom))

    set_active_profile("demo")
    # active_profile returns None when HIVE_HOME is set
    assert active_profile() is None
    # hive_dir uses HIVE_HOME, not profile
    assert hive_dir() == custom


def test_list_profiles_default_only(profile_env):
    """list_profiles shows default when no named profiles exist."""
    from keephive.storage import list_profiles

    profiles = list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "default"
    assert profiles[0]["active"] is True


def test_list_profiles_with_named(profile_env):
    """list_profiles finds hive-<name> directories."""
    from keephive.storage import list_profiles

    (profile_env / "hive-demo").mkdir()
    (profile_env / "hive-work").mkdir()
    profiles = list_profiles()
    names = [p["name"] for p in profiles]
    assert "default" in names
    assert "demo" in names
    assert "work" in names


def test_list_profiles_active_indicator(profile_env):
    """Active profile is marked in list."""
    from keephive.storage import list_profiles, set_active_profile

    (profile_env / "hive-demo").mkdir()
    set_active_profile("demo")
    profiles = list_profiles()
    demo = next(p for p in profiles if p["name"] == "demo")
    default = next(p for p in profiles if p["name"] == "default")
    assert demo["active"] is True
    assert default["active"] is False


def test_profile_dir_default(profile_env):
    """profile_dir('default') returns hive/ not hive-default/."""
    from keephive.storage import profile_dir

    assert profile_dir("default") == profile_env / "hive"


def test_profile_dir_named(profile_env):
    """profile_dir('demo') returns hive-demo/."""
    from keephive.storage import profile_dir

    assert profile_dir("demo") == profile_env / "hive-demo"


def test_create_profile_scaffolds(profile_env, capsys):
    """cmd_profile create scaffolds directories."""
    from keephive.commands.profile import cmd_profile
    from keephive.storage import profile_dir

    cmd_profile(["create", "test-proj"])
    target = profile_dir("test-proj")
    assert target.exists()
    assert (target / "working").exists()
    assert (target / "daily").exists()
    assert (target / "knowledge" / "guides").exists()
    out = capsys.readouterr().out
    assert "Created" in out


def test_create_profile_already_exists(profile_env, capsys):
    """Creating an existing profile shows warning."""
    from keephive.commands.profile import cmd_profile

    cmd_profile(["create", "dup"])
    cmd_profile(["create", "dup"])
    out = capsys.readouterr().out
    assert "already exists" in out


def test_create_profile_invalid_name(profile_env):
    """Invalid names are rejected."""
    from keephive.commands.profile import cmd_profile

    with pytest.raises(SystemExit):
        cmd_profile(["create", "UPPERCASE"])

    with pytest.raises(SystemExit):
        cmd_profile(["create", "has spaces"])


def test_use_nonexistent_profile(profile_env):
    """Using a nonexistent profile shows error."""
    from keephive.commands.profile import cmd_profile

    with pytest.raises(SystemExit):
        cmd_profile(["use", "ghost"])


def test_use_profile_switches(profile_env, capsys):
    """use switches the active profile."""
    from keephive.commands.profile import cmd_profile
    from keephive.storage import active_profile

    cmd_profile(["create", "demo"])
    cmd_profile(["use", "demo"])
    assert active_profile() == "demo"


def test_use_default_switches_back(profile_env, capsys):
    """use default clears the profile."""
    from keephive.commands.profile import cmd_profile
    from keephive.storage import active_profile

    cmd_profile(["create", "demo"])
    cmd_profile(["use", "demo"])
    assert active_profile() == "demo"
    cmd_profile(["use", "default"])
    assert active_profile() is None


def test_delete_active_profile_refused(profile_env):
    """Cannot delete the currently active profile."""
    from keephive.commands.profile import cmd_profile
    from keephive.storage import set_active_profile

    (profile_env / "hive-active").mkdir()
    set_active_profile("active")
    with pytest.raises(SystemExit):
        cmd_profile(["delete", "active"])


def test_delete_default_refused(profile_env):
    """Cannot delete the default profile."""
    from keephive.commands.profile import cmd_profile

    with pytest.raises(SystemExit):
        cmd_profile(["delete", "default"])


def test_delete_profile(profile_env, monkeypatch, capsys):
    """Delete removes directory after confirmation."""
    from keephive.commands.profile import cmd_profile
    from keephive.storage import profile_dir

    cmd_profile(["create", "temp"])
    target = profile_dir("temp")
    assert target.exists()

    # Auto-confirm the deletion prompt
    monkeypatch.setattr("keephive.output.prompt_yn", lambda *a, **kw: True)
    cmd_profile(["delete", "temp"])
    assert not target.exists()


def test_full_lifecycle(profile_env, monkeypatch, capsys):
    """Create -> use -> write data -> switch back -> data isolated."""
    from keephive.commands.profile import cmd_profile
    from keephive.storage import hive_dir, memory_file

    # Create and switch to demo
    cmd_profile(["create", "demo"])
    cmd_profile(["use", "demo"])

    # Write some data
    mem = memory_file()
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text("- FACT: demo data [verified:2026-01-01]\n")
    assert "demo data" in mem.read_text()

    # Switch back to default
    cmd_profile(["use", "default"])

    # Default memory should NOT contain demo data
    default_mem = memory_file()
    if default_mem.exists():
        assert "demo data" not in default_mem.read_text()
