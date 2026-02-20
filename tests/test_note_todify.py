"""Tests for hive n todo extraction (structured + LLM fallback)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from unittest.mock import patch

from keephive.commands.note import (
    _build_todo_buffer,
    _extract_structured_items,
    _note_extract_todos,
)
from keephive.models import NoteExtractResponse
from keephive.storage import daily_dir, open_todos, slot_file


def _accept_all(*args, **kwargs):
    """Mock editor that touches the file (updates mtime) without changing content."""
    Path(args[0][1]).touch()


def test_todify_structured(hive_env, monkeypatch):
    """## todo section parses without LLM. All short items → editor opens, accept all."""
    slot_file(4).write_text(
        "## notes\nsome context here\n\n## todo\n- adoption goals api testing\n"
        "- prompt library figma\n- andy feedback loess\n\n## done\n- old task\n"
    )
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")
    monkeypatch.setattr("subprocess.run", _accept_all)

    _note_extract_todos(4)

    todos = open_todos()
    texts = [t for _, _, t in todos]
    assert any("adoption goals" in t for t in texts)
    assert any("prompt library" in t for t in texts)
    assert any("loess" in t.lower() for t in texts)
    # old task (done section) should NOT be added
    assert not any("old task" in t for t in texts)


def test_todify_prose(hive_env, monkeypatch):
    """Freeform prose triggers LLM extraction."""
    slot_file(1).write_text(
        "We need to fix the auth bug before the demo. Also the logging is broken and "
        "Tom asked us to update the Figma spec before the next design review."
    )
    monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)

    mock_response = NoteExtractResponse(
        items=["fix auth bug before demo", "update Figma spec before design review"]
    )
    monkeypatch.setattr("subprocess.run", _accept_all)

    with patch("keephive.commands.note.run_claude_pipe", return_value=mock_response):
        _note_extract_todos(1)

    todos = open_todos()
    texts = [t for _, _, t in todos]
    assert any("auth bug" in t for t in texts)
    assert any("figma" in t.lower() for t in texts)
    # Verify log format: - [HH:MM:SS] TODO: text
    today_log = daily_dir() / f"{date.today().isoformat()}.md"
    log_content = today_log.read_text()
    assert re.search(r"\[\d{2}:\d{2}:\d{2}\] TODO:", log_content)


def test_todify_empty(hive_env, monkeypatch, capsys):
    """Empty slot bails without LLM call."""
    slot_file(2).write_text("")
    monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)

    with patch("keephive.commands.note.run_claude_pipe") as mock_llm:
        _note_extract_todos(2)
        mock_llm.assert_not_called()

    assert len(open_todos()) == 0


def test_todify_conversation(hive_env, monkeypatch):
    """Slack-style transcript extracts action items, skips context."""
    slot_file(3).write_text(
        "Tom: hey can you merge the PR after adoption goals sign-off?\n"
        "Me: sure, also I need to add LOESS smoothing to the graph\n"
        "Tom: sounds good, ping me when done\n"
        "Me: will do\n"
    )
    monkeypatch.delenv("HIVE_SKIP_LLM", raising=False)

    mock_response = NoteExtractResponse(
        items=["merge PR after adoption goals sign-off", "add LOESS smoothing to graph"]
    )
    monkeypatch.setattr("subprocess.run", _accept_all)

    with patch("keephive.commands.note.run_claude_pipe", return_value=mock_response):
        _note_extract_todos(3)

    todos = open_todos()
    texts = [t for _, _, t in todos]
    assert any("loess" in t.lower() for t in texts)
    assert any("merge" in t.lower() for t in texts)
    # Context lines should NOT be TODOs
    assert not any("sounds good" in t for t in texts)
    assert not any("will do" in t for t in texts)


def test_extract_structured_todo_section():
    """_extract_structured_items handles ## todo section."""
    content = "## notes\nsome context\n\n## todo\n- item one\n- item two\n\n## done\n- completed"
    items = _extract_structured_items(content)
    assert "item one" in items
    assert "item two" in items
    assert "completed" not in items


def test_extract_structured_checkboxes():
    """_extract_structured_items handles checkboxes, skips completed."""
    content = "- [ ] pending task\n- [x] done task\n- [ ] another task"
    items = _extract_structured_items(content)
    assert "pending task" in items
    assert "another task" in items
    assert "done task" not in items


def test_extract_structured_majority_bullets():
    """_extract_structured_items uses bullets when majority are bullets."""
    content = "Context line here.\n- do this\n- do that\n- and this too"
    items = _extract_structured_items(content)
    assert "do this" in items
    assert "do that" in items
    assert "and this too" in items


def test_bare_digit_dispatch(hive_env):
    """hive 4 routes to cmd_note_slot — same as hive n.4."""
    from unittest.mock import patch

    from keephive.cli import main

    called_with = []

    def fake_note_slot(slot, args):
        called_with.append((slot, args))

    with patch("keephive.commands.note.cmd_note_slot", fake_note_slot):
        main(["4"])

    assert called_with[0] == (4, [])


def test_bare_digit_with_subcommand(hive_env):
    """hive 4 todo routes to cmd_note_slot(4, ['todo'])."""
    from unittest.mock import patch

    from keephive.cli import main

    called_with = []

    def fake_note_slot(slot, args):
        called_with.append((slot, args))

    with patch("keephive.commands.note.cmd_note_slot", fake_note_slot):
        main(["4", "todo"])

    assert called_with[0] == (4, ["todo"])


def test_bare_zero_routes_to_slot_10(hive_env):
    """hive 0 routes to cmd_note_slot(10, []) — same as hive n.0."""
    from unittest.mock import patch

    from keephive.cli import main

    called_with = []

    def fake_note_slot(slot, args):
        called_with.append((slot, args))

    with patch("keephive.commands.note.cmd_note_slot", fake_note_slot):
        main(["0"])

    assert called_with[0] == (10, [])


def test_todify_long_item_filtered_at_extraction(hive_env, monkeypatch):
    """Long items (>120 chars) are silently filtered at extraction — never shown as candidates."""
    long = (
        "Fascinating! Even after excluding weekends, graphs have downward spikes. "
        "We should eliminate non-work days to get a cleaner signal from the data."
    )
    assert len(long) > 120
    slot_file(4).write_text(f"- pr approved\n- figma\n- {long}\n")
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")

    # Only 2 short items remain after filtering → editor opens, accept all
    monkeypatch.setattr("subprocess.run", _accept_all)

    _note_extract_todos(4)

    todos = open_todos()
    texts = [t for _, _, t in todos]
    assert len(texts) == 2
    assert any("pr approved" in t for t in texts)
    assert any("figma" in t for t in texts)
    # Long observation was filtered before ever being offered
    assert not any("Fascinating" in t for t in texts)


def test_todify_all_short_offers_add_all(hive_env, monkeypatch):
    """All short items: editor opens and all are accepted."""
    slot_file(3).write_text("- fix auth bug\n- update spec\n- add tests\n")
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")
    monkeypatch.setattr("subprocess.run", _accept_all)

    _note_extract_todos(3)

    todos = open_todos()
    assert len(todos) == 3


def test_todify_editor_buffer_review(hive_env, monkeypatch):
    """Editor deletes one '- ' line — only remaining items are added as TODOs."""
    slot_file(1).write_text("- fix auth bug\n- update spec\n- add tests\n")
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")

    def delete_first_todo(*args, **kwargs):
        path = Path(args[0][1])
        lines = path.read_text().splitlines()
        result = []
        deleted = False
        for line in lines:
            if not deleted and line.startswith("- "):
                deleted = True
                continue
            result.append(line)
        path.write_text("\n".join(result) + "\n")

    monkeypatch.setattr("subprocess.run", delete_first_todo)

    _note_extract_todos(1)

    todos = open_todos()
    texts = [t for _, _, t in todos]
    assert len(texts) == 2
    assert any("update spec" in t for t in texts)
    assert any("add tests" in t for t in texts)


def test_todify_editor_all_deleted(hive_env, monkeypatch):
    """Editor removes all '- ' lines — no TODOs created."""
    slot_file(2).write_text("- task one\n- task two\n")
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")

    def clear_todos(*args, **kwargs):
        path = Path(args[0][1])
        # Keep non-'- ' lines (instruction, blank lines) but strip all todo markers
        lines = [
            ln for ln in path.read_text().splitlines() if not ln.startswith("- ")
        ]
        path.write_text("\n".join(lines) + "\n")

    monkeypatch.setattr("subprocess.run", clear_todos)

    _note_extract_todos(2)

    assert len(open_todos()) == 0


def test_todify_no_save_cancels(hive_env, monkeypatch):
    """Exiting editor without saving (mtime unchanged) cancels — no TODOs created."""
    slot_file(1).write_text("- task one\n- task two\n")
    monkeypatch.setenv("HIVE_SKIP_LLM", "1")

    # No-op: editor does nothing, file mtime does not change
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)

    _note_extract_todos(1)

    assert len(open_todos()) == 0


def test_extract_structured_includes_plain_lines():
    """Plain (non-bullet) lines under ## todo section are included in extraction."""
    content = (
        "## todo\n"
        "adoption goals api testing\n"
        "prompt library start\n"
        "- pr approved\n"
        "- figma\n"
    )
    items = _extract_structured_items(content)
    assert "adoption goals api testing" in items
    assert "prompt library start" in items
    assert "pr approved" in items
    assert "figma" in items


def test_build_todo_buffer_marks_candidates(hive_env):
    """Candidates are pre-marked with '- ', non-candidate bullets stripped to plain text."""
    content = "## todo\n- fix auth bug\n- update spec\n\n## done\n- old task\n"
    candidates = {"fix auth bug", "update spec"}
    buf = _build_todo_buffer(content, candidates)
    lines = buf.splitlines()

    # Candidates are pre-marked
    assert "- fix auth bug" in lines
    assert "- update spec" in lines
    # Non-candidate bullet (done section) is stripped to plain text
    assert "old task" in lines
    assert "- old task" not in lines
    # Instruction line is present
    assert any('Lines starting with "- "' in ln for ln in lines)


def test_build_todo_buffer_unmatched(hive_env):
    """LLM-extracted items not found in note are appended after '---' separator."""
    content = "Just some prose about the project."
    candidates = {"fix auth bug", "add tests"}
    buf = _build_todo_buffer(content, candidates)

    assert "---" in buf
    assert "- fix auth bug" in buf
    assert "- add tests" in buf
