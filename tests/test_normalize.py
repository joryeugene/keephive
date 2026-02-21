"""Tests for memory normalization and tag stripping."""

from __future__ import annotations

from pathlib import Path

from keephive.storage import _strip_verified_tags, normalize_memory

# ---- _strip_verified_tags ----


def test_strip_single_tag():
    assert _strip_verified_tags("some fact [verified:2026-02-21]") == "some fact"


def test_strip_double_tags():
    text = "some fact [verified:2026-02-20] [verified:2026-02-21]"
    assert _strip_verified_tags(text) == "some fact"


def test_strip_no_tags():
    assert _strip_verified_tags("plain text") == "plain text"


def test_strip_preserves_content_with_brackets():
    text = "FACT: keephive v0.18.0 [verified:2026-02-21]"
    assert _strip_verified_tags(text) == "FACT: keephive v0.18.0"


# ---- normalize_memory ----


def test_normalize_strips_double_tags(tmp_path: Path):
    mem = tmp_path / "memory.md"
    mem.write_text(
        "# Working Memory\n\n- FACT: something [verified:2026-02-20] [verified:2026-02-20]\n"
    )
    stats = normalize_memory(mem)
    assert stats["double_tags"] == 1
    content = mem.read_text()
    assert content.count("[verified:") == 1
    assert "[verified:2026-02-20]" in content


def test_normalize_keeps_last_date(tmp_path: Path):
    mem = tmp_path / "memory.md"
    mem.write_text(
        "# Working Memory\n\n- FACT: changed [verified:2026-02-10] [verified:2026-02-21]\n"
    )
    stats = normalize_memory(mem)
    assert stats["double_tags"] == 1
    content = mem.read_text()
    assert "[verified:2026-02-21]" in content
    assert "[verified:2026-02-10]" not in content


def test_normalize_removes_resolved_todos(tmp_path: Path):
    mem = tmp_path / "memory.md"
    mem.write_text(
        "# Working Memory\n\n"
        "- TODO (RESOLVED): hive serve dashboard gaps [verified:2026-02-21]\n"
        "- FACT: real fact [verified:2026-02-21]\n"
    )
    stats = normalize_memory(mem)
    assert stats["resolved_todos"] == 1
    content = mem.read_text()
    assert "TODO (RESOLVED)" not in content
    assert "real fact" in content


def test_normalize_fixes_double_dash(tmp_path: Path):
    mem = tmp_path / "memory.md"
    mem.write_text("# Working Memory\n\n- - FACT: double dash [verified:2026-02-21]\n")
    stats = normalize_memory(mem)
    assert stats["malformed_prefix"] == 1
    content = mem.read_text()
    assert "- FACT: double dash" in content
    assert "- - " not in content


def test_normalize_deduplicates(tmp_path: Path):
    mem = tmp_path / "memory.md"
    mem.write_text(
        "# Working Memory\n\n"
        "- FACT: keephive Phase 4 complete [verified:2026-02-20]\n"
        "- FACT: keephive Phase 4 complete [verified:2026-02-21]\n"
    )
    stats = normalize_memory(mem)
    assert stats["deduped"] == 1
    content = mem.read_text()
    # First occurrence kept
    assert content.count("Phase 4 complete") == 1


def test_normalize_preserves_clean_lines(tmp_path: Path):
    mem = tmp_path / "memory.md"
    original = (
        "# Working Memory\n\n"
        "- FACT: clean line [verified:2026-02-21]\n"
        "- DECISION: another clean line [verified:2026-02-20]\n"
    )
    mem.write_text(original)
    stats = normalize_memory(mem)
    assert stats == {"double_tags": 0, "resolved_todos": 0, "malformed_prefix": 0, "deduped": 0}
    # Content preserved (normalize always writes with trailing newline)
    content = mem.read_text()
    assert "clean line" in content
    assert "another clean line" in content


def test_normalize_handles_missing_file(tmp_path: Path):
    mem = tmp_path / "nonexistent.md"
    stats = normalize_memory(mem)
    assert stats == {"double_tags": 0, "resolved_todos": 0, "malformed_prefix": 0, "deduped": 0}
    assert not mem.exists()


def test_normalize_preserves_section_headers(tmp_path: Path):
    mem = tmp_path / "memory.md"
    mem.write_text(
        "# Working Memory\n\n"
        "## User Preferences\n\n"
        "- FACT: a fact [verified:2026-02-21]\n\n"
        "## Auto-Captured\n"
        "- FACT: auto fact [verified:2026-02-21]\n"
    )
    stats = normalize_memory(mem)
    content = mem.read_text()
    assert "## User Preferences" in content
    assert "## Auto-Captured" in content
    assert stats["double_tags"] == 0


def test_normalize_multiple_issues(tmp_path: Path):
    """All issue types fixed in one pass."""
    mem = tmp_path / "memory.md"
    mem.write_text(
        "# Working Memory\n\n"
        "- FACT: doubled [verified:2026-02-20] [verified:2026-02-20]\n"
        "- TODO (RESOLVED): old todo [verified:2026-02-21]\n"
        "- - FACT: double dash [verified:2026-02-21]\n"
        "- FACT: dup line [verified:2026-02-20]\n"
        "- FACT: dup line [verified:2026-02-21]\n"
    )
    stats = normalize_memory(mem)
    assert stats["double_tags"] == 1
    assert stats["resolved_todos"] == 1
    assert stats["malformed_prefix"] == 1
    assert stats["deduped"] == 1
    content = mem.read_text()
    assert content.count("[verified:") == 3  # doubled->1, double dash->1, dup kept->1


# ---- precompact tag stripping ----


def test_add_to_auto_captured_strips_existing_tag():
    """Input text with [verified:] tag produces single tag in output."""
    from keephive.hooks.precompact import _add_to_auto_captured

    content = "# Working Memory\n\n## Auto-Captured\n- existing [verified:2026-02-20]\n"
    result = _add_to_auto_captured(content, "new fact [verified:2026-02-19]", "2026-02-21")
    # Should have exactly one [verified:] tag for the new line
    new_lines = [line for line in result.splitlines() if "new fact" in line]
    assert len(new_lines) == 1
    assert new_lines[0].count("[verified:") == 1
    assert "[verified:2026-02-21]" in new_lines[0]
    assert "[verified:2026-02-19]" not in new_lines[0]


def test_correct_in_memory_strips_existing_tag():
    """Correction with [verified:] in new_text produces single tag."""
    from keephive.hooks.precompact import _correct_in_memory

    content = "- old fact [verified:2026-02-10]\n"
    result = _correct_in_memory(
        content, "old fact", "corrected fact [verified:2026-02-19]", "2026-02-21"
    )
    assert result.count("[verified:") == 1
    assert "[verified:2026-02-21]" in result
    assert "[verified:2026-02-19]" not in result


def test_update_contradiction_strips_existing_tag():
    """reflect's _update_contradiction strips tags from new_text."""
    from keephive.commands.reflect import _update_contradiction

    content = "- old version info [verified:2026-02-10]\n"
    result = _update_contradiction(
        content, "old version info", "new version info [verified:2026-02-18]", "2026-02-21"
    )
    assert result.count("[verified:") == 1
    assert "[verified:2026-02-21]" in result
    assert "[verified:2026-02-18]" not in result
