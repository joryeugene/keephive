"""Tests for garbage collection (commands/gc.py)."""

from __future__ import annotations

import json
from datetime import date, timedelta

from keephive.cli import main

# ---- _rebuild_index ----


class TestRebuildIndex:
    def test_produces_valid_json(self, hive_env):
        from keephive.commands.gc import _rebuild_index
        from keephive.storage import index_file

        _rebuild_index()

        idx = index_file()
        assert idx.exists()
        data = json.loads(idx.read_text())
        assert "rebuilt" in data
        assert "fact_count" in data
        assert "guides" in data
        assert "prompts" in data
        assert "files" in data

    def test_counts_facts(self, hive_env):
        from keephive.commands.gc import _rebuild_index
        from keephive.storage import index_file

        _rebuild_index()
        data = json.loads(index_file().read_text())
        # memory.md has 3 lines starting with "- "
        assert data["fact_count"] >= 3

    def test_counts_guides(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "extra.md").write_text("# Extra\n")

        from keephive.commands.gc import _rebuild_index
        from keephive.storage import index_file

        _rebuild_index()

        data = json.loads(index_file().read_text())
        assert data["guides"] >= 1

    def test_counts_prompts(self, hive_env):
        pd = hive_env / "knowledge" / "prompts"
        (pd / "test-prompt.md").write_text("# Prompt\n")

        from keephive.commands.gc import _rebuild_index
        from keephive.storage import index_file

        _rebuild_index()

        data = json.loads(index_file().read_text())
        assert data["prompts"] >= 1

    def test_counts_daily_files(self, hive_env):
        dd = hive_env / "daily"
        (dd / "2026-02-17.md").write_text("# Daily\n- entry\n")

        from keephive.commands.gc import _rebuild_index
        from keephive.storage import index_file

        _rebuild_index()

        data = json.loads(index_file().read_text())
        assert data["files"]["daily"] >= 1

    def test_empty_dirs(self, hive_env):
        """No crash on empty directories."""
        # Remove all content
        for f in (hive_env / "working").glob("*.md"):
            f.unlink()
        for f in (hive_env / "knowledge" / "guides").glob("*.md"):
            f.unlink()

        from keephive.commands.gc import _rebuild_index
        from keephive.storage import index_file

        _rebuild_index()

        data = json.loads(index_file().read_text())
        assert data["fact_count"] == 0


# ---- cmd_gc ----


class TestCmdGc:
    def test_archives_old_logs(self, hive_env, capsys):
        dd = hive_env / "daily"
        old_date = (date.today() - timedelta(days=60)).isoformat()
        (dd / f"{old_date}.md").write_text(f"# Daily Log: {old_date}\n")

        main(["gc"])
        out = capsys.readouterr().out
        assert "Archived" in out or "archived" in out.lower()

        # File moved to archive/
        assert not (dd / f"{old_date}.md").exists()
        assert (hive_env / "archive" / f"{old_date}.md").exists()

    def test_keeps_recent_logs(self, hive_env, capsys):
        dd = hive_env / "daily"
        recent = date.today().isoformat()
        (dd / f"{recent}.md").write_text(f"# Daily Log: {recent}\n")

        main(["gc"])
        capsys.readouterr()

        # Recent file stays
        assert (dd / f"{recent}.md").exists()

    def test_dry_run(self, hive_env, capsys):
        dd = hive_env / "daily"
        old_date = (date.today() - timedelta(days=60)).isoformat()
        (dd / f"{old_date}.md").write_text(f"# Daily Log: {old_date}\n")

        main(["gc", "--dry-run"])
        out = capsys.readouterr().out
        assert "Would" in out

        # File NOT moved
        assert (dd / f"{old_date}.md").exists()

    def test_cleans_reminded_markers(self, hive_env, capsys):
        hd = hive_env
        (hd / ".reminded-abc123").write_text("")

        main(["gc"])
        capsys.readouterr()
        assert not (hd / ".reminded-abc123").exists()

    def test_cleans_bak_files(self, hive_env, capsys):
        wd = hive_env / "working"
        (wd / "memory.md.bak").write_text("backup\n")

        main(["gc"])
        capsys.readouterr()
        assert not (wd / "memory.md.bak").exists()

    def test_cleans_recall_tmp(self, hive_env, capsys):
        hd = hive_env
        (hd / ".recall_tmp_test").write_text("tmp\n")

        main(["gc"])
        capsys.readouterr()
        assert not (hd / ".recall_tmp_test").exists()

    def test_nothing_to_archive(self, hive_env, capsys):
        main(["gc"])
        out = capsys.readouterr().out
        assert "Nothing to archive" in out

    def test_index_rebuilt(self, hive_env, capsys):
        from keephive.storage import index_file

        main(["gc"])
        out = capsys.readouterr().out
        assert "Index rebuilt" in out
        assert index_file().exists()
