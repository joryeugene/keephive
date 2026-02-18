"""Tests for knowledge guides and prompts (commands/knowledge.py)."""

from __future__ import annotations

import pytest

from keephive.cli import main


# ---- _resolve_file ----

class TestResolveFile:
    def test_exact_match(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "my-guide.md").write_text("# My Guide\n")

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("my-guide", gd)
        assert result is not None
        assert result.stem == "my-guide"

    def test_exact_match_with_extension(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "my-guide.md").write_text("# My Guide\n")

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("my-guide.md", gd)
        assert result is not None
        assert result.stem == "my-guide"

    def test_prefix_match_single(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "code-review.md").write_text("# Code Review\n")

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("code", gd)
        assert result is not None
        assert result.stem == "code-review"

    def test_prefix_match_ambiguous(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "code-review.md").write_text("# Code Review\n")
        (gd / "code-hygiene.md").write_text("# Code Hygiene\n")

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("code", gd)
        # Ambiguous: returns None
        assert result is None

    def test_substring_match_single(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "my-review-guide.md").write_text("# Review\n")

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("review", gd)
        assert result is not None
        assert result.stem == "my-review-guide"

    def test_substring_match_ambiguous(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "my-review-guide.md").write_text("# Review\n")
        (gd / "code-review.md").write_text("# Code Review\n")

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("review", gd)
        # Ambiguous substring: returns None
        assert result is None

    def test_no_match(self, hive_env):
        gd = hive_env / "knowledge" / "guides"

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("nonexistent", gd)
        assert result is None

    def test_multi_directory_search(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        pd = hive_env / "knowledge" / "prompts"
        (pd / "commit-draft.md").write_text("# Commit\n")

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("commit-draft", gd, pd)
        assert result is not None
        assert result.stem == "commit-draft"

    def test_empty_dir(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        # guides dir exists but has no .md files (only bundled ones from fixture)
        for f in gd.glob("*.md"):
            f.unlink()

        from keephive.commands.knowledge import _resolve_file
        result = _resolve_file("anything", gd)
        assert result is None


# ---- _knowledge_view ----

class TestKnowledgeView:
    def test_view_exact_name(self, hive_env, capsys):
        gd = hive_env / "knowledge" / "guides"
        (gd / "test-guide.md").write_text("# Test Guide\n\nContent here.\n")

        main(["k", "test-guide"])
        out = capsys.readouterr().out
        assert "Test Guide" in out

    def test_view_prefix_match(self, hive_env, capsys):
        gd = hive_env / "knowledge" / "guides"
        (gd / "unique-name.md").write_text("# Unique\n\nHello.\n")

        main(["k", "uni"])
        out = capsys.readouterr().out
        assert "Unique" in out

    def test_view_ambiguous_shows_candidates(self, hive_env, capsys):
        gd = hive_env / "knowledge" / "guides"
        (gd / "test-a.md").write_text("# A\n")
        (gd / "test-b.md").write_text("# B\n")

        main(["k", "test"])
        out = capsys.readouterr().out
        assert "Ambiguous" in out or "test-a" in out

    def test_view_not_found_shows_available(self, hive_env, capsys):
        main(["k", "nonexistent-xyz"])
        out = capsys.readouterr().out
        assert "not found" in out.lower()


# ---- _knowledge_list ----

class TestKnowledgeList:
    def test_list_shows_guides(self, hive_env, capsys):
        gd = hive_env / "knowledge" / "guides"
        (gd / "my-guide.md").write_text("---\ntags: [python]\n---\n# My Guide\n")

        main(["k"])
        out = capsys.readouterr().out
        assert "my-guide" in out
        assert "python" in out

    def test_list_shows_guides_without_tags(self, hive_env, capsys):
        gd = hive_env / "knowledge" / "guides"
        (gd / "plain.md").write_text("# Plain Guide\n\nNo front matter.\n")

        main(["k"])
        out = capsys.readouterr().out
        assert "plain" in out

    def test_list_empty_shows_none(self, hive_env, capsys):
        # Remove all guides
        gd = hive_env / "knowledge" / "guides"
        for f in gd.glob("*.md"):
            f.unlink()

        main(["k"])
        out = capsys.readouterr().out
        assert "none yet" in out.lower()

    def test_list_shows_prompts_section(self, hive_env, capsys):
        pd = hive_env / "knowledge" / "prompts"
        (pd / "my-prompt.md").write_text("# My Prompt\n")

        main(["k"])
        out = capsys.readouterr().out
        assert "Prompts" in out
        assert "my-prompt" in out


# ---- _prompt_list ----

class TestPromptList:
    def test_prompt_list(self, hive_env, capsys):
        pd = hive_env / "knowledge" / "prompts"
        (pd / "review.md").write_text("# Review\n")

        main(["p"])
        out = capsys.readouterr().out
        assert "review" in out

    def test_prompt_list_empty(self, hive_env, capsys):
        pd = hive_env / "knowledge" / "prompts"
        for f in pd.glob("*.md"):
            f.unlink()

        main(["p"])
        out = capsys.readouterr().out
        assert "none yet" in out.lower()


# ---- _knowledge_rm ----

class TestKnowledgeRm:
    def test_rm_removes_file(self, hive_env, capsys):
        gd = hive_env / "knowledge" / "guides"
        (gd / "to-remove.md").write_text("# Remove Me\n")

        main(["k", "rm", "to-remove"])
        out = capsys.readouterr().out
        assert "Removed" in out
        assert not (gd / "to-remove.md").exists()

    def test_rm_not_found(self, hive_env, capsys):
        main(["k", "rm", "no-such-guide"])
        out = capsys.readouterr().out
        assert "Not found" in out
