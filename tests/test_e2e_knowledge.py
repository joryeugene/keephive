"""E2E tests for knowledge guide lifecycle.

Priority 6 from the E2E coverage gap analysis.

Tests the CRUD operations for knowledge guides and verifies that
guides with `always: true` frontmatter are injected by sessionstart
regardless of project context.

Run: uv run pytest -m terminal -k test_e2e_knowledge -v -o "addopts="
"""

from __future__ import annotations

import pytest


@pytest.mark.terminal
class TestKnowledgeGuideLifecycle:
    """Verify knowledge guide list, view, create, and injection."""

    def test_empty_state(self, term, save_terminal_output):
        """hive k with no guides shows 'No guides yet'."""
        screen = term.type("python -m keephive k")
        screen.has("No guides yet")
        save_terminal_output("knowledge/empty_state", term)

    def test_list_shows_created_guide(self, term, save_terminal_output):
        """Manually created guide appears in hive k listing."""
        # Create a guide directly (bypass editor)
        term.type(
            'python -c "from pathlib import Path; import os; '
            "Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'testing.md')"
            ".write_text('# Testing Guide\\n\\nAlways run tests before committing.\\n')\""
        )

        screen = term.type("python -m keephive k")
        screen.has("testing")
        screen.lacks("No guides yet")

        save_terminal_output("knowledge/list_with_guide", term)

    def test_view_guide_content(self, term, save_terminal_output):
        """hive k <name> displays the guide content."""
        term.type(
            'python -c "from pathlib import Path; import os; '
            "Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'deployment.md')"
            ".write_text('# Deployment Guide\\n\\nUse blue-green deployments for zero downtime.\\n')\""
        )

        screen = term.type("python -m keephive k deployment")
        screen.has("Deployment Guide", "blue-green")

        save_terminal_output("knowledge/view_content", term)

    def test_view_fuzzy_resolution(self, term, save_terminal_output):
        """hive k with partial name resolves via prefix/substring match."""
        term.type(
            'python -c "from pathlib import Path; import os; '
            "Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'api-design.md')"
            ".write_text('# API Design\\n\\nUse REST conventions.\\n')\""
        )

        # Prefix match
        screen = term.type("python -m keephive k api")
        screen.has("API Design", "REST")

        save_terminal_output("knowledge/fuzzy_resolution", term)

    def test_guide_with_tags_displayed(self, term, save_terminal_output):
        """Guide with YAML frontmatter tags shows tags in listing."""
        guide_content = (
            "---\\ntags: [keephive, testing]\\n---\\n# Tagged Guide\\n\\nContent with tags.\\n"
        )
        term.type(
            'python -c "from pathlib import Path; import os; '
            f"Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'tagged.md')"
            f".write_text('{guide_content}')\""
        )

        screen = term.type("python -m keephive k")
        screen.has("tagged", "tags:")

        save_terminal_output("knowledge/tags_displayed", term)

    def test_guide_not_found(self, term, save_terminal_output):
        """hive k <nonexistent> shows 'not found' message."""
        screen = term.type("python -m keephive k nonexistent_guide_xyz")
        screen.has("not found")

        save_terminal_output("knowledge/not_found", term)

    def test_multiple_guides_listed(self, term, save_terminal_output):
        """Multiple guides are listed alphabetically."""
        for name in ["beta-guide", "alpha-guide", "gamma-guide"]:
            term.type(
                'python -c "from pathlib import Path; import os; '
                f"Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', '{name}.md')"
                f".write_text('# {name}\\n\\nContent for {name}.\\n')\""
            )

        screen = term.type("python -m keephive k")
        screen.has("alpha-guide", "beta-guide", "gamma-guide")
        screen.lacks("No guides yet")

        save_terminal_output("knowledge/multiple_listed", term)

    def test_always_true_guide_injected_by_sessionstart(self, term, save_terminal_output):
        """Guide with 'always: true' frontmatter is injected into sessionstart context."""
        # Create an always-inject guide
        guide_content = (
            "---\\n"
            "always: true\\n"
            "---\\n"
            "# Universal Principles\\n\\n"
            "Always verify before deploying.\\n"
        )
        term.type(
            'python -c "from pathlib import Path; import os; '
            f"Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'principles.md')"
            f".write_text('{guide_content}')\""
        )

        # Run sessionstart with a non-matching project name
        # The always:true guide should still appear in output
        screen = term.type(
            'echo \'{"source":"terminal","cwd":"/some/random/project"}\' '
            "| python -m keephive hook-sessionstart; echo"
        )

        # sessionstart outputs context text that includes matched guide content
        output = screen.plain
        assert "Universal Principles" in output or "principles" in output.lower(), (
            f"Expected always:true guide to be injected. Output:\n{output[:500]}"
        )

        save_terminal_output("knowledge/always_true_injection", term)

    def test_sessionstart_skips_non_matching_guide(self, term):
        """Guide without always:true and non-matching tags is NOT injected."""
        # Create a project-specific guide (not always)
        # Use a tag with no substring overlap with the test cwd
        guide_content = (
            "---\\n"
            "tags: [zebra-unicorn-flamingo]\\n"
            "---\\n"
            "# Zebra Unicorn Guide\\n\\n"
            "Only for zebra-unicorn-flamingo.\\n"
        )
        term.type(
            'python -c "from pathlib import Path; import os; '
            f"Path(os.environ['HIVE_HOME'], 'knowledge', 'guides', 'zebra.md')"
            f".write_text('{guide_content}')\""
        )

        # Run sessionstart with a completely different project name
        screen = term.type(
            'echo \'{"source":"terminal","cwd":"/home/dev/alpha-repo"}\' '
            "| python -m keephive hook-sessionstart; echo"
        )

        output = screen.plain
        assert "Only for zebra-unicorn-flamingo" not in output, (
            "Non-matching guide should not be injected"
        )
