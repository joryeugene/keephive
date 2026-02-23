"""Tests for SOUL.md — KingBee's persistent identity.

Covers: soul_file(), read_soul(), read_soul_summary(), and sessionstart injection.
"""

from __future__ import annotations


class TestSoulFilePath:
    """soul_file() must live inside hive_dir()."""

    def test_soul_file_inside_hive_dir(self, hive_env):
        """soul_file() returns a path under the active hive_dir."""
        from keephive.storage import hive_dir, soul_file

        assert soul_file().parent == hive_dir()
        assert soul_file().name == "SOUL.md"


class TestReadSoul:
    """read_soul() returns full content or empty string."""

    def test_returns_empty_when_missing(self, hive_env):
        """read_soul() returns '' when SOUL.md does not exist."""
        from keephive.storage import read_soul, soul_file

        assert not soul_file().exists()
        assert read_soul() == ""

    def test_returns_content_when_present(self, hive_env):
        """read_soul() returns full file content when SOUL.md exists."""
        from keephive.storage import read_soul, soul_file

        expected = "# SOUL.md\n\n## Summary\nI am KingBee.\n"
        soul_file().write_text(expected)
        assert read_soul() == expected


class TestReadSoulSummary:
    """read_soul_summary() extracts and sanitizes the ## Summary section."""

    def test_returns_empty_when_file_missing(self, hive_env):
        """Returns '' when SOUL.md does not exist."""
        from keephive.storage import read_soul_summary, soul_file

        assert not soul_file().exists()
        assert read_soul_summary() == ""

    def test_returns_empty_when_no_summary_section(self, hive_env):
        """Returns '' when SOUL.md exists but has no ## Summary section."""
        from keephive.storage import read_soul_summary, soul_file

        soul_file().write_text("# SOUL.md\n\n## Personality\nDirectness: 85%\n")
        assert read_soul_summary() == ""

    def test_extracts_summary_section(self, hive_env):
        """Returns the ## Summary section text, prefixed with '## '."""
        from keephive.storage import read_soul_summary, soul_file

        soul_file().write_text(
            "# SOUL.md\n\n## Summary\nI am KingBee. I act before I'm asked.\n\n## Personality\nDirectness: 85%\n"
        )
        result = read_soul_summary()
        assert result.startswith("## ")
        assert "KingBee" in result
        assert "I act before I'm asked" in result
        # Must NOT include other sections
        assert "Personality" not in result
        assert "Directness" not in result

    def test_strips_html_template_comments(self, hive_env):
        """HTML comments (<!-- ... -->) are removed before returning."""
        from keephive.storage import read_soul_summary, soul_file

        soul_file().write_text(
            "# SOUL.md\n\n## Summary\n<!-- Keep under 300 tokens. -->\nI am KingBee.\n"
        )
        result = read_soul_summary()
        assert "<!--" not in result
        assert "-->" not in result
        assert "Keep under 300 tokens" not in result
        assert "KingBee" in result

    def test_summary_section_case_insensitive(self, hive_env):
        """'## summary' (lowercase) is also recognized."""
        from keephive.storage import read_soul_summary, soul_file

        soul_file().write_text("# SOUL\n\n## summary\nLowercase header.\n")
        result = read_soul_summary()
        assert "Lowercase header" in result


class TestSessionstartSoulInjection:
    """sessionstart injects soul summary as ## Agent Identity when present."""

    def test_injects_agent_identity_when_summary_exists(self, hive_env):
        """build_context() includes ## Agent Identity when SOUL.md has a ## Summary."""
        from keephive.hooks.sessionstart import build_context
        from keephive.storage import soul_file

        soul_file().write_text(
            "# SOUL.md\n\n## Summary\nI am KingBee. Verification over faith.\n\n## Personality\nDirectness: 85%\n"
        )
        ctx = build_context("/test/project", "project")
        assert "## Agent Identity" in ctx
        assert "KingBee" in ctx

    def test_no_agent_identity_when_soul_missing(self, hive_env):
        """build_context() does NOT include ## Agent Identity when SOUL.md is absent."""
        from keephive.hooks.sessionstart import build_context
        from keephive.storage import soul_file

        assert not soul_file().exists()
        ctx = build_context("/test/project", "project")
        assert "## Agent Identity" not in ctx
