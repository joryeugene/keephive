"""Tests for hive swarm: _parse_tasks, _build_prompt, cmd_swarm."""

from __future__ import annotations

from pathlib import Path

import pytest

from keephive.commands.swarm import _build_prompt, _parse_tasks, cmd_swarm


class TestParseTasksTagged:
    def test_tagged_basic(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [auth] Do thing\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 1
        assert tasks[0]["tag"] == "auth"
        assert tasks[0]["description"] == "Do thing"
        assert tasks[0]["hint"] == ""

    def test_tagged_with_hint(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [auth] Do thing → src/auth/\n")
        tasks = _parse_tasks(f)
        assert tasks[0]["tag"] == "auth"
        assert tasks[0]["hint"] == "src/auth/"

    def test_multiple_same_tag(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [api] Task one\n- [api] Task two\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 2
        assert tasks[0]["tag"] == "api"
        assert tasks[1]["tag"] == "api"


class TestParseTasksBare:
    def test_bare_untagged(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- Do the thing\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 1
        assert tasks[0]["tag"] == "worker-1"
        assert tasks[0]["description"] == "Do the thing"

    def test_bare_sequential_numbering(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- Task one\n- Task two\n- Task three\n")
        tasks = _parse_tasks(f)
        assert [t["tag"] for t in tasks] == ["worker-1", "worker-2", "worker-3"]

    def test_bare_with_hint(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- Do thing → src/\n")
        tasks = _parse_tasks(f)
        assert tasks[0]["hint"] == "src/"


class TestParseTasksCheckbox:
    def test_checkbox_unchecked_is_worker(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [ ] Do thing\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 1
        assert tasks[0]["tag"] == "worker-1"
        assert tasks[0]["description"] == "Do thing"

    def test_checkbox_unchecked_with_hint(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [ ] Do thing → src/\n")
        tasks = _parse_tasks(f)
        assert tasks[0]["hint"] == "src/"
        assert tasks[0]["tag"] == "worker-1"

    def test_checkbox_checked_lowercase_skipped(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [x] Done\n")
        tasks = _parse_tasks(f)
        assert tasks == []

    def test_checkbox_checked_uppercase_skipped(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [X] Done\n")
        tasks = _parse_tasks(f)
        assert tasks == []

    def test_all_checkboxes_no_categories(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [ ] Task one\n- [ ] Task two\n- [ ] Task three\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 3
        assert [t["tag"] for t in tasks] == ["worker-1", "worker-2", "worker-3"]

    def test_mixed_checked_unchecked(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "- [x] Already done\n"
            "- [ ] Still todo\n"
            "- [X] Also done\n"
            "- [ ] Another todo\n"
        )
        tasks = _parse_tasks(f)
        assert len(tasks) == 2
        assert tasks[0]["tag"] == "worker-1"
        assert tasks[0]["description"] == "Still todo"
        assert tasks[1]["tag"] == "worker-2"
        assert tasks[1]["description"] == "Another todo"


class TestParseTasksMixed:
    def test_tagged_and_checkbox_mixed(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("- [auth] Auth task\n- [ ] Unchecked task\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 2
        assert tasks[0]["tag"] == "auth"
        assert tasks[0]["description"] == "Auth task"
        assert tasks[1]["tag"] == "worker-1"
        assert tasks[1]["description"] == "Unchecked task"


class TestParseTasksEdgeCases:
    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("")
        assert _parse_tasks(f) == []

    def test_comment_lines_skipped(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("# Header\n# Another comment\n- [tag] Real task\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 1
        assert tasks[0]["tag"] == "tag"

    def test_blank_lines_skipped(self, tmp_path: Path):
        f = tmp_path / "tasks.md"
        f.write_text("\n- Task one\n\n- Task two\n\n")
        tasks = _parse_tasks(f)
        assert len(tasks) == 2
        assert tasks[0]["tag"] == "worker-1"
        assert tasks[1]["tag"] == "worker-2"


class TestBuildPrompt:
    def test_worker_tasks_in_prompt(self):
        tasks = [
            {"tag": "worker-1", "description": "Task one", "hint": ""},
            {"tag": "worker-2", "description": "Task two", "hint": ""},
            {"tag": "worker-3", "description": "Task three", "hint": ""},
        ]
        prompt = _build_prompt(tasks, "test-team")
        assert "worker-1" in prompt
        assert "worker-2" in prompt
        assert "worker-3" in prompt

    def test_prompt_team_name(self):
        tasks = [{"tag": "api", "description": "Build API", "hint": ""}]
        prompt = _build_prompt(tasks, "my-team-name")
        assert "my-team-name" in prompt.splitlines()[0]


class TestCmdSwarm:
    def test_no_args_prints_help(self, hive_env, capsys):
        cmd_swarm([])
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_dry_run_checkbox_file(self, hive_env, tmp_path, capsys):
        f = tmp_path / "tasks.md"
        f.write_text("- [ ] Do thing\n- [x] Done\n- [ ] Another\n")
        cmd_swarm([str(f), "--dry-run"])
        out = capsys.readouterr().out
        assert "worker-1" in out
        assert "worker-2" in out
        # checked item must not appear in the prompt
        assert "Done" not in out

    def test_missing_file_error(self, hive_env, tmp_path, capsys):
        missing = str(tmp_path / "nonexistent.md")
        cmd_swarm([missing])
        out = capsys.readouterr().out
        assert "Error" in out

    def test_empty_file_warns(self, hive_env, tmp_path, capsys):
        f = tmp_path / "tasks.md"
        f.write_text("# Only comments here\n# Nothing else\n")
        cmd_swarm([str(f)])
        out = capsys.readouterr().out
        assert "No tasks" in out
