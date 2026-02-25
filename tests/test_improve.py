"""Tests for hive improve — KingBee's self-improvement proposal queue.

Covers:
- pending_improvements_file() path
- read/write/append pending improvements (atomic, append-only semantics)
- dismissed improvements (rolling 100-cap, compact storage)
- cmd_improve() list + review output
- _apply_improvement() for skill, task, rule, and edit types
- _apply_skill_edit() LLM success + failure fallback paths
- KingBee status line in hive stats output
"""

from __future__ import annotations

import json
from datetime import datetime


class TestPendingImprovementsFilePath:
    """pending_improvements_file() lives inside hive_dir()."""

    def test_path_in_hive_dir(self, hive_env):
        """pending_improvements_file() is hive_dir()/.pending-improvements.json."""
        from keephive.storage import hive_dir, pending_improvements_file

        assert pending_improvements_file() == hive_dir() / ".pending-improvements.json"


class TestReadPendingImprovements:
    """read_pending_improvements() returns list or [] on missing/corrupt."""

    def test_returns_empty_when_missing(self, hive_env):
        """Returns [] when .pending-improvements.json does not exist."""
        from keephive.storage import pending_improvements_file, read_pending_improvements

        assert not pending_improvements_file().exists()
        assert read_pending_improvements() == []

    def test_returns_list_when_present(self, hive_env):
        """Returns the stored list when file exists with valid JSON."""
        from keephive.storage import pending_improvements_file, read_pending_improvements

        items = [{"type": "skill", "name": "fast-git", "rationale": "saves time"}]
        pending_improvements_file().write_text(json.dumps(items))
        result = read_pending_improvements()
        assert len(result) == 1
        assert result[0]["name"] == "fast-git"

    def test_returns_empty_on_corrupt_json(self, hive_env):
        """Returns [] when file exists but contains invalid JSON."""
        from keephive.storage import pending_improvements_file, read_pending_improvements

        pending_improvements_file().write_text("{{{broken")
        assert read_pending_improvements() == []


class TestWritePendingImprovements:
    """write_pending_improvements() overwrites atomically."""

    def test_write_and_read_roundtrip(self, hive_env):
        """write_pending_improvements() persists; read_pending_improvements() recovers exactly."""
        from keephive.storage import read_pending_improvements, write_pending_improvements

        items = [
            {"type": "skill", "name": "standup-context", "rationale": "saves time"},
            {"type": "rule", "rule": "Capture rationale before session ends"},
        ]
        write_pending_improvements(items)
        result = read_pending_improvements()
        assert len(result) == 2
        assert result[0]["name"] == "standup-context"

    def test_write_is_atomic(self, hive_env):
        """.tmp file is cleaned up after write — no partial state left on disk."""
        from keephive.storage import pending_improvements_file, write_pending_improvements

        write_pending_improvements([{"type": "skill", "name": "x"}])
        tmp = pending_improvements_file().with_suffix(".tmp")
        assert not tmp.exists()
        assert pending_improvements_file().exists()


class TestAppendPendingImprovements:
    """append_pending_improvements() adds to the queue without replacing."""

    def test_appends_to_existing_items(self, hive_env):
        """Appending to a non-empty queue adds new items without removing old ones.

        Bug caught: overwrite semantics accidentally clearing user's pending work.
        """
        from keephive.storage import (
            append_pending_improvements,
            read_pending_improvements,
            write_pending_improvements,
        )

        write_pending_improvements([{"type": "skill", "name": "existing-skill"}])
        append_pending_improvements([{"type": "rule", "rule": "New rule text"}])

        result = read_pending_improvements()
        assert len(result) == 2
        assert result[0]["name"] == "existing-skill"  # original preserved
        assert result[1]["rule"] == "New rule text"  # new item appended

    def test_sets_proposed_at_timestamp(self, hive_env):
        """Each appended item gets a proposed_at ISO timestamp.

        Bug caught: items missing proposed_at → age display and stale detection broken.
        """
        from keephive.storage import append_pending_improvements, read_pending_improvements

        before = datetime.now()
        append_pending_improvements([{"type": "skill", "name": "timed-skill"}])
        after = datetime.now()

        result = read_pending_improvements()
        assert len(result) == 1
        proposed_at = datetime.fromisoformat(result[0]["proposed_at"])
        assert before <= proposed_at <= after

    def test_appends_to_empty_queue(self, hive_env):
        """append_pending_improvements() works on an empty queue (no existing file)."""
        from keephive.storage import append_pending_improvements, read_pending_improvements

        append_pending_improvements([{"type": "task", "name": "new-task"}])
        result = read_pending_improvements()
        assert len(result) == 1
        assert result[0]["name"] == "new-task"


class TestCmdImproveList:
    """cmd_improve(['list']) displays queue state correctly."""

    def test_shows_no_pending_when_empty(self, hive_env, capsys):
        """'hive improve list' prints a no-pending message when queue is empty."""
        from keephive.commands.improve import cmd_improve

        cmd_improve(["list"])
        out = capsys.readouterr().out
        assert "No pending" in out or "no pending" in out.lower()

    def test_shows_count_and_type_when_items_present(self, hive_env, capsys):
        """'hive improve list' shows item count and type for each queued proposal."""
        from keephive.commands.improve import cmd_improve
        from keephive.storage import append_pending_improvements

        append_pending_improvements(
            [
                {"type": "skill", "name": "fast-git-summary", "rationale": "saves 2 min/day"},
                {"type": "rule", "rule": "Capture rationale", "rationale": "missed 3 sessions"},
            ]
        )
        cmd_improve(["list"])
        out = capsys.readouterr().out
        assert "SKILL" in out.upper() or "skill" in out.lower()
        assert "RULE" in out.upper() or "rule" in out.lower()
        assert "fast-git-summary" in out


class TestApplyImprovement:
    """_apply_improvement() installs approved proposals to the right destinations."""

    def test_skill_writes_guide_to_guides_dir(self, hive_env):
        """Accepting a skill proposal writes a .md file to guides_dir().

        Bug caught: wrong path, wrong extension, file not created at all.
        """
        from keephive.commands.improve import _apply_improvement
        from keephive.storage import guides_dir

        item = {
            "type": "skill",
            "name": "fast-standup",
            "rationale": "Used 11x",
            "content": "# Fast Standup\nPull yesterday's git activity automatically.",
        }
        _apply_improvement(item)

        skill_path = guides_dir() / "fast-standup.md"
        assert skill_path.exists(), f"Guide not found at {skill_path}"
        assert "Pull yesterday" in skill_path.read_text()

    def test_task_adds_to_daemon_json(self, hive_env):
        """Accepting a task proposal adds it to daemon.json.

        Bug caught: task not written, wrong key structure, existing tasks overwritten.
        """
        from keephive.commands.improve import _apply_improvement
        from keephive.storage import read_daemon_config

        item = {
            "type": "task",
            "name": "weekly-git-activity",
            "rationale": "Run every Monday",
            "config": {"enabled": False, "day": "monday", "time": "08:30"},
        }
        _apply_improvement(item)

        config = read_daemon_config()
        assert "weekly-git-activity" in config.get("tasks", {}), "Task not found in daemon.json"
        assert config["tasks"]["weekly-git-activity"]["day"] == "monday"

    def test_rule_queues_to_pending_rules(self, hive_env):
        """Accepting a rule proposal appends it to .pending-rules.md.

        Bug caught: rule written to wrong file, or written directly to rules.md
        (should go through review flow).
        """
        from keephive.commands.improve import _apply_improvement
        from keephive.storage import hive_dir

        item = {
            "type": "rule",
            "rule": "Capture rationale for architecture decisions before session ends.",
            "rationale": "3 sessions missed this",
        }
        _apply_improvement(item)

        pending_rules = hive_dir() / ".pending-rules.md"
        assert pending_rules.exists(), ".pending-rules.md not created"
        content = pending_rules.read_text()
        assert "Capture rationale" in content


class TestApplySkillEdit:
    """_apply_skill_edit() merges proposed changes into an existing guide via LLM."""

    def test_llm_success_writes_merged_content(self, hive_env, monkeypatch):
        """LLM returns merged content → file is updated with it.

        Bug caught: edit overwrites with raw delta string instead of merged guide.
        """
        from keephive.commands.improve import _apply_skill_edit
        from keephive.storage import guides_dir

        gd = guides_dir()
        gd.mkdir(parents=True, exist_ok=True)
        skill_path = gd / "my-guide.md"
        skill_path.write_text("# My Guide\n\nOriginal content here.\n")

        class FakeResult:
            content = "# My Guide\n\nOriginal content here.\n\n## New Section\n\nAdded by LLM.\n"

        monkeypatch.setattr("keephive.claude.run_claude_pipe", lambda *a, **kw: FakeResult())

        result = _apply_skill_edit(skill_path, "my-guide", "Add a New Section about X")

        assert result is True
        text = skill_path.read_text()
        assert "Original content here" in text
        assert "Added by LLM" in text

    def test_llm_failure_returns_false(self, hive_env, monkeypatch):
        """ClaudePipeError from LLM → returns False, file unchanged.

        Bug caught: exception propagates instead of returning False for fallback.
        """
        from keephive.claude import ClaudePipeError
        from keephive.commands.improve import _apply_skill_edit
        from keephive.storage import guides_dir

        gd = guides_dir()
        gd.mkdir(parents=True, exist_ok=True)
        skill_path = gd / "my-guide.md"
        original = "# My Guide\n\nOriginal content here.\n"
        skill_path.write_text(original)

        def raise_error(*a, **kw):
            raise ClaudePipeError("LLM unavailable")

        monkeypatch.setattr("keephive.claude.run_claude_pipe", raise_error)

        result = _apply_skill_edit(skill_path, "my-guide", "Add a New Section")

        assert result is False
        assert skill_path.read_text() == original  # file untouched

    def test_llm_empty_response_returns_false(self, hive_env, monkeypatch):
        """LLM returns empty content → returns False without overwriting.

        Bug caught: empty string overwrites existing guide with blank file.
        """
        from keephive.commands.improve import _apply_skill_edit
        from keephive.storage import guides_dir

        gd = guides_dir()
        gd.mkdir(parents=True, exist_ok=True)
        skill_path = gd / "my-guide.md"
        original = "# My Guide\n\nOriginal content here.\n"
        skill_path.write_text(original)

        class EmptyResult:
            content = "   "

        monkeypatch.setattr("keephive.claude.run_claude_pipe", lambda *a, **kw: EmptyResult())

        result = _apply_skill_edit(skill_path, "my-guide", "Add a New Section")

        assert result is False
        assert skill_path.read_text() == original

    def test_edit_action_calls_apply_skill_edit_not_direct_write(self, hive_env, monkeypatch):
        """edit action on existing skill routes through _apply_skill_edit, not write_text.

        Bug caught: the corruption — edit was doing skill_path.write_text(changes) directly,
        overwriting the guide with a delta string.
        """
        from keephive.commands.improve import _apply_improvement
        from keephive.storage import guides_dir

        gd = guides_dir()
        gd.mkdir(parents=True, exist_ok=True)
        skill_path = gd / "keephive-guide.md"
        skill_path.write_text("# keephive Guide\n\nFull existing content.\n")

        apply_calls = []

        def fake_apply(path, target, changes):
            apply_calls.append((str(path), target, changes))
            return True

        monkeypatch.setattr("keephive.commands.improve._apply_skill_edit", fake_apply)

        item = {
            "type": "edit",
            "action": "edit",
            "target_type": "skill",
            "name": "keephive-guide",
            "changes": "Add to Core commands table: | `hive loop` | `loop` | ...",
            "rationale": "loop command missing from guide",
        }
        _apply_improvement(item)

        assert len(apply_calls) == 1, "Expected exactly one _apply_skill_edit call"
        assert apply_calls[0][1] == "keephive-guide"
        # Verify original guide was NOT overwritten with the raw delta
        assert skill_path.read_text() == "# keephive Guide\n\nFull existing content.\n"

    def test_merge_action_does_direct_write(self, hive_env, monkeypatch):
        """merge action bypasses _apply_skill_edit and does a direct write.

        For merges, the LLM has already produced the combined full content.
        """
        from keephive.commands.improve import _apply_improvement
        from keephive.storage import guides_dir

        gd = guides_dir()
        gd.mkdir(parents=True, exist_ok=True)
        skill_path = gd / "guide-a.md"
        skill_path.write_text("# Guide A\n\nOriginal.\n")

        apply_calls = []
        monkeypatch.setattr(
            "keephive.commands.improve._apply_skill_edit",
            lambda *a, **kw: apply_calls.append(a) or True,
        )

        merged_content = "# Guide A+B\n\nCombined content from both guides.\n"
        item = {
            "type": "edit",
            "action": "merge",
            "target_type": "skill",
            "name": "guide-a",
            "changes": merged_content,
            "rationale": "guide-b covers same ground",
        }
        _apply_improvement(item)

        assert len(apply_calls) == 0, "merge should not call _apply_skill_edit"
        assert skill_path.read_text() == merged_content


class TestDismissedImprovements:
    """append_dismissed_improvements() records dismissals with a rolling cap."""

    def test_records_dismissed_with_timestamp(self, hive_env):
        """Each dismissed item gets a dismissed_at timestamp in the record.

        Bug caught: dismissed items have no timestamp → can't detect how old rejections are.
        """
        from keephive.storage import append_dismissed_improvements, read_dismissed_improvements

        before = datetime.now()
        append_dismissed_improvements(
            [{"type": "skill", "name": "some-skill", "rationale": "nope"}]
        )
        after = datetime.now()

        records = read_dismissed_improvements()
        assert len(records) == 1
        dismissed_at = datetime.fromisoformat(records[0]["dismissed_at"])
        assert before <= dismissed_at <= after
        assert records[0]["type"] == "skill"
        assert records[0]["name"] == "some-skill"

    def test_rolling_100_item_cap(self, hive_env):
        """Dismissed list is capped at 100 items — oldest drop off.

        Bug caught: unbounded growth meaning KingBee 'forgets' early dismissals and
        re-proposes them again, annoying the user.
        """
        from keephive.storage import append_dismissed_improvements, read_dismissed_improvements

        # Add 105 items in batches
        for batch in range(21):
            items = [{"type": "skill", "name": f"skill-{batch * 5 + i}"} for i in range(5)]
            append_dismissed_improvements(items)

        records = read_dismissed_improvements()
        assert len(records) == 100, f"Expected 100 records (rolling cap), got {len(records)}"
        # Most recent items should be preserved (last 100 out of 105)
        names = [r["name"] for r in records]
        assert "skill-104" in names  # most recent batch preserved
        assert "skill-0" not in names  # oldest item dropped off


class TestKingBeeStatusLine:
    """hive stats output includes a KingBee status line when SOUL.md exists."""

    def test_kingbee_line_shown_when_soul_exists(self, hive_env, capsys):
        """_display_full() prints '🐝 KingBee' when SOUL.md is present.

        Bug caught: KingBee status line silently absent, user doesn't know identity
        was updated.
        """
        from keephive.commands.stats import _display_full
        from keephive.storage import soul_file

        # Create SOUL.md so the status line triggers
        soul_file().write_text("# SOUL.md\n\n## Summary\nI am KingBee.\n")

        # Provide minimal non-empty days so _display_full() doesn't early-return
        _display_full({"days": {"2026-02-22": {}}})
        out = capsys.readouterr().out
        assert "KingBee" in out, "KingBee status line missing when SOUL.md exists"

    def test_kingbee_line_shows_improvement_count(self, hive_env, capsys):
        """KingBee status line shows pending improvement count when > 0."""
        from keephive.commands.stats import _display_full
        from keephive.storage import append_pending_improvements, soul_file

        soul_file().write_text("# SOUL.md\n\n## Summary\nI am KingBee.\n")
        append_pending_improvements(
            [
                {"type": "skill", "name": "x"},
                {"type": "rule", "rule": "y"},
            ]
        )

        # Provide minimal non-empty days so _display_full() doesn't early-return
        _display_full({"days": {"2026-02-22": {}}})
        out = capsys.readouterr().out
        assert "improvement" in out.lower(), (
            "Pending improvement count should appear in stats output"
        )

    def test_kingbee_line_absent_when_no_soul(self, hive_env, capsys):
        """_display_full() does NOT print KingBee line when SOUL.md is absent."""
        from keephive.commands.stats import _display_full
        from keephive.storage import soul_file

        assert not soul_file().exists()
        # Provide minimal non-empty days so _display_full() doesn't early-return
        _display_full({"days": {"2026-02-22": {}}})
        out = capsys.readouterr().out
        assert "KingBee" not in out
