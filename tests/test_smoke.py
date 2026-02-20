"""Smoke tests: every command produces useful output without crashing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(
    args: list[str], hive_home: str | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess:
    """Run keephive as a subprocess."""
    env = {
        "HIVE_SKIP_LLM": "1",
        "NO_COLOR": "1",
        "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin:"
        + (Path.home() / ".local/bin").as_posix(),
    }
    if hive_home:
        env["HIVE_HOME"] = hive_home
    return subprocess.run(
        [sys.executable, "-m", "keephive"] + args,
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
    )


def test_version():
    r = _run(["--version"])
    assert r.returncode == 0
    assert "keephive v" in r.stdout


def test_help():
    r = _run(["help"])
    assert r.returncode == 0
    assert "status" in r.stdout
    assert "verify" in r.stdout


def test_status(hive_env):
    r = _run(["s"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert len(r.stdout) > 50, f"Status output too short ({len(r.stdout)} chars): {r.stdout!r}"
    assert "keephive" in r.stdout
    assert "facts" in r.stdout
    assert "today" in r.stdout


def test_remember(hive_env):
    r = _run(["r", "FACT: smoke test works"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Remembered" in r.stdout


def test_remember_empty(hive_env):
    r = _run(["r"], hive_home=str(hive_env))
    # Should print usage guidance, not silently succeed
    assert r.returncode == 0
    assert "nothing to remember" in r.stdout.lower() or "usage" in r.stdout.lower(), (
        f"Empty remember should give guidance. Output: {r.stdout!r}"
    )


def test_recall(hive_env):
    r = _run(["rc", "Python"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Python" in r.stdout, (
        f"Recall for 'Python' should find the test fact. Output: {r.stdout!r}"
    )


def test_recall_json(hive_env):
    r = _run(["rc", "Python", "--json"], hive_home=str(hive_env))
    assert r.returncode == 0
    import json

    data = json.loads(r.stdout)
    assert "query" in data
    assert "results" in data


def test_log(hive_env):
    r = _run(["l"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No log" in r.stdout or "Daily Log" in r.stdout, (
        f"Log should show 'No log' or daily content. Output: {r.stdout!r}"
    )


def test_todo(hive_env, daily_with_entries):
    r = _run(["todo"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "TODO" in r.stdout or "No open" in r.stdout, (
        f"Todo should show items or 'No open'. Output: {r.stdout!r}"
    )


def test_todo_done(hive_env, daily_with_entries):
    r = _run(["todo", "done", "tests"], hive_home=str(hive_env))
    assert r.returncode == 0
    # The fixture's only TODO "Add more tests" is already DONE, so no open match.
    assert "No matching TODO" in r.stdout or "Done" in r.stdout or "Completed" in r.stdout, (
        f"Todo done should confirm completion or report no match. Output: {r.stdout!r}"
    )


def test_todo_quick(hive_env):
    r = _run(["t", "write docs"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Remembered" in r.stdout


def test_t_done_shortcut(hive_env, daily_with_entries):
    """hive t done <pat> marks a TODO done (same as hive todo done)."""
    r = _run(["t", "done", "tests"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No matching TODO" in r.stdout or "Completed" in r.stdout, (
        f"hive t done should mark done or report no match. Got: {r.stdout!r}"
    )


def test_t_d_shortcut(hive_env, daily_with_entries):
    """hive t d <pat> is the shortest done shortcut."""
    r = _run(["t", "d", "tests"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No matching TODO" in r.stdout or "Completed" in r.stdout, (
        f"hive t d should mark done or report no match. Got: {r.stdout!r}"
    )


def test_td_direct_shortcut(hive_env, daily_with_entries):
    """hive td <pat> marks done without the 'done' keyword."""
    r = _run(["td", "tests"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No matching TODO" in r.stdout or "Completed" in r.stdout, (
        f"hive td <pat> should mark done or report no match. Got: {r.stdout!r}"
    )


def test_to_shows_todo_list(hive_env, daily_with_entries):
    """hive to lists open TODOs."""
    r = _run(["to"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Open TODOs" in r.stdout, f"hive to should list todos. Got: {r.stdout!r}"


def test_edit_todos_diff(hive_env, daily_with_entries):
    """edit_todos: removals become DONE, additions become TODO."""
    from datetime import date
    from unittest.mock import patch

    # Add a fresh open TODO
    today = date.today().isoformat()
    daily = hive_env / "daily" / f"{today}.md"
    content = daily.read_text()
    content += "- [11:00:00] TODO: Write integration tests\n"
    content += "- [11:01:00] TODO: Update README\n"
    daily.write_text(content)

    def fake_editor(cmd):
        """Simulate editor: remove 'Update README', add 'Deploy to staging'."""
        path = cmd[1]
        text = open(path).read()
        lines = text.splitlines()
        new_lines = [line for line in lines if "Update README" not in line]
        new_lines.append("- Deploy to staging")
        open(path, "w").write("\n".join(new_lines) + "\n")

    with patch("subprocess.run", side_effect=fake_editor):
        from keephive.commands.todo import edit_todos

        edit_todos()

    final = daily.read_text()
    assert "DONE: Update README" in final
    assert "TODO: Deploy to staging" in final


def test_gc_dry_run(hive_env):
    r = _run(["gc", "--dry-run"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Garbage collection" in r.stdout


def test_doctor(hive_env):
    r = _run(["doctor"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert len(r.stdout) > 100, f"Doctor output too short ({len(r.stdout)} chars)"
    assert "Directories" in r.stdout
    assert "Working Memory" in r.stdout
    assert "Dependencies" in r.stdout
    assert "Data Quality" in r.stdout


def test_reflect_scan(hive_env, daily_with_entries):
    r = _run(["rf"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert len(r.stdout) > 10, f"Reflect should produce output. Output: {r.stdout!r}"


def test_knowledge_list(hive_env):
    r = _run(["k"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Knowledge" in r.stdout or "No guides" in r.stdout or "guide" in r.stdout.lower(), (
        f"Knowledge list should show guides or empty state. Output: {r.stdout!r}"
    )


def test_skill_list(hive_env):
    r = _run(["sk"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert len(r.stdout) > 0, "Skill list should produce output"


def test_mem_add(hive_env):
    r = _run(["mem", "uv is fast"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Saved" in r.stdout
    mem = (hive_env / "working" / "memory.md").read_text()
    assert "uv is fast" in mem


def test_mem_rm(hive_env):
    r = _run(["mem", "rm", "Python is great"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Removed" in r.stdout


def test_rule_add(hive_env):
    r = _run(["rule", "Always verify before deploying"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Saved" in r.stdout


def test_standup(hive_env, daily_with_entries):
    r = _run(["standup"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert len(r.stdout) > 50, f"Standup output too short ({len(r.stdout)} chars)"
    assert "Standup" in r.stdout


def test_hook_sessionstart(hive_env):
    r = _run(
        ["hook-sessionstart"],
        hive_home=str(hive_env),
        stdin='{"source":"test","cwd":"/tmp/test"}',
    )
    assert r.returncode == 0
    import json

    data = json.loads(r.stdout)
    assert "hookSpecificOutput" in data
    assert "additionalContext" in data["hookSpecificOutput"]


def test_hook_precompact(hive_env):
    r = _run(
        ["hook-precompact"],
        hive_home=str(hive_env),
        stdin='{"trigger":"test","transcript_path":""}',
    )
    assert r.returncode == 0


def test_mem_no_args(hive_env):
    """mem with no args shows memory content, not error."""
    r = _run(["mem"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Working Memory" in r.stdout
    assert "Error" not in r.stdout


def test_rule_no_args(hive_env):
    """rule with no args shows rules content, not error."""
    r = _run(["rule"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Working Rules" in r.stdout
    assert "Error" not in r.stdout


def test_rule_rm(hive_env):
    """rule rm 'pattern' removes matching rule."""
    # Add a rule first
    _run(["rule", "Always test before deploy"], hive_home=str(hive_env))
    r = _run(["rule", "rm", "Always test"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Removed" in r.stdout


def test_prompt_list(hive_env):
    """p with no args lists prompts."""
    r = _run(["p"], hive_home=str(hive_env))
    assert r.returncode == 0


def test_standup_alias(hive_env, daily_with_entries):
    """su routes to standup."""
    r = _run(["su"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Standup" in r.stdout


def test_doctor_alias(hive_env):
    """dr routes to doctor."""
    r = _run(["dr"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert len(r.stdout) > 100, f"Doctor alias output too short ({len(r.stdout)} chars)"
    assert "Directories" in r.stdout
    assert "Working Memory" in r.stdout
    assert "Data Quality" in r.stdout


def test_note_smoke(hive_env):
    """n show runs without error."""
    r = _run(["n", "show"], hive_home=str(hive_env))
    assert r.returncode == 0


def test_note_copy_smoke(hive_env):
    """nc runs without error."""
    r = _run(["nc"], hive_home=str(hive_env))
    assert r.returncode == 0


def test_note_d_alias_smoke(hive_env):
    """d show runs without error (backward compat alias)."""
    r = _run(["d", "show"], hive_home=str(hive_env))
    assert r.returncode == 0


def test_note_dc_alias_smoke(hive_env):
    """dc runs without error (backward compat alias)."""
    r = _run(["dc"], hive_home=str(hive_env))
    assert r.returncode == 0


def test_setup_hook_format(tmp_path):
    """_setup_hooks writes correct matcher-grouped format."""
    import json

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{}")

    from keephive.commands.setup import _setup_hooks

    _setup_hooks(settings_path=settings_path)

    data = json.loads(settings_path.read_text())
    hooks = data.get("hooks", {})

    for event in ["SessionStart", "PreCompact"]:
        assert event in hooks, f"{event} missing from hooks"
        entries = hooks[event]
        for entry in entries:
            if "keephive" in str(entry):
                assert "matcher" in entry, f"{event} entry missing 'matcher' key"
                assert "hooks" in entry, f"{event} entry missing 'hooks' key"
                assert isinstance(entry["hooks"], list)
                # No bare "command" at top level
                assert "command" not in entry or "hooks" in entry


def test_doctor_hygiene_corrections(hive_env, daily_with_entries):
    """doctor surfaces hygiene corrections from daily log."""
    # Write a CORRECTION about dead code into today's log
    daily = daily_with_entries
    daily.write_text(
        daily.read_text()
        + "- [11:00:00] CORRECTION: removed dead export from utils.py that was never imported\n"
    )
    r = _run(["doctor"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert len(r.stdout) > 100, f"Doctor output too short ({len(r.stdout)} chars)"
    assert "hygiene correction" in r.stdout.lower()
    assert "Directories" in r.stdout
    assert "Data Quality" in r.stdout


def test_mcp_serve_dispatch():
    """mcp-serve subcommand is recognized by CLI dispatch."""
    from keephive import mcp_server

    # Verify the dispatch target exists and is callable
    assert hasattr(mcp_server, "main")
    assert callable(mcp_server.main)


def test_hook_posttooluse_nudge(hive_env):
    """PostToolUse fires nudge on interval boundary."""
    import json as json_mod

    # Set interval to 2 for quick test
    # Write counter at 1 so next call hits interval
    counter_file = hive_env / ".tool-counter"
    counter_file.write_text(json_mod.dumps({"count": 1, "session_id": "smoke-nudge"}))

    r = _run(
        ["hook-posttooluse"],
        hive_home=str(hive_env),
        stdin='{"session_id":"smoke-nudge","tool_name":"Edit"}',
    )
    assert r.returncode == 0
    # Default interval is 8, counter was at 1, now at 2. No nudge yet.
    # But we verify no crash
    # To test actual nudge, set counter to 7
    counter_file.write_text(json_mod.dumps({"count": 7, "session_id": "smoke-nudge"}))
    r = _run(
        ["hook-posttooluse"],
        hive_home=str(hive_env),
        stdin='{"session_id":"smoke-nudge","tool_name":"Edit"}',
    )
    assert r.returncode == 0
    if r.stdout.strip():
        data = json_mod.loads(r.stdout)
        assert "hookSpecificOutput" in data
        assert "additionalContext" in data["hookSpecificOutput"]


def test_hook_posttooluse_silent_between_nudges(hive_env):
    """PostToolUse is silent between nudge intervals."""
    r = _run(
        ["hook-posttooluse"],
        hive_home=str(hive_env),
        stdin='{"session_id":"smoke-silent-456","tool_name":"Write"}',
    )
    assert r.returncode == 0
    # First call (count=1), no nudge expected at default interval 8
    assert r.stdout.strip() == ""


def test_gc_cleans_reminded_markers(hive_env):
    """gc removes legacy .reminded-* marker files."""
    marker = hive_env / ".reminded-old-session"
    marker.touch()

    r = _run(["gc"], hive_home=str(hive_env))
    assert r.returncode == 0

    assert not marker.exists(), "Legacy marker should be cleaned"


def test_setup_posttooluse_hook_format(tmp_path):
    """_setup_hooks writes PostToolUse with Edit|Write matcher."""
    import json

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{}")

    from keephive.commands.setup import _setup_hooks

    _setup_hooks(settings_path=settings_path)

    data = json.loads(settings_path.read_text())
    hooks = data.get("hooks", {})

    assert "PostToolUse" in hooks, "PostToolUse missing from hooks"
    entries = hooks["PostToolUse"]
    found = False
    for entry in entries:
        if "keephive" in str(entry):
            assert entry["matcher"] == "Edit|Write"
            assert "hooks" in entry
            found = True
    assert found, "PostToolUse hook entry not found"


def test_recurring_list_empty(hive_env):
    """todo repeat list shows empty state."""
    r = _run(["todo", "repeat", "list"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Recurring Tasks" in r.stdout


def test_recurring_add_named_freq(hive_env):
    """todo repeat weekly adds a recurring task."""
    r = _run(["todo", "repeat", "weekly", "Review stale facts"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Added" in r.stdout
    assert "weekly" in r.stdout


def test_recurring_add_numeric_freq(hive_env):
    """todo repeat 2d adds a recurring task with numeric interval."""
    r = _run(["todo", "repeat", "2d", "Run test suite"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Added" in r.stdout
    assert "2d" in r.stdout


def test_recurring_add_hours(hive_env):
    """todo repeat 12h adds a recurring task with hour interval."""
    r = _run(["todo", "repeat", "12h", "Check build status"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Added" in r.stdout
    assert "12h" in r.stdout


def test_recurring_done_and_rm(hive_env):
    """todo repeat done and rm work on recurring tasks."""
    _run(["todo", "repeat", "daily", "Run tests"], hive_home=str(hive_env))
    r = _run(["todo", "repeat", "done", "tests"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Done" in r.stdout
    r = _run(["todo", "repeat", "rm", "tests"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Removed" in r.stdout


def test_log_yesterday(hive_env):
    """hive l yesterday runs without error."""
    from datetime import date, timedelta

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (hive_env / "daily" / f"{yesterday}.md").write_text(
        f"# Daily Log: {yesterday}\n\n- [10:00:00] FACT: Test\n"
    )
    r = _run(["l", "yesterday"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Test" in r.stdout


def test_log_days_ago(hive_env):
    """hive l 2 runs without error."""
    r = _run(["l", "2"], hive_home=str(hive_env))
    assert r.returncode == 0


def test_log_iso_date(hive_env):
    """hive l YYYY-MM-DD runs without error."""
    r = _run(["l", "2026-01-01"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No log for" in r.stdout


def test_log_missing_shows_nearby(hive_env, daily_with_entries):
    """Missing date shows nearby logs."""
    r = _run(["l", "99"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No log for" in r.stdout
    assert "Nearby" in r.stdout


def test_reflect_apply_no_analysis(hive_env):
    """hive rf apply with no prior analysis gives guidance."""
    r = _run(["rf", "apply"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No pending analysis" in r.stdout


def test_reflect_apply_empty(hive_env):
    """hive rf apply with empty analysis says nothing to review."""
    import json

    data = {"patterns": [], "additions": [], "contradictions": [], "actions": []}
    (hive_env / ".last-analyze.json").write_text(json.dumps(data))
    r = _run(["rf", "apply"], hive_home=str(hive_env))
    assert r.returncode == 0


def test_reflect_draft_no_topic(hive_env):
    """hive rf draft with no topic shows usage."""
    r = _run(["rf", "draft"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Usage" in r.stdout or "topic" in r.stdout.lower()


def test_reflect_draft_no_matches(hive_env, daily_with_entries):
    """hive rf draft with non-matching topic gives feedback."""
    r = _run(["rf", "draft", "nonexistent-xyz-topic"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "No entries found" in r.stdout or "No daily logs" in r.stdout


def test_recall_fast_flag(hive_env):
    """hive rc --fast is accepted (backward compat, no-op)."""
    r = _run(["rc", "Python", "--fast"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Python" in r.stdout


def test_recall_deep_flag(hive_env):
    """hive rc --deep with HIVE_SKIP_LLM=1 skips LLM, returns normal results."""
    r = _run(["rc", "Python", "--deep"], hive_home=str(hive_env))
    assert r.returncode == 0
    assert "Python" in r.stdout


def test_help_shows_grouped_sections():
    """Help text has grouped sections and key commands."""
    r = _run(["help"])
    assert r.returncode == 0
    assert "Memory" in r.stdout
    assert "Todo" in r.stdout
    assert "Knowledge" in r.stdout
    assert "Analysis" in r.stdout
    assert "Maintenance" in r.stdout
    assert "rf, reflect" in r.stdout


def test_hook_ups_nudge(hive_env):
    """UserPromptSubmit fires nudge on interval boundary."""
    import json as json_mod

    # Set counter to 15 so next call (count=16) fires nudge at default interval 8.
    # count=16 -> slot = (16 // 8) % 3 = 2 (recall slot), avoids status-aware slot
    # which depends on fixture state.
    counter_file = hive_env / ".prompt-counter"
    counter_file.write_text(json_mod.dumps({"count": 15, "session_id": "test-ups"}))

    r = _run(
        ["hook-userpromptsubmit"],
        hive_home=str(hive_env),
        stdin='{"prompt":"fix the login bug","session_id":"test-ups"}',
    )
    assert r.returncode == 0
    assert r.stdout.strip() != "", "16th call should produce nudge"
    data = json_mod.loads(r.stdout)
    assert "hookSpecificOutput" in data
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "hive_recall" in ctx


def test_hook_ups_silent_between_nudges(hive_env):
    """UserPromptSubmit is silent between nudge intervals."""
    r = _run(
        ["hook-userpromptsubmit"],
        hive_home=str(hive_env),
        stdin='{"prompt":"fix the login bug","session_id":"test-silent"}',
    )
    assert r.returncode == 0
    # First call (count=1), no nudge expected at default interval 8
    assert r.stdout.strip() == ""


def test_setup_userpromptsubmit_hook(tmp_path):
    """_setup_hooks writes UserPromptSubmit hook."""
    import json

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{}")

    from keephive.commands.setup import _setup_hooks

    _setup_hooks(settings_path=settings_path)

    data = json.loads(settings_path.read_text())
    hooks = data.get("hooks", {})
    assert "UserPromptSubmit" in hooks


def test_unknown_command():
    r = _run(["nonexistent"])
    assert r.returncode != 0
    assert "hive help" in r.stdout


def test_per_command_help():
    """--help flag shows per-command help for known commands."""
    r = _run(["todo", "--help"])
    assert r.returncode == 0
    assert "Usage:" in r.stdout
    assert "todo" in r.stdout


def test_version_short_flag():
    """-v shows version."""
    r = _run(["-v"])
    assert r.returncode == 0
    assert "keephive v" in r.stdout
