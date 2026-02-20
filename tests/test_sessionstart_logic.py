"""Tests for SessionStart hook logic (hooks/sessionstart.py).

Tests pure-logic functions without invoking the hook as a subprocess.
"""

from __future__ import annotations

from conftest import make_daily

# ---- _data_quality_warnings ----


class TestDataQualityWarnings:
    def test_duplicate_todos(self, hive_env):
        """Near-duplicate TODOs flagged."""
        make_daily(
            hive_env,
            days_ago=0,
            entries=[
                "- [10:00:00] TODO: Fix the authentication bug in login flow",
                "- [10:05:00] TODO: Fix the authentication bug in the login flow",
            ],
        )

        from keephive.hooks.sessionstart import _data_quality_warnings

        warnings = _data_quality_warnings()
        found_dup = any("duplicate" in w.lower() for w in warnings)
        assert found_dup

    def test_stale_todos_over_7_days(self, hive_env):
        """TODOs older than 7 days flagged."""
        make_daily(
            hive_env,
            days_ago=10,
            entries=[
                "- [10:00:00] TODO: Ancient task that was never done",
            ],
        )

        from keephive.hooks.sessionstart import _data_quality_warnings

        warnings = _data_quality_warnings()
        found_stale = any("7 days" in w for w in warnings)
        assert found_stale

    def test_accumulation_warning(self, hive_env):
        """More than 10 open TODOs triggers warning."""
        entries = [f"- [10:{i:02d}:00] TODO: Task number {i}" for i in range(12)]
        make_daily(hive_env, days_ago=0, entries=entries)

        from keephive.hooks.sessionstart import _data_quality_warnings

        warnings = _data_quality_warnings()
        found_accumulation = any("open TODO" in w for w in warnings)
        assert found_accumulation

    def test_clean_state_no_warnings(self, hive_env):
        """No warnings when everything is clean."""
        make_daily(
            hive_env,
            days_ago=0,
            entries=[
                "- [10:00:00] FACT: Just a fact, no TODOs",
            ],
        )

        from keephive.hooks.sessionstart import _data_quality_warnings

        warnings = _data_quality_warnings()
        assert len(warnings) == 0

    def test_completed_todos_ignored(self, hive_env):
        """Completed TODOs don't count toward warnings."""
        make_daily(
            hive_env,
            days_ago=0,
            entries=[
                "- [10:00:00] TODO: Do the thing",
                "- [10:05:00] DONE: Do the thing",
            ],
        )

        from keephive.hooks.sessionstart import _data_quality_warnings

        warnings = _data_quality_warnings()
        # No accumulation or staleness warnings
        assert len(warnings) == 0


# ---- _match_guides ----


class TestMatchGuides:
    def test_match_by_front_matter_tag(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "my-tool.md").write_text(
            "---\ntags: [python]\nprojects: [keephive]\n---\n# My Tool Guide\n\nContent.\n"
        )

        from keephive.hooks.sessionstart import _match_guides

        result = _match_guides("keephive")
        assert "My Tool Guide" in result

    def test_match_by_filename(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "keephive-guide.md").write_text("# keephive Guide\n\nInfo.\n")

        from keephive.hooks.sessionstart import _match_guides

        result = _match_guides("keephive")
        assert "keephive Guide" in result or "keephive-guide" in result

    def test_max_guides_limit(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        for i in range(5):
            (gd / f"myproj-guide-{i}.md").write_text(f"# Guide {i}\n\n{'x ' * 100}\n")

        from keephive.hooks.sessionstart import _match_guides

        result = _match_guides("myproj")
        # Should be limited to max_guides=3
        guide_count = result.count("--- Guide:")
        assert guide_count <= 3

    def test_max_words_limit(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        # Create a very long guide
        (gd / "longproj-guide.md").write_text("# Long\n\n" + "word " * 2000 + "\n")

        from keephive.hooks.sessionstart import _match_guides

        result = _match_guides("longproj")
        # Result should be bounded by max_words=1500
        word_count = len(result.split())
        assert word_count <= 1600  # Allow some overhead from headers

    def test_strips_front_matter(self, hive_env):
        gd = hive_env / "knowledge" / "guides"
        (gd / "testproj-guide.md").write_text(
            "---\ntags: [testproj]\nprojects: [testproj]\n---\n# Guide Content\n\nActual content.\n"
        )

        from keephive.hooks.sessionstart import _match_guides

        result = _match_guides("testproj")
        assert "tags:" not in result
        assert "Guide Content" in result

    def test_no_match_returns_empty(self, hive_env):
        from keephive.hooks.sessionstart import _match_guides

        result = _match_guides("nonexistent-project-xyz")
        assert result == ""

    def test_missing_guides_dir(self, hive_env):
        import shutil

        gd = hive_env / "knowledge" / "guides"
        shutil.rmtree(gd)

        from keephive.hooks.sessionstart import _match_guides

        result = _match_guides("keephive")
        assert result == ""


# ---- build_context integration ----


class TestBuildContext:
    def test_includes_memory(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "Working Memory" in ctx

    def test_includes_rules(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "When You Learn Something New" in ctx

    def test_includes_stale_warning(self, hive_env):
        """memory.md fixture has a fact from 2020-01-01 which is stale."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "stale" in ctx.lower()

    def test_workflows_not_statically_injected(self, hive_env):
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        # Workflows section removed from static injection (token bloat reduction).
        # It lives in the keephive-guide, injected only when that guide matches.
        assert "Workflows" not in ctx

    def test_guide_notification_not_in_context(self, hive_env, monkeypatch):
        """Guide update notifications moved to hive status (context diet)."""
        import keephive.commands.setup as _setup_mod
        from keephive.hooks.sessionstart import build_context

        monkeypatch.setattr(_setup_mod, "check_bundled_updates", lambda: 2)
        ctx = build_context("/tmp/test", "test")

        # Guide notifications are now in cmd_status, not in session context
        assert "bundled guide" not in ctx

    def test_no_quality_pulse_in_context(self, hive_env):
        """Quality Pulse moved to hive status (context diet)."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "Quality Pulse" not in ctx

    def test_no_accumulation_warnings_in_context(self, hive_env):
        """Accumulation warnings moved to hive status (context diet)."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "auto-captured facts pending" not in ctx

    def test_no_recent_entries_in_context(self, hive_env):
        """Recent entries removed from context (available via hive_recall)."""
        from conftest import make_daily

        make_daily(
            hive_env,
            days_ago=0,
            entries=["- [10:00:00] FACT: something happened today"],
        )
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "## Recent (today)" not in ctx
        assert "## This Week" not in ctx


# ---- session signal (file-based guard) ----


class TestSessionSignal:
    def test_recent_signal_blocks_injection(self, hive_env):
        """File written <15s ago → hook returns empty context and deletes file."""
        import importlib
        import json
        import sys
        import time
        from io import StringIO
        from unittest.mock import patch

        from keephive.storage import hive_dir

        sig = hive_dir() / ".session-launched"
        sig.write_text(str(int(time.time())))

        output_parts: list[str] = []

        with patch("sys.stdin", StringIO(json.dumps({"cwd": str(hive_dir())}))):
            with patch.object(sys, "stdout") as mock_out:
                mock_out.write = lambda s: output_parts.append(s)
                import keephive.hooks.sessionstart as _ss_mod

                importlib.reload(_ss_mod)
                _ss_mod.hook_sessionstart([])

        result = json.loads("".join(output_parts))
        assert result["hookSpecificOutput"]["additionalContext"] == ""
        assert not sig.exists()

    def test_stale_signal_allows_injection(self, hive_env):
        """File written >15s ago → hook proceeds with injection and deletes file."""
        import importlib
        import json
        import sys
        import time
        from io import StringIO
        from unittest.mock import patch

        from keephive.storage import hive_dir

        sig = hive_dir() / ".session-launched"
        sig.write_text(str(int(time.time()) - 30))  # 30s ago, stale

        output_parts: list[str] = []

        with patch("sys.stdin", StringIO(json.dumps({"cwd": str(hive_dir())}))):
            with patch.object(sys, "stdout") as mock_out:
                mock_out.write = lambda s: output_parts.append(s)
                import keephive.hooks.sessionstart as _ss_mod

                importlib.reload(_ss_mod)
                _ss_mod.hook_sessionstart([])

        result = json.loads("".join(output_parts))
        # stale signal → injection proceeds (additionalContext is non-empty or at least hook ran)
        assert "hookSpecificOutput" in result
        assert not sig.exists()

    def test_no_signal_file_runs_normally(self, hive_env):
        """No signal file → hook runs normally, no empty context shortcut."""
        import importlib
        import json
        import sys
        from io import StringIO
        from unittest.mock import patch

        from keephive.storage import hive_dir

        sig = hive_dir() / ".session-launched"
        assert not sig.exists()

        output_parts: list[str] = []

        with patch("sys.stdin", StringIO(json.dumps({"cwd": str(hive_dir())}))):
            with patch.object(sys, "stdout") as mock_out:
                mock_out.write = lambda s: output_parts.append(s)
                import keephive.hooks.sessionstart as _ss_mod

                importlib.reload(_ss_mod)
                _ss_mod.hook_sessionstart([])

        result = json.loads("".join(output_parts))
        assert "hookSpecificOutput" in result
        # Context should be non-empty (has memory, rules etc from hive_env fixture)
        assert result["hookSpecificOutput"]["additionalContext"] != ""
