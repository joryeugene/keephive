"""E2E terminal tests: TODO discipline, lifecycle nudges, hook mechanics.

Tests the user-visible behaviors of:
- TODO fuzzy dedup (0.8 threshold)
- Lifecycle-aware nudge state machine (priority-based)
- Hook counter/interval mechanics (5 for tool/prompt, 8 for stop)
- PreCompact no-noise guarantees
- Cross-day, multi-profile, and edge-case scenarios

Run all:  uv run pytest -m terminal -k test_e2e_todo_nudge -v -o "addopts="
Run one:  uv run pytest tests/test_e2e_todo_nudge.py -k test_near_duplicate -v -o "addopts="
"""

from __future__ import annotations

import json
import time

import pytest

# ---- Helpers for hook JSON output (handles terminal line wrapping) ----


def _hook_has(screen, *texts):
    """Assert texts appear in hook JSON output (ignores terminal line wraps).

    Hook output is a single JSON line that often exceeds the 120-char tmux pane
    width, causing line breaks mid-word. Joining lines before searching avoids
    false negatives from text split across wrapped lines.
    """
    unwrapped = screen.plain.replace("\n", "")
    for t in texts:
        assert t in unwrapped, f"Expected {t!r} in hook output:\n{screen.plain}"


def _hook_lacks(screen, *texts):
    """Assert texts do NOT appear in hook JSON output."""
    unwrapped = screen.plain.replace("\n", "")
    for t in texts:
        assert t not in unwrapped, f"Unexpected {t!r} in hook output:\n{screen.plain}"


def _hook_json(screen) -> dict:
    """Parse hook JSON output, handling terminal line wrapping."""
    unwrapped = screen.plain.replace("\n", "").strip()
    # The command echo and prompt may be present; find the JSON object
    start = unwrapped.find("{")
    end = unwrapped.rfind("}") + 1
    assert start >= 0 and end > start, f"No JSON found in hook output:\n{screen.plain}"
    return json.loads(unwrapped[start:end])


# ============================================================
#  Category 1: TODO Discipline & Dedup
# ============================================================


@pytest.mark.terminal
class TestTodoDiscipline:
    """Verify fuzzy dedup at 0.8 SequenceMatcher threshold in real terminal."""

    def test_near_duplicate_dedup(self, term, save_terminal_output):
        """Near-identical TODOs (>0.8 similarity) collapse to one in display."""
        term.type("python -m keephive t 'Research portable context standards'")
        term.type("python -m keephive t 'Research portable context standards for agents'")
        term.type("python -m keephive t 'Research portable context standards for memory'")
        screen = term.type("python -m keephive todo")
        # Dedup collapses near-duplicates to 1
        screen.has("portable context")
        # Should NOT show all 3 as separate open TODOs
        screen.lacks("3 open")
        save_terminal_output("todo_discipline/near_duplicate_dedup", term)

    def test_distinct_todos_preserved(self, term, save_terminal_output):
        """Sufficiently different TODOs all survive dedup."""
        term.type("python -m keephive t 'Fix authentication bug in login endpoint'")
        term.type("python -m keephive t 'Write database migration for user profiles'")
        term.type("python -m keephive t 'Deploy monitoring dashboard to production'")
        screen = term.type("python -m keephive todo")
        screen.has("authentication bug", "database migration", "monitoring dashboard")
        save_terminal_output("todo_discipline/distinct_preserved", term)

    def test_dedup_plus_distinct_mix(self, term, save_terminal_output):
        """Duplicates collapse while distinct items survive."""
        # 3 near-duplicates about context
        term.type("python -m keephive t 'Research portable context standards'")
        term.type("python -m keephive t 'Research portable context standards for agents'")
        term.type("python -m keephive t 'Research portable context standards for LLMs'")
        # 1 completely different
        term.type("python -m keephive t 'Fix PostgreSQL connection pooling configuration'")
        screen = term.type("python -m keephive todo")
        screen.has("portable context", "PostgreSQL connection")
        save_terminal_output("todo_discipline/dedup_plus_distinct", term)

    def test_boundary_similarity_preserved(self, term):
        """TODOs just below 0.8 similarity threshold are kept separate."""
        term.type("python -m keephive t 'Fix login page CSS on mobile Safari browser'")
        term.type("python -m keephive t 'Fix signup page API on Android Chrome browser'")
        screen = term.type("python -m keephive todo")
        screen.has("login page", "signup page")


# ============================================================
#  Category 2: Nudge Lifecycle Priorities (via hooks)
# ============================================================


@pytest.mark.terminal
class TestNudgeLifecyclePriorities:
    """Verify priority-based nudge state machine via hook output.

    Priority order: open TODOs > stale facts > pending facts > unreflected logs > fallback.
    Each test sets up one priority level and verifies the nudge message.
    """

    def _set_counter(self, term, name: str, count: int, session_id: str = "test"):
        """Pre-set counter file so next hook call fires at interval boundary."""
        counter_file = term.hive_home / f".{name}-counter"
        counter_file.write_text(json.dumps({"count": count, "session_id": session_id}))

    def test_priority_1_open_todo(self, term, save_terminal_output):
        """With open TODOs, nudge cites the specific TODO text."""
        term.type("python -m keephive t 'Fix authentication endpoint timeout'")
        self._set_counter(term, "tool", 4)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "Open TODO", "authentication endpoint")
        save_terminal_output("nudge_lifecycle/priority_1_todo", term)

    def test_priority_2_stale_facts(self, term, save_terminal_output):
        """With no TODOs but stale facts, nudge suggests hive v."""
        (term.hive_home / "working" / "memory.md").write_text(
            "# Working Memory\n\n- FACT: ancient data [verified:2020-01-01]\n"
        )
        self._set_counter(term, "tool", 4)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "unverified", "hive v")
        save_terminal_output("nudge_lifecycle/priority_2_stale", term)

    def test_priority_3_pending_facts(self, term, save_terminal_output):
        """With pending facts (no TODOs, no stale), nudge suggests mem review."""
        (term.hive_home / ".pending-facts.md").write_text(
            "- FACT: pending fact alpha\n- FACT: pending fact beta\n"
        )
        self._set_counter(term, "tool", 4)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "pending review", "hive mem review")
        save_terminal_output("nudge_lifecycle/priority_3_pending", term)

    def test_priority_4_unreflected_logs(self, term, save_terminal_output):
        """With 7+ unreflected daily logs, nudge suggests hive rf."""
        for i in range(8):
            day = f"2026-02-{10 + i:02d}"
            (term.hive_home / "daily" / f"{day}.md").write_text(
                f"# Daily Log: {day}\n\n- [10:00:00] FACT: log entry {i}\n"
            )
        self._set_counter(term, "tool", 4)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "daily logs", "hive rf")
        save_terminal_output("nudge_lifecycle/priority_4_unreflected", term)

    def test_priority_5_context_fallback_tool(self, term, save_terminal_output):
        """With no actionable state, tool hook shows hive_remember message."""
        self._set_counter(term, "tool", 4)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "hive_remember")
        save_terminal_output("nudge_lifecycle/priority_5_tool_fallback", term)

    def test_priority_5_context_fallback_stop(self, term, save_terminal_output):
        """With no actionable state, stop hook shows hive_remember message."""
        self._set_counter(term, "stop", 7)
        screen = term.type('echo \'{"session_id":"test"}\' | python -m keephive hook-stop; echo')
        _hook_has(screen, "hive_remember")
        save_terminal_output("nudge_lifecycle/priority_5_stop_fallback", term)

    def test_silent_before_interval(self, term):
        """Hook produces no nudge output before counter reaches interval."""
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        # First call: count=1, interval=5 -> silent
        _hook_lacks(screen, "additionalContext", "hookSpecificOutput")


# ============================================================
#  Category 3: Hook Counter & Interval Mechanics
# ============================================================


@pytest.mark.terminal
class TestHookCounterMechanics:
    """Verify counter-based firing at correct intervals (5 for tool, 8 for stop)."""

    def _set_counter(self, term, name: str, count: int, session_id: str = "test"):
        counter_file = term.hive_home / f".{name}-counter"
        counter_file.write_text(json.dumps({"count": count, "session_id": session_id}))

    def _read_counter(self, term, name: str) -> int:
        counter_file = term.hive_home / f".{name}-counter"
        data = json.loads(counter_file.read_text())
        return data["count"]

    def test_tool_fires_at_interval_5(self, term):
        """PostToolUse fires nudge at count=5 (default interval)."""
        self._set_counter(term, "tool", 4)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "additionalContext")

    def test_tool_silent_at_non_interval(self, term):
        """PostToolUse silent at count=4 (not at interval boundary)."""
        self._set_counter(term, "tool", 3)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_lacks(screen, "additionalContext")

    def test_stop_fires_at_interval_8(self, term):
        """Stop hook fires nudge at count=8 (stop-specific interval)."""
        self._set_counter(term, "stop", 7)
        screen = term.type('echo \'{"session_id":"test"}\' | python -m keephive hook-stop; echo')
        _hook_has(screen, "additionalContext")

    def test_stop_silent_at_5(self, term):
        """Stop hook silent at count=5 (stop interval is 8, not 5)."""
        self._set_counter(term, "stop", 4)
        screen = term.type('echo \'{"session_id":"test"}\' | python -m keephive hook-stop; echo')
        _hook_lacks(screen, "additionalContext")

    def test_counter_resets_on_session_change(self, term):
        """Counter resets to 0 when session_id changes."""
        self._set_counter(term, "tool", 4, "old-session")
        # New session resets count to 0, then increments to 1 (not at interval 5)
        screen = term.type(
            'echo \'{"session_id":"new-session","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_lacks(screen, "additionalContext")
        assert self._read_counter(term, "tool") == 1

    def test_counter_increments_correctly(self, term):
        """Counter shows correct value after sequential calls."""
        for _ in range(3):
            term.type(
                'echo \'{"session_id":"test","tool_name":"Edit"}\''
                " | python -m keephive hook-posttooluse; echo"
            )
        assert self._read_counter(term, "tool") == 3

    def test_hook_output_is_valid_json(self, term):
        """When hook fires, output is parseable JSON with correct structure."""
        self._set_counter(term, "tool", 4)
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        data = _hook_json(screen)
        assert "hookSpecificOutput" in data
        assert "additionalContext" in data["hookSpecificOutput"]


# ============================================================
#  Category 4: PreCompact No-Noise
# ============================================================


@pytest.mark.terminal
class TestPreCompactTerminal:
    """Verify precompact hook produces no noise entries in daily log."""

    def test_no_compaction_noise(self, term, save_terminal_output):
        """hook-precompact does not write 'compacted' entries to daily log."""
        term.set_date("2026-03-15")
        term.type(
            'echo \'{"trigger":"test","transcript_path":""}\''
            " | python -m keephive hook-precompact; echo"
        )
        if term.file_exists("daily/2026-03-15.md"):
            content = term.read_file("daily/2026-03-15.md")
            assert "compacted" not in content.lower()
        save_terminal_output("precompact/no_compaction_noise", term)

    def test_no_session_noise(self, term):
        """hook-precompact does not write session entries to daily log."""
        term.set_date("2026-03-16")
        term.type(
            'echo \'{"trigger":"test","transcript_path":""}\''
            " | python -m keephive hook-precompact; echo"
        )
        if term.file_exists("daily/2026-03-16.md"):
            content = term.read_file("daily/2026-03-16.md")
            assert "session [" not in content

    def test_no_crash_empty_transcript(self, term):
        """hook-precompact handles empty transcript path gracefully."""
        screen = term.type(
            'echo \'{"trigger":"test","transcript_path":""}\''
            " | python -m keephive hook-precompact; echo"
        )
        _hook_lacks(screen, "Traceback")


# ============================================================
#  Category 5: SessionStart Context Injection
# ============================================================


@pytest.mark.terminal
class TestSessionStartTerminal:
    """Verify hook-sessionstart context includes relevant hive state."""

    def test_includes_open_todos(self, term, save_terminal_output):
        """SessionStart context includes TODO text from daily log."""
        term.type("python -m keephive t 'Deploy staging environment to AWS'")
        screen = term.type(
            'echo \'{"source":"test","cwd":"/test/project"}\''
            " | python -m keephive hook-sessionstart; echo"
        )
        _hook_has(screen, "Deploy staging")
        save_terminal_output("sessionstart/includes_todos", term)

    def test_includes_stale_warning(self, term, save_terminal_output):
        """SessionStart warns about stale facts in working memory."""
        (term.hive_home / "working" / "memory.md").write_text(
            "# Working Memory\n\n- FACT: ancient data [verified:2020-01-01]\n"
        )
        screen = term.type(
            'echo \'{"source":"test","cwd":"/test/project"}\''
            " | python -m keephive hook-sessionstart; echo"
        )
        _hook_has(screen, "unverified 30+ days")
        save_terminal_output("sessionstart/stale_warning", term)

    def test_includes_working_memory(self, term):
        """SessionStart injects verified facts from working memory."""
        (term.hive_home / "working" / "memory.md").write_text(
            "# Working Memory\n\n- FACT: keephive uses Pydantic [verified:2026-02-01]\n"
        )
        screen = term.type(
            'echo \'{"source":"test","cwd":"/test/project"}\''
            " | python -m keephive hook-sessionstart; echo"
        )
        _hook_has(screen, "keephive uses Pydantic")


# ============================================================
#  Category 6: Cross-Day TODO Lifecycle
# ============================================================


@pytest.mark.terminal
class TestCrossDayTodoLifecycle:
    """Verify TODO behavior across multiple days with time travel."""

    def test_todo_persists_10_days(self, term, save_terminal_output):
        """TODO created on day 1 is visible and completable on day 10."""
        term.set_date("2026-04-01")
        term.type("python -m keephive t 'Complete API documentation for v2'")

        term.set_date("2026-04-05")
        term.type("python -m keephive todo").has("API documentation")

        term.set_date("2026-04-10")
        term.type("python -m keephive todo").has("API documentation")
        term.type("python -m keephive td 'API documentation'").has("Completed")
        term.type("python -m keephive todo").has("No open TODOs")
        save_terminal_output("todo_lifecycle/persists_10_days", term)

    def test_done_entry_in_daily_log(self, term):
        """Marking TODO done writes DONE entry to daily log."""
        term.set_date("2026-04-15")
        term.type("python -m keephive t 'Write integration test suite'")
        term.type("python -m keephive td 'integration test'")
        content = term.read_file("daily/2026-04-15.md")
        assert "DONE" in content
        assert "integration test" in content.lower()

    def test_nudge_cites_oldest_todo(self, term, save_terminal_output):
        """Nudge state machine shows the oldest open TODO first."""
        term.set_date("2026-05-01")
        term.type("python -m keephive t 'First oldest task to complete'")

        term.set_date("2026-05-05")
        term.type("python -m keephive t 'Second newer task to handle'")

        counter_file = term.hive_home / ".tool-counter"
        counter_file.write_text(json.dumps({"count": 4, "session_id": "test"}))

        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "First oldest task")
        save_terminal_output("todo_lifecycle/nudge_cites_oldest", term)

    def test_todo_done_then_nudge_falls_through(self, term, save_terminal_output):
        """After completing all TODOs, nudge falls to next priority."""
        term.set_date("2026-05-10")
        term.type("python -m keephive t 'Only task for nudge test'")

        # Nudge with TODO present
        counter_file = term.hive_home / ".tool-counter"
        counter_file.write_text(json.dumps({"count": 4, "session_id": "test"}))
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "Only task for nudge test")

        # Mark done
        term.type("python -m keephive td 'Only task'")

        # Nudge should now fall through (no TODOs, no stale facts, no pending)
        counter_file.write_text(json.dumps({"count": 4, "session_id": "test"}))
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_lacks(screen, "Only task for nudge test")
        save_terminal_output("todo_lifecycle/done_then_fallthrough", term)


# ============================================================
#  Category 7: Profile Isolation
# ============================================================


@pytest.mark.terminal
class TestProfileIsolation:
    """Verify nudge and TODO state is isolated per HIVE_HOME."""

    def test_separate_profiles_separate_nudges(self, term, save_terminal_output):
        """Profile A's TODOs do not appear in Profile B's nudges."""
        # Profile A: add TODO
        term.type("python -m keephive t 'Profile A unique migration task'")
        counter_file = term.hive_home / ".tool-counter"
        counter_file.write_text(json.dumps({"count": 4, "session_id": "test"}))
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "Profile A unique migration")

        # Switch to Profile B
        alt_home = term.hive_home.parent / "hive_alt"
        for sub in [
            "working",
            "daily",
            "knowledge/guides",
            "knowledge/prompts",
            "working/notes",
            "archive",
        ]:
            (alt_home / sub).mkdir(parents=True, exist_ok=True)
        (alt_home / "working" / "memory.md").write_text("# Working Memory\n")
        (alt_home / "working" / "rules.md").write_text("# Working Rules\n")
        term._send(f"export HIVE_HOME={alt_home}")
        time.sleep(0.1)

        # Profile B counter
        alt_counter = alt_home / ".tool-counter"
        alt_counter.write_text(json.dumps({"count": 4, "session_id": "test"}))

        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        # Profile B has no TODOs -> falls through to context-specific
        _hook_lacks(screen, "Profile A unique migration")
        _hook_has(screen, "hive_remember")
        save_terminal_output("profiles/isolated_nudges", term)

    def test_separate_profiles_separate_todos(self, term, save_terminal_output):
        """TODO list is profile-scoped."""
        term.type("python -m keephive t 'Profile A database migration'")
        term.type("python -m keephive todo").has("database migration")

        # Switch profile
        alt_home = term.hive_home.parent / "hive_alt2"
        for sub in [
            "working",
            "daily",
            "knowledge/guides",
            "knowledge/prompts",
            "working/notes",
            "archive",
        ]:
            (alt_home / sub).mkdir(parents=True, exist_ok=True)
        term._send(f"export HIVE_HOME={alt_home}")
        time.sleep(0.1)

        # Profile B should have no TODOs
        term.type("python -m keephive todo").has("No open TODOs").lacks("database migration")
        save_terminal_output("profiles/isolated_todos", term)


# ============================================================
#  Category 8: Edge Cases & Bad Users
# ============================================================


@pytest.mark.terminal
class TestEdgeCasesTerminal:
    """Worst paths, corrupt data, unusual input patterns."""

    def test_corrupted_counter_file(self, term):
        """Hook recovers from corrupted counter JSON."""
        (term.hive_home / ".tool-counter").write_text("{corrupted json here")
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_lacks(screen, "Traceback")

    def test_hook_with_empty_stdin(self, term):
        """Hook handles empty stdin without crashing."""
        screen = term.type("echo '' | python -m keephive hook-posttooluse; echo")
        _hook_lacks(screen, "Traceback")

    def test_hook_with_malformed_json(self, term):
        """Hook handles non-JSON stdin."""
        screen = term.type("echo 'not json at all' | python -m keephive hook-posttooluse; echo")
        _hook_lacks(screen, "Traceback")

    def test_nudge_truncates_long_todo(self, term):
        """Nudge truncates TODO text beyond 60 chars."""
        long_text = "A" * 100 + " unique_end_marker"
        # Write directly to avoid long tmux send-keys
        term.set_date("2026-06-01")
        daily = term.hive_home / "daily" / "2026-06-01.md"
        daily.write_text(f"# Daily Log: 2026-06-01\n\n- [10:00:00] TODO: {long_text}\n")
        counter_file = term.hive_home / ".tool-counter"
        counter_file.write_text(json.dumps({"count": 4, "session_id": "test"}))
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_has(screen, "...")
        _hook_lacks(screen, "unique_end_marker")

    def test_rapid_distinct_todo_adds(self, term):
        """Multiple rapid TODO additions preserve all distinct items."""
        tasks = [
            "Fix PostgreSQL connection pool exhaustion",
            "Write Redis caching integration tests",
            "Deploy Kubernetes monitoring stack",
            "Review OAuth2 token refresh logic",
            "Update GraphQL schema documentation",
        ]
        for task in tasks:
            term.type(f"python -m keephive t '{task}'")
        screen = term.type("python -m keephive todo")
        for task in tasks:
            screen.has(task)

    def test_stop_hook_empty_hive(self, term):
        """Stop hook works in completely empty hive."""
        screen = term.type('echo \'{"session_id":"test"}\' | python -m keephive hook-stop; echo')
        _hook_lacks(screen, "Traceback")

    def test_sessionstart_empty_hive(self, term):
        """SessionStart produces output even with empty hive."""
        screen = term.type(
            'echo \'{"source":"test","cwd":"/test/project"}\''
            " | python -m keephive hook-sessionstart; echo"
        )
        # Note: only check Traceback, not "Error", because sessionstart injects
        # built-in knowledge guides that contain text like "Error Ownership"
        _hook_lacks(screen, "Traceback")

    def test_precompact_malformed_input(self, term):
        """PreCompact handles malformed JSON input."""
        screen = term.type("echo 'garbage{input' | python -m keephive hook-precompact; echo")
        _hook_lacks(screen, "Traceback")

    def test_todo_done_nonexistent_no_crash(self, term):
        """Marking done on nonexistent TODO fails gracefully."""
        screen = term.type("python -m keephive td 'this does not exist at all'")
        screen.has("No matching TODO")

    def test_counter_file_missing_on_first_call(self, term):
        """Hook works when no counter file exists (first ever call)."""
        # Ensure no counter file
        counter = term.hive_home / ".tool-counter"
        if counter.exists():
            counter.unlink()
        screen = term.type(
            'echo \'{"session_id":"test","tool_name":"Edit"}\''
            " | python -m keephive hook-posttooluse; echo"
        )
        _hook_lacks(screen, "Traceback")
        # Counter file should be created
        assert counter.exists()
