"""Tests for progressive CLI help (adaptive Recent/Discover layout)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from keephive.cli import _CMD_FAMILIES, _command_usage, _help, _help_grouped


def _make_stats(days_commands: dict[str, dict[str, int]]) -> dict:
    """Build a stats dict from {date_str: {cmd: count}} mapping."""
    days = {}
    for day_str, cmds in days_commands.items():
        days[day_str] = {"commands": cmds}
    return {"days": days}


def _today_str(days_ago: int = 0) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


# -- Fallback tests --


def test_help_no_stats(monkeypatch, capsys):
    """No .stats.json exists: falls back to grouped layout."""
    monkeypatch.setattr(
        "keephive.cli.read_stats",
        lambda: {"days": {}},
        raising=False,
    )
    # _command_usage imports read_stats lazily, so we patch the storage module
    import keephive.storage

    monkeypatch.setattr(keephive.storage, "read_stats", lambda: {"days": {}})

    _help()
    out = capsys.readouterr().out
    assert "Capture & Search" in out


def test_help_all_unchanged(capsys):
    """--all produces the grouped layout with Plumbing section."""
    _help(show_all=True)
    out = capsys.readouterr().out
    assert "Plumbing" in out
    assert "rule [learn|review]" in out
    assert "setup" in out
    assert "hive help --all" not in out  # no self-reference in --all


def test_help_corrupt_stats(monkeypatch, capsys):
    """Corrupt stats: graceful fallback to grouped layout."""
    import keephive.storage

    def _raise():
        raise json.JSONDecodeError("bad", "", 0)

    monkeypatch.setattr(keephive.storage, "read_stats", lambda: (_ for _ in ()).throw(ValueError("corrupt")))
    # _command_usage catches all exceptions
    _help()
    out = capsys.readouterr().out
    assert "Capture & Search" in out


# -- Adaptive layout tests --


def _patch_stats(monkeypatch, stats_data: dict):
    """Patch read_stats in both keephive.storage (for lazy import) and keephive.cli."""
    import keephive.storage

    monkeypatch.setattr(keephive.storage, "read_stats", lambda: stats_data)


def test_help_adaptive_recent(monkeypatch, capsys):
    """Commands used in last 7 days appear in Recent section, sorted by frequency."""
    stats = _make_stats({
        _today_str(1): {"s": 10, "status": 2, "v": 5},
        _today_str(2): {"r": 3},
    })
    _patch_stats(monkeypatch, stats)

    _help()
    out = capsys.readouterr().out
    assert "Recent" in out
    lines = out.split("\n")

    # Find the Recent section entries
    recent_lines = []
    in_recent = False
    for line in lines:
        if "Recent" in line and "Shorthand" in line:
            in_recent = True
            continue
        if in_recent:
            if line.strip() == "" or "Discover" in line:
                break
            recent_lines.append(line.strip())

    # status (12 total) should come before verify (5) which comes before remember (3)
    labels = [l.split()[0] for l in recent_lines if l]
    assert labels.index("status") < labels.index("verify")
    assert labels.index("verify") < labels.index("remember")


def test_help_adaptive_discover(monkeypatch, capsys):
    """Undiscovered commands appear in Discover section in priority order."""
    # Only status and remember ever used
    stats = _make_stats({
        _today_str(1): {"s": 5},
        _today_str(30): {"r": 1},  # old but counts for all-time
    })
    _patch_stats(monkeypatch, stats)

    _help()
    out = capsys.readouterr().out
    assert "Discover" in out

    # Discover should list commands the user has never tried
    # "recall" (index 2) should be in discover since it was never used
    assert "recall" in out


def test_help_discover_cap(monkeypatch, capsys):
    """Discover shows at most 6 commands, with overflow message."""
    # Only status ever used (index 0)
    stats = _make_stats({
        _today_str(1): {"s": 1},
    })
    _patch_stats(monkeypatch, stats)

    _help()
    out = capsys.readouterr().out
    assert "Discover" in out

    # Count discover entries (lines with 4-space indent after "Discover" header)
    lines = out.split("\n")
    discover_entries = []
    in_discover = False
    for line in lines:
        if "Discover" in line and "Recent" not in line:
            in_discover = True
            continue
        if in_discover:
            stripped = line.strip()
            if not stripped or stripped.startswith("Run ") or stripped.startswith("..."):
                if stripped.startswith("..."):
                    discover_entries.append(stripped)
                continue
            discover_entries.append(stripped)

    # Should have exactly 6 command lines + overflow message
    cmd_lines = [l for l in discover_entries if not l.startswith("...")]
    overflow_lines = [l for l in discover_entries if l.startswith("...")]
    assert len(cmd_lines) == 6
    assert len(overflow_lines) == 1
    assert "more" in overflow_lines[0]
    assert "hive help --all" in overflow_lines[0]


def test_help_all_used(monkeypatch, capsys):
    """All command families used: no Discover section."""
    # Build stats covering every family
    cmds: dict[str, int] = {}
    for _, _, _, aliases in _CMD_FAMILIES:
        for alias in aliases:
            cmds[alias] = 1
            break  # one alias per family is enough
    stats = _make_stats({_today_str(1): cmds})
    _patch_stats(monkeypatch, stats)

    _help()
    out = capsys.readouterr().out
    assert "Discover" not in out
    assert "Recent" in out


def test_help_no_recent(monkeypatch, capsys):
    """Commands used only >7 days ago: no Recent section, Discover shows unused."""
    # Only status used, but 10 days ago
    stats = _make_stats({
        _today_str(10): {"s": 5, "r": 3},
    })
    _patch_stats(monkeypatch, stats)

    _help()
    out = capsys.readouterr().out
    assert "Recent" not in out
    assert "Discover" in out


# -- _command_usage tests --


def test_command_usage_aggregation(monkeypatch):
    """Aliases aggregate to the same family (e.g. 's' and 'status' both count for status)."""
    stats = _make_stats({
        _today_str(1): {"s": 5, "status": 3},
    })
    _patch_stats(monkeypatch, stats)

    recent, all_time = _command_usage(7)
    # status family is index 0
    assert 0 in recent
    assert recent[0] == 8  # 5 + 3


def test_command_usage_note_slots(monkeypatch):
    """note.N patterns count toward the note family."""
    stats = _make_stats({
        _today_str(1): {"note.3": 2, "note.0": 1, "n": 4},
    })
    _patch_stats(monkeypatch, stats)

    recent, all_time = _command_usage(7)
    # note family is index 7
    note_idx = next(
        i for i, (label, _, _, _) in enumerate(_CMD_FAMILIES) if label == "note"
    )
    assert note_idx in recent
    assert recent[note_idx] == 7  # 2 + 1 + 4


def test_command_usage_empty_stats(monkeypatch):
    """Empty stats returns empty results."""
    _patch_stats(monkeypatch, {"days": {}})
    recent, all_time = _command_usage(7)
    assert recent == {}
    assert all_time == set()
