"""Tests for the self-improving memory loop.

Covers:
- todo done fallthrough to recurring
- PreCompact memory_updates (auto-promote, auto-correct)
- SessionStart auto-reverify
- Accumulation warnings
- Deduplication
"""

from __future__ import annotations

from datetime import date, timedelta

from conftest import make_daily

# ---- Phase 1: todo done + recurring unification ----


class TestTodoDoneRecurring:
    def test_todo_done_matches_regular_todo(self, hive_env, capsys):
        """Regular TODO matching still works."""
        from keephive.commands.remember import cmd_remember
        from keephive.commands.todo import _todo_done

        cmd_remember(["TODO: fix the login bug"])
        capsys.readouterr()  # discard

        _todo_done("login")
        out = capsys.readouterr().out
        assert "Completed" in out

    def test_todo_done_falls_through_to_recurring(self, hive_env, capsys):
        """When no regular TODO matches, falls through to recurring."""
        from keephive.commands.recurring import _ensure_recurring, _recurring_add
        from keephive.commands.todo import _todo_done

        _ensure_recurring()
        _recurring_add("daily", "Run test suite")
        capsys.readouterr()  # discard

        _todo_done("test suite")
        out = capsys.readouterr().out
        assert "Done" in out
        assert "test suite" in out.lower()

    def test_todo_done_no_match_anywhere(self, hive_env, capsys):
        """When neither regular nor recurring matches, shows clear feedback."""
        from keephive.commands.todo import _todo_done

        _todo_done("nonexistent xyz")
        out = capsys.readouterr().out
        assert "No matching TODO" in out, f"Should report no matching TODO. Output: {out!r}"

    def test_todo_hints_shown(self, hive_env, capsys):
        """cmd_todo shows contextual hints."""
        from keephive.commands.todo import cmd_todo

        cmd_todo([])
        out = capsys.readouterr().out
        assert "td <pat>" in out
        assert "t <text>" in out
        assert "todo repeat" in out
        assert "e todo" in out


class TestMcpTodoDoneRecurring:
    """MCP hive_todo_done falls through to recurring tasks."""

    def test_mcp_todo_done_recurring_match(self, hive_env):
        """MCP hive_todo_done marks recurring task done when no regular TODO matches."""
        from keephive.commands.recurring import _ensure_recurring, _recurring_add
        from keephive.mcp_server import hive_todo_done
        from keephive.storage import recurring_file, safe_read_text

        _ensure_recurring()
        # Suppress Rich output from _recurring_add
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _recurring_add("daily", "Run test suite")

        result = hive_todo_done("test suite")
        assert "Done" in result
        assert "Run test suite" in result
        assert "next due per schedule" in result

        # Verify the file was actually updated
        content = safe_read_text(recurring_file())
        assert "Run test suite:" in content
        assert date.today().isoformat() in content

    def test_mcp_todo_done_no_match(self, hive_env):
        """MCP hive_todo_done returns clear message when nothing matches."""
        from keephive.mcp_server import hive_todo_done

        result = hive_todo_done("nonexistent xyz")
        assert "No open TODO or recurring task" in result
        assert "nonexistent xyz" in result

    def test_mcp_todo_done_prefers_regular_todo(self, hive_env):
        """MCP hive_todo_done matches regular TODOs before falling through."""
        from keephive.commands.recurring import _ensure_recurring, _recurring_add
        from keephive.commands.remember import cmd_remember
        from keephive.mcp_server import hive_todo_done

        cmd_remember(["TODO: review test suite coverage"])
        _ensure_recurring()
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _recurring_add("daily", "Run test suite")

        result = hive_todo_done("test suite")
        # Should match the regular TODO, not the recurring one
        assert "Completed" in result


# ---- Phase 1: Help rewrite ----


class TestHelpRewrite:
    def test_help_grouped_sections(self, hive_env, capsys):
        """Help output has grouped sections."""
        from keephive.cli import _help

        _help()
        out = capsys.readouterr().out
        assert "Capture & Search" in out
        assert "Workflows" in out
        assert "Manage" in out

    def test_help_no_hook_commands(self, hive_env, capsys):
        """Help output does not expose hook commands."""
        from keephive.cli import _help

        _help()
        out = capsys.readouterr().out
        assert "hook-precompact" not in out
        assert "hook-sessionstart" not in out
        assert "hook-posttooluse" not in out
        assert "hook-userpromptsubmit" not in out

    def test_help_no_todo_repeat_done(self, hive_env, capsys):
        """Help output does not show todo repeat done (handled by todo done)."""
        from keephive.cli import _help

        _help()
        out = capsys.readouterr().out
        assert "todo repeat done" not in out


# ---- Phase 2: Models ----


class TestMemoryUpdateModels:
    def test_memory_action_enum(self):
        from keephive.models import MemoryAction

        assert MemoryAction.ADD == "add"
        assert MemoryAction.CORRECT == "correct"

    def test_memory_update_add(self):
        from keephive.models import MemoryAction, MemoryUpdate

        mu = MemoryUpdate(action=MemoryAction.ADD, text="Python uses GIL")
        assert mu.action == MemoryAction.ADD
        assert mu.text == "Python uses GIL"
        assert mu.replaces is None

    def test_memory_update_correct(self):
        from keephive.models import MemoryAction, MemoryUpdate

        mu = MemoryUpdate(
            action=MemoryAction.CORRECT,
            text="Python 3.13 removes GIL optionally",
            replaces="Python uses GIL",
        )
        assert mu.action == MemoryAction.CORRECT
        assert mu.replaces == "Python uses GIL"

    def test_precompact_response_backward_compat(self):
        """PreCompactResponse still works without memory_updates."""
        from keephive.models import PreCompactResponse

        r = PreCompactResponse(insights=[{"category": "FACT", "description": "test"}])
        assert len(r.insights) == 1
        assert r.memory_updates == []

    def test_precompact_response_with_updates(self):
        from keephive.models import PreCompactResponse

        r = PreCompactResponse(
            insights=[{"category": "FACT", "description": "test"}],
            memory_updates=[
                {"action": "add", "text": "New fact here"},
            ],
        )
        assert len(r.memory_updates) == 1
        assert r.memory_updates[0].text == "New fact here"


# ---- Phase 2: Queue to .pending-facts.md ----


class TestPendingFactsQueue:
    def test_add_queues_to_pending_facts(self, hive_env):
        """Memory ADD action queues to .pending-facts.md."""
        from keephive.hooks.precompact import _apply_memory_updates, _pending_facts_path
        from keephive.models import MemoryAction, MemoryUpdate
        from keephive.storage import ensure_daily, safe_read_text

        ensure_daily()
        updates = [MemoryUpdate(action=MemoryAction.ADD, text="New auto fact")]
        _apply_memory_updates(updates)

        pf = _pending_facts_path()
        assert pf.exists()
        content = safe_read_text(pf)
        assert "- New auto fact" in content
        assert "[auto:" in content

    def test_correct_queues_with_replaces_metadata(self, hive_env):
        """Memory CORRECT action queues with [replaces:] metadata."""
        from keephive.hooks.precompact import _apply_memory_updates, _pending_facts_path
        from keephive.models import MemoryAction, MemoryUpdate
        from keephive.storage import ensure_daily, memory_file, safe_read_text

        ensure_daily()
        mem = memory_file()
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("# Memory\n- Python uses pip [verified:2025-01-01]\n")

        updates = [
            MemoryUpdate(
                action=MemoryAction.CORRECT,
                text="Python uses uv",
                replaces="Python uses pip",
            )
        ]
        _apply_memory_updates(updates)

        content = safe_read_text(_pending_facts_path())
        assert "Python uses uv" in content
        assert "[replaces:Python uses pip]" in content

    def test_multiple_adds_append_to_pending(self, hive_env):
        """Multiple ADD updates accumulate in .pending-facts.md."""
        from keephive.hooks.precompact import _apply_memory_updates, _pending_facts_path
        from keephive.models import MemoryAction, MemoryUpdate
        from keephive.storage import ensure_daily, safe_read_text

        ensure_daily()
        updates = [
            MemoryUpdate(action=MemoryAction.ADD, text="Fact alpha"),
            MemoryUpdate(action=MemoryAction.ADD, text="Fact beta"),
        ]
        _apply_memory_updates(updates)

        content = safe_read_text(_pending_facts_path())
        assert "Fact alpha" in content
        assert "Fact beta" in content

    def test_apply_memory_updates_add(self, hive_env):
        """_apply_memory_updates with ADD creates ## Auto-Captured entry."""
        from keephive.hooks.precompact import _apply_memory_updates
        from keephive.models import MemoryAction, MemoryUpdate
        from keephive.storage import daily_file, ensure_daily, safe_read_text

        ensure_daily()
        updates = [MemoryUpdate(action=MemoryAction.ADD, text="uv is faster than pip")]
        _apply_memory_updates(updates)

        # Fact should be in .pending-facts.md, not memory.md directly
        from keephive.hooks.precompact import _pending_facts_path

        pf_content = safe_read_text(_pending_facts_path())
        assert "uv is faster than pip" in pf_content

        # Check daily log has AUTO-CAPTURED
        daily_content = safe_read_text(daily_file())
        assert "AUTO-CAPTURED" in daily_content
        assert "uv is faster than pip" in daily_content

    def test_apply_memory_updates_max_3(self, hive_env):
        """_apply_memory_updates caps at 3 updates."""
        from keephive.hooks.precompact import _apply_memory_updates, _pending_facts_path
        from keephive.models import MemoryAction, MemoryUpdate
        from keephive.storage import ensure_daily, safe_read_text

        ensure_daily()
        distinct_facts = [
            "Rust has zero-cost abstractions",
            "PostgreSQL supports JSONB columns",
            "Redis is an in-memory data store",
            "GraphQL uses schema-first design",
            "Docker containers share the host kernel",
        ]
        updates = [MemoryUpdate(action=MemoryAction.ADD, text=fact) for fact in distinct_facts]
        _apply_memory_updates(updates)

        pf_content = safe_read_text(_pending_facts_path())
        # Only 3 should be queued (hard cap)
        queued_count = sum(
            1 for line in pf_content.splitlines() if any(f in line for f in distinct_facts)
        )
        assert queued_count == 3

    def test_apply_memory_updates_dedup(self, hive_env):
        """Duplicate facts are not auto-promoted."""
        from keephive.hooks.precompact import _apply_memory_updates
        from keephive.models import MemoryAction, MemoryUpdate
        from keephive.storage import ensure_daily, memory_file, safe_read_text

        # Memory already has "keephive uses Pydantic"
        ensure_daily()
        updates = [
            MemoryUpdate(action=MemoryAction.ADD, text="keephive uses Pydantic for validation"),
        ]
        _apply_memory_updates(updates)

        mem_content = safe_read_text(memory_file())
        # Should NOT add a duplicate
        assert "## Auto-Captured" not in mem_content


# ---- Phase 2: Auto-correct ----


class TestAutoCorrect:
    def test_correct_in_memory(self, hive_env):
        """_correct_in_memory replaces matching fact."""
        from keephive.hooks.precompact import _correct_in_memory

        content = (
            "# Working Memory\n\n"
            "- Python is great [verified:2020-01-01]\n"
            "- Other fact [verified:2026-01-01]\n"
        )
        result = _correct_in_memory(content, "Python is great", "Python is excellent", "2026-02-17")
        assert "- Python is excellent [verified:2026-02-17]" in result
        assert "Python is great" not in result
        assert "Other fact" in result

    def test_correct_in_memory_no_match(self, hive_env):
        """_correct_in_memory returns unchanged if no match."""
        from keephive.hooks.precompact import _correct_in_memory

        content = "# Working Memory\n\n- Some fact [verified:2026-01-01]\n"
        result = _correct_in_memory(content, "nonexistent fact", "new value", "2026-02-17")
        assert result == content

    def test_apply_memory_updates_correct(self, hive_env):
        """_apply_memory_updates with CORRECT queues to .pending-facts.md."""
        from keephive.hooks.precompact import _apply_memory_updates, _pending_facts_path
        from keephive.models import MemoryAction, MemoryUpdate
        from keephive.storage import daily_file, ensure_daily, safe_read_text

        ensure_daily()
        updates = [
            MemoryUpdate(
                action=MemoryAction.CORRECT,
                text="Python is amazing",
                replaces="Python is great",
            )
        ]
        _apply_memory_updates(updates)

        # CORRECT now queues to .pending-facts.md with [replaces:] metadata
        pf_content = safe_read_text(_pending_facts_path())
        assert "Python is amazing" in pf_content
        assert "[replaces:Python is great]" in pf_content

        # Check daily log has AUTO-CAPTURED
        daily_content = safe_read_text(daily_file())
        assert "AUTO-CAPTURED" in daily_content


# ---- Phase 2: Auto-reverify ----


class TestAutoReverify:
    def test_auto_reverify_updates_date(self, hive_env):
        """Stale fact with matching daily entry gets re-verified."""
        from keephive.hooks.sessionstart import _auto_reverify
        from keephive.storage import memory_file, safe_read_text

        # Memory has a stale fact about Python
        old_date = (date.today() - timedelta(days=60)).isoformat()
        mem_path = memory_file()
        mem_path.write_text(
            f"# Working Memory\n\n- Python uses GIL for thread safety [verified:{old_date}]\n"
        )

        # Add matching daily entry
        make_daily(
            hive_env,
            days_ago=1,
            entries=[
                "- [10:00:00] FACT: Python uses GIL for thread safety confirmed",
            ],
        )

        reverified = _auto_reverify()
        assert len(reverified) == 1

        mem_content = safe_read_text(mem_path)
        today_str = date.today().isoformat()
        assert f"[verified:{today_str}]" in mem_content
        assert f"[verified:{old_date}]" not in mem_content

    def test_auto_reverify_no_match(self, hive_env):
        """Stale fact without matching entry is not re-verified."""
        from keephive.hooks.sessionstart import _auto_reverify
        from keephive.storage import memory_file, safe_read_text

        old_date = (date.today() - timedelta(days=60)).isoformat()
        mem_path = memory_file()
        mem_path.write_text(
            f"# Working Memory\n\n- Obscure fact about quantum computing [verified:{old_date}]\n"
        )

        # Add unrelated daily entry
        make_daily(
            hive_env,
            days_ago=1,
            entries=[
                "- [10:00:00] FACT: Python is great for web development",
            ],
        )

        reverified = _auto_reverify()
        assert len(reverified) == 0

        mem_content = safe_read_text(mem_path)
        assert f"[verified:{old_date}]" in mem_content

    def test_auto_reverify_fresh_facts_skipped(self, hive_env):
        """Fresh facts (not stale) are not touched."""
        from keephive.hooks.sessionstart import _auto_reverify
        from keephive.storage import memory_file

        recent_date = date.today().isoformat()
        mem_path = memory_file()
        mem_path.write_text(f"# Working Memory\n\n- Fresh fact [verified:{recent_date}]\n")

        reverified = _auto_reverify()
        assert len(reverified) == 0


# ---- Phase 2: Accumulation warnings ----


class TestAccumulationWarnings:
    def test_warn_many_facts(self, hive_env):
        """Warns when memory has >40 facts."""
        from keephive.hooks.sessionstart import _accumulation_warnings

        lines = ["# Working Memory\n"]
        for i in range(45):
            lines.append(f"- Fact number {i} [verified:2026-01-01]")
        content = "\n".join(lines)

        warnings = _accumulation_warnings(content)
        assert any("45 facts" in w for w in warnings)
        assert any("hive rf" in w for w in warnings)

    def test_warn_pending_facts_count(self, hive_env):
        """Warns when .pending-facts.md has pending facts."""
        from keephive.hooks.sessionstart import _accumulation_warnings
        from keephive.storage import hive_dir

        # Write pending facts to .pending-facts.md
        pf = hive_dir() / ".pending-facts.md"
        pf.write_text("\n".join(f"- Auto fact {i} [auto:2026-02-17]" for i in range(7)) + "\n")

        warnings = _accumulation_warnings("# Working Memory\n\n- Normal fact\n")
        assert any("7 fact" in w and "pending review" in w for w in warnings)

    def test_warn_critical_stale(self, hive_env):
        """Warns CRITICAL for facts stale >60 days."""
        from keephive.hooks.sessionstart import _accumulation_warnings

        old_date = (date.today() - timedelta(days=90)).isoformat()
        content = f"# Memory\n\n- Old fact [verified:{old_date}]\n"

        warnings = _accumulation_warnings(content)
        assert any("CRITICAL" in w for w in warnings)

    def test_no_warnings_when_healthy(self, hive_env):
        """No warnings for healthy memory."""
        from keephive.hooks.sessionstart import _accumulation_warnings

        recent_date = date.today().isoformat()
        content = (
            f"# Memory\n\n- Fact 1 [verified:{recent_date}]\n- Fact 2 [verified:{recent_date}]\n"
        )

        warnings = _accumulation_warnings(content)
        assert warnings == []


# ---- Phase 2: Dedup in memory ----


class TestDedupInMemory:
    def test_is_duplicate_exact(self):
        """Exact duplicates are caught."""
        from keephive.hooks.precompact import _is_duplicate_in_memory

        content = "- Python is great [verified:2026-01-01]\n"
        assert _is_duplicate_in_memory(content, "Python is great") is True

    def test_is_duplicate_fuzzy(self):
        """Fuzzy near-duplicates are caught at 0.7 threshold."""
        from keephive.hooks.precompact import _is_duplicate_in_memory

        content = "- Python is a great programming language [verified:2026-01-01]\n"
        assert (
            _is_duplicate_in_memory(content, "Python is a great programming language for data")
            is True
        )

    def test_not_duplicate(self):
        """Unrelated content is not flagged as duplicate."""
        from keephive.hooks.precompact import _is_duplicate_in_memory

        content = "- Python is great [verified:2026-01-01]\n"
        assert _is_duplicate_in_memory(content, "Rust has zero-cost abstractions") is False


# ---- Phase 2: Integration (build_context with auto-reverify) ----


class TestBuildContextIntegration:
    def testbuild_context_reverify_silently(self, hive_env):
        """build_context auto-reverifies but no longer injects summary (context diet)."""
        from keephive.hooks.sessionstart import build_context
        from keephive.storage import memory_file

        old_date = (date.today() - timedelta(days=60)).isoformat()
        mem_path = memory_file()
        mem_path.write_text(
            f"# Working Memory\n\n- Python uses Pydantic models [verified:{old_date}]\n"
        )

        # Add matching daily entry
        make_daily(
            hive_env,
            days_ago=1,
            entries=[
                "- [10:00:00] FACT: keephive uses Pydantic models for validation",
            ],
        )

        context = build_context("/test/project", "project")
        # Auto-reverify still runs but summary is not injected into context
        assert "auto-updated" not in context.lower()
        # The fact should have been re-verified in memory.md though
        updated_mem = mem_path.read_text()
        assert date.today().isoformat() in updated_mem

    def testbuild_context_no_accumulation_warnings(self, hive_env):
        """build_context no longer includes verbose accumulation warnings (context diet).

        The compact suggest_next hint ("Suggested next action: hive rf ...") IS
        expected in build_context. The verbose _accumulation_warnings output
        ("Memory has N facts. Consider consolidating") is NOT.
        """
        from keephive.hooks.sessionstart import build_context
        from keephive.storage import memory_file

        lines = ["# Working Memory\n"]
        for i in range(45):
            lines.append(f"- Fact number {i} [verified:2026-02-17]")
        memory_file().write_text("\n".join(lines) + "\n")

        context = build_context("/test/project", "project")
        # Verbose accumulation warnings moved to cmd_status
        assert "Consider consolidating" not in context
        assert "auto-captured facts" not in context
        # Compact suggest_next hint IS expected
        assert "Suggested next action" in context


# ---- Secret redaction ----


class TestRedactSecrets:
    def test_redacts_api_key_value(self):
        """Redacts key=value style API keys."""
        from keephive.hooks.precompact import _redact_secrets

        text = "API key: moltbook_sk_S3c22Dp_NYLg5gvVIv6rRlVp-tfMrhei"
        result = _redact_secrets(text)
        assert "moltbook_sk_S3c22Dp" not in result
        assert "[REDACTED]" in result

    def test_redacts_prefixed_secret(self):
        """Redacts prefixed secrets like stripe_sk_..."""
        from keephive.hooks.precompact import _redact_secrets

        text = "Used stripe_sk_test_abc123defg456789 for payment"
        result = _redact_secrets(text)
        assert "stripe_sk_test" not in result
        assert "[REDACTED]" in result

    def test_redacts_bearer_token(self):
        """Redacts Bearer tokens."""
        from keephive.hooks.precompact import _redact_secrets

        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        result = _redact_secrets(text)
        assert "eyJhbGciOiJ" not in result
        assert "Bearer [REDACTED]" in result

    def test_preserves_normal_text(self):
        """Normal text without secrets is not mangled."""
        from keephive.hooks.precompact import _redact_secrets

        text = "The user prefers uv over pip for package management"
        result = _redact_secrets(text)
        assert result == text

    def test_redacts_password_value(self):
        """Redacts password= style entries."""
        from keephive.hooks.precompact import _redact_secrets

        text = "password=SuperSecretPassword123456"
        result = _redact_secrets(text)
        assert "SuperSecretPassword" not in result
        assert "[REDACTED]" in result

    def test_multiple_secrets_in_one_text(self):
        """Multiple secrets in same text all get redacted."""
        from keephive.hooks.precompact import _redact_secrets

        text = "token: abc123def456ghi789jkl and also app_secret_XYZ123456789ABC"
        result = _redact_secrets(text)
        assert "abc123def456ghi789jkl" not in result
        assert "app_secret_XYZ123456789ABC" not in result


# ---- Improved duplicate detection ----


class TestImprovedDuplicateInsight:
    def test_prefix_match_catches_rephrased(self, hive_env):
        """Prefix-based match catches rephrased duplicates with same start."""
        from keephive.hooks.precompact import _is_duplicate_insight
        from keephive.storage import daily_file, ensure_daily

        ensure_daily()
        df = daily_file()
        df.write_text(
            "# Daily Log\n\n"
            "- [10:00:00] FACT: mindlessmuze is a registered Moltbook agent. "
            "API key: abc123. Claim URL: https://example.com/claim/long-token-here\n"
        )

        # Similar fact, different ending (shorter)
        assert (
            _is_duplicate_insight(
                df,
                "mindlessmuze is a registered Moltbook agent. API key redacted. Claim URL provided.",
            )
            is True
        )

    def test_url_stripping_helps_match(self, hive_env):
        """Normalizing URLs helps match otherwise-different text."""
        from keephive.hooks.precompact import _is_duplicate_insight
        from keephive.storage import daily_file, ensure_daily

        ensure_daily()
        df = daily_file()
        df.write_text(
            "# Daily Log\n\n"
            "- [10:00:00] INSIGHT: Viral formula on Moltbook: posts need a story "
            "https://moltbook.com/post/abc123 and verified claims\n"
        )

        # Same insight without URL
        assert (
            _is_duplicate_insight(
                df, "Viral formula on Moltbook: posts need a story and verified claims"
            )
            is True
        )

    def test_genuinely_different_not_flagged(self, hive_env):
        """Genuinely different insights are not flagged as duplicates."""
        from keephive.hooks.precompact import _is_duplicate_insight
        from keephive.storage import daily_file, ensure_daily

        ensure_daily()
        df = daily_file()
        df.write_text("# Daily Log\n\n- [10:00:00] FACT: Python uses GIL for thread safety\n")

        assert (
            _is_duplicate_insight(df, "Rust has zero-cost abstractions and no garbage collector")
            is False
        )

    def test_normalize_for_dedup(self):
        """_normalize_for_dedup strips URLs and long tokens."""
        from keephive.hooks.precompact import _normalize_for_dedup

        text = "Agent registered at https://moltbook.com/claim/abc123def456 with key moltbook_sk_S3c22Dp_NYLg5gvVIv6rRlVp"
        result = _normalize_for_dedup(text)
        assert "https://" not in result
        assert "moltbook_sk" not in result
        assert "agent registered" in result
