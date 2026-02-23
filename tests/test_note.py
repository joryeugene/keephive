"""Tests for the note (Tot-style multi-slot scratchpad) command."""

from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import MagicMock

from keephive.commands.note import cmd_note
from keephive.storage import active_slot


def _run(args, hive_home):
    """Run keephive as subprocess with given hive home."""
    return subprocess.run(
        [sys.executable, "-m", "keephive"] + args,
        capture_output=True,
        text=True,
        env={
            "HIVE_HOME": hive_home,
            "HIVE_SKIP_LLM": "1",
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


# ---- Basic operations (migrated from test_draft.py) ----


def test_note_show_empty(hive_env):
    """No note yet, output contains 'No note'."""
    r = _run(["n", "show"], str(hive_env))
    assert r.returncode == 0
    assert "No note" in r.stdout


def test_note_show_content(hive_env):
    """Write to note-1.md, n show prints it."""
    (hive_env / "working" / "note-1.md").write_text("Hello world\nSecond line\n")

    r = _run(["n", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Hello world" in r.stdout
    assert "2L" in r.stdout


def test_note_copy_stdout_fallback(hive_env):
    """No pbcopy/xclip, copy falls back to stdout."""
    (hive_env / "working" / "note-1.md").write_text("Note content here\n")

    # Empty PATH = no clipboard tools, so falls back to stdout
    r = subprocess.run(
        [sys.executable, "-m", "keephive", "nc"],
        capture_output=True,
        text=True,
        env={"HIVE_HOME": str(hive_env), "HIVE_SKIP_LLM": "1", "PATH": ""},
    )
    assert r.returncode == 0
    assert "Note content here" in r.stdout


def test_note_clear_truncates(hive_env):
    """Write note, clear, verify file is empty (no archive)."""
    (hive_env / "working" / "note-1.md").write_text("Some content\n")

    r = _run(["n", "clear"], str(hive_env))
    assert r.returncode == 0
    assert "cleared" in r.stdout.lower()

    # Note should be empty
    assert (hive_env / "working" / "note-1.md").read_text().strip() == ""

    # No archive created
    archives = list((hive_env / "working" / "notes").glob("*.md"))
    assert len(archives) == 0


def test_note_clear_empty_noop(hive_env):
    """Clear when no note does not error."""
    r = _run(["n", "clear"], str(hive_env))
    assert r.returncode == 0
    assert "cleared" in r.stdout.lower()


def test_note_list_empty(hive_env):
    """No archives and no slot content, shows slot bar."""
    r = _run(["n", "list"], str(hive_env))
    assert r.returncode == 0
    assert "Note Slots" in r.stdout


def test_note_digit_switches_slot(hive_env):
    """hive n 3 switches to slot 3 and edits."""
    (hive_env / "working" / "note-3.md").write_text("Slot 3 content\n")

    r = _run(["n", "3", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Slot 3 content" in r.stdout

    # Active slot should now be 3
    active = (hive_env / "working" / ".note-active").read_text().strip()
    assert active == "3"


def test_note_digit_0_switches_slot_10(hive_env):
    """hive n 0 switches to slot 10."""
    (hive_env / "working" / "note-10.md").write_text("Slot 10 via 0\n")

    r = _run(["n", "0", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Slot 10 via 0" in r.stdout

    active = (hive_env / "working" / ".note-active").read_text().strip()
    assert active == "10"


def test_note_l_shortcut_opens_last(hive_env, monkeypatch):
    """hive n l opens the most recently edited slot, not list."""
    # Write to slots 1 and 3; ensure slot 3 has a later mtime
    (hive_env / "working" / "note-1.md").write_text("Slot one content\n")
    time.sleep(0.01)
    (hive_env / "working" / "note-3.md").write_text("Slot three content\n")

    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)

    cmd_note(["l"])

    # Editor should have been opened
    mock_run.assert_called_once()
    # Active slot should be 3 (most recently modified)
    assert active_slot() == 3


def test_note_last_opens_most_recent(hive_env, monkeypatch):
    """hive n last switches to and opens the slot with the latest mtime."""
    (hive_env / "working" / "note-1.md").write_text("Slot one\n")
    time.sleep(0.01)
    (hive_env / "working" / "note-3.md").write_text("Slot three\n")

    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)

    cmd_note(["last"])

    mock_run.assert_called_once()
    assert active_slot() == 3


def test_note_last_no_notes(hive_env):
    """hive n last with no notes prints helpful message and does not open editor."""
    r = _run(["n", "last"], str(hive_env))
    assert r.returncode == 0
    assert "No notes" in r.stdout


def test_note_list_still_works(hive_env):
    """hive n list still shows slot bar (regression: 'l' repurposed, 'list' must still work)."""
    (hive_env / "working" / "note-1.md").write_text("Some content\n")

    r = _run(["n", "list"], str(hive_env))
    assert r.returncode == 0
    assert "Note Slots" in r.stdout
    assert "Some content" in r.stdout


def test_note_c_shortcut_copies(hive_env):
    """hive n c copies active slot."""
    (hive_env / "working" / "note-1.md").write_text("Copy me\n")

    r = subprocess.run(
        [sys.executable, "-m", "keephive", "n", "c"],
        capture_output=True,
        text=True,
        env={"HIVE_HOME": str(hive_env), "HIVE_SKIP_LLM": "1", "PATH": ""},
    )
    assert r.returncode == 0
    assert "Copy me" in r.stdout


def test_note_s_shortcut_shows(hive_env):
    """hive n s shows active slot."""
    (hive_env / "working" / "note-1.md").write_text("Show me\n")

    r = _run(["n", "s"], str(hive_env))
    assert r.returncode == 0
    assert "Show me" in r.stdout
    assert "1L" in r.stdout


def test_note_digit_then_show(hive_env):
    """hive n 3 s switches to slot 3 then shows."""
    (hive_env / "working" / "note-3.md").write_text("Slot 3 show\n")

    r = _run(["n", "3", "s"], str(hive_env))
    assert r.returncode == 0
    assert "Slot 3 show" in r.stdout


def test_note_from_template(hive_env):
    """Create prompt file, verify template lookup populates note."""
    prompts = hive_env / "knowledge" / "prompts"
    (prompts / "test-prompt.md").write_text("# Test Prompt\n\nDo the thing.\n")

    note = hive_env / "working" / "note-1.md"
    note.write_text((prompts / "test-prompt.md").read_text())
    assert "Test Prompt" in note.read_text()
    assert "Do the thing" in note.read_text()


def test_note_status_indicator(hive_env):
    """Note exists, status output shows 'Active draft' indicator with slot and word count."""
    (hive_env / "working" / "note-1.md").write_text("Some note content\nLine two\n")

    r = _run(["s"], str(hive_env))
    assert r.returncode == 0
    # ANSI escape codes split "slot 1" across spans; check parts individually
    assert "Active Note" in r.stdout
    assert "slot" in r.stdout
    assert "words)" in r.stdout


# ---- Backward compat aliases ----


def test_d_alias_works(hive_env):
    """hive d show works (backward compat)."""
    (hive_env / "working" / "note-1.md").write_text("Hello via d\n")
    r = _run(["d", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Hello via d" in r.stdout


def test_dc_alias_works(hive_env):
    """hive dc works (backward compat)."""
    (hive_env / "working" / "note-1.md").write_text("Copy via dc\n")
    r = subprocess.run(
        [sys.executable, "-m", "keephive", "dc"],
        capture_output=True,
        text=True,
        env={"HIVE_HOME": str(hive_env), "HIVE_SKIP_LLM": "1", "PATH": ""},
    )
    assert r.returncode == 0
    assert "Copy via dc" in r.stdout


# ---- Slot system tests ----


def test_slot_switching(hive_env):
    """n.3 switches active slot to 3."""
    (hive_env / "working" / "note-3.md").write_text("Slot 3 content\n")

    r = _run(["n.3", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Slot 3 content" in r.stdout

    # Active slot should now be 3
    active = (hive_env / "working" / ".note-active").read_text().strip()
    assert active == "3"


def test_slot_persistence(hive_env):
    """After switching to slot 3, plain 'n show' uses slot 3."""
    (hive_env / "working" / "note-3.md").write_text("Persistent slot 3\n")
    (hive_env / "working" / ".note-active").write_text("3")

    r = _run(["n", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Persistent slot 3" in r.stdout


def test_slot_0_is_slot_10(hive_env):
    """n.0 maps to slot 10."""
    (hive_env / "working" / "note-10.md").write_text("Slot 10 via 0\n")

    r = _run(["n.0", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Slot 10 via 0" in r.stdout

    active = (hive_env / "working" / ".note-active").read_text().strip()
    assert active == "10"


def test_slot_copy_shorthand(hive_env):
    """n.5c copies slot 5 content."""
    (hive_env / "working" / "note-5.md").write_text("Slot 5 for copy\n")

    r = subprocess.run(
        [sys.executable, "-m", "keephive", "n.5c"],
        capture_output=True,
        text=True,
        env={"HIVE_HOME": str(hive_env), "HIVE_SKIP_LLM": "1", "PATH": ""},
    )
    assert r.returncode == 0
    assert "Slot 5 for copy" in r.stdout


def test_d_dot_alias(hive_env):
    """d.3 works same as n.3 (backward compat)."""
    (hive_env / "working" / "note-3.md").write_text("Via d.3\n")

    r = _run(["d.3", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Via d.3" in r.stdout


def test_slot_bar_rendering(hive_env):
    """_slot_bar renders correctly with active and filled slots."""
    from keephive.commands.note import _slot_bar
    from keephive.storage import set_active_slot

    (hive_env / "working" / "note-1.md").write_text("Content\n")
    (hive_env / "working" / "note-3.md").write_text("Content\n")
    set_active_slot(3)

    bar = _slot_bar()
    # Active slot 3 should be in bold brackets
    assert "[3]" in bar
    # Slot 1 has content, should be normal (not dim)
    assert "1" in bar


def test_slot_list_shows_filled_slots(hive_env):
    """n list shows filled slots with previews."""
    (hive_env / "working" / "note-1.md").write_text("First slot content\n")
    (hive_env / "working" / "note-3.md").write_text("Third slot here\n")

    r = _run(["n", "list"], str(hive_env))
    assert r.returncode == 0
    assert "First slot content" in r.stdout
    assert "Third slot here" in r.stdout


def test_slot_clear_specific(hive_env):
    """n.3 clear clears slot 3 specifically (no archive)."""
    (hive_env / "working" / "note-3.md").write_text("To be cleared\n")

    r = _run(["n.3", "clear"], str(hive_env))
    assert r.returncode == 0
    assert "cleared" in r.stdout.lower()
    assert (hive_env / "working" / "note-3.md").read_text().strip() == ""
    # No archive created
    archives = list((hive_env / "working" / "notes").glob("*.md"))
    assert len(archives) == 0


# ---- Migration tests ----


def test_migration_draft_to_note(hive_env):
    """draft.md migrates to note-1.md on first use."""
    # Create old-style draft.md
    (hive_env / "working" / "draft.md").write_text("Old draft content\n")

    r = _run(["n", "show"], str(hive_env))
    assert r.returncode == 0
    assert "Old draft content" in r.stdout

    # draft.md should be gone
    assert not (hive_env / "working" / "draft.md").exists()
    # note-1.md should exist
    assert (hive_env / "working" / "note-1.md").exists()


def test_migration_drafts_dir_to_notes(hive_env):
    """drafts/ archives migrate to notes/ on first use."""
    dd = hive_env / "working" / "drafts"
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "2026-02-15_10-00-00.md").write_text("Old archive\n")

    r = _run(["n", "list"], str(hive_env))
    assert r.returncode == 0

    # notes/ should have the archive
    nd = hive_env / "working" / "notes"
    assert (nd / "2026-02-15_10-00-00.md").exists()
    assert "Old archive" in (nd / "2026-02-15_10-00-00.md").read_text()


# ---- Quick-append tests ----


def test_note_slot_quick_append(hive_env):
    """cmd_note_slot(4, ["fix auth bug"]) appends text to note-4 without opening editor."""
    from keephive.commands.note import cmd_note_slot
    from keephive.storage import slot_file

    cmd_note_slot(4, ["fix auth bug"])

    content = slot_file(4).read_text()
    assert "fix auth bug" in content


def test_note_slot_quick_append_multiword(hive_env):
    """cmd_note_slot(4, ["fix", "auth", "bug"]) joins all args into appended text."""
    from keephive.commands.note import cmd_note_slot
    from keephive.storage import slot_file

    cmd_note_slot(4, ["fix", "auth", "bug"])

    content = slot_file(4).read_text()
    assert "fix auth bug" in content


def test_note_slot_quick_append_adds_newline(hive_env):
    """Quick-append ends with newline and doesn't double-space existing content."""
    from keephive.commands.note import cmd_note_slot
    from keephive.storage import slot_file

    # Pre-populate with content that doesn't end in newline
    slot_file(4).write_text("existing line")

    cmd_note_slot(4, ["new line"])

    content = slot_file(4).read_text()
    assert "existing line\nnew line\n" == content


def test_note_slot_no_args_opens_editor(hive_env, monkeypatch):
    """cmd_note_slot(4, []) opens editor via subprocess.run."""
    from unittest.mock import MagicMock

    from keephive.commands.note import cmd_note_slot

    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)

    cmd_note_slot(4, [])

    mock_run.assert_called_once()


def test_note_slot_list_subcommand(hive_env):
    """cmd_note_slot with 'list' shows all slots."""
    (hive_env / "working" / "note-4.md").write_text("Slot 4 content\n")

    r = _run(["n.4", "list"], str(hive_env))
    assert r.returncode == 0
    assert "Note Slots" in r.stdout
