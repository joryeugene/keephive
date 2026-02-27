"""Doctor command tests: health checks, display functions, HIVE_SKIP_LLM fallback."""

from __future__ import annotations

from unittest.mock import patch

from conftest import make_daily

from keephive.models import DoctorDuplicatesResponse, DuplicateGroup

# ---------------------------------------------------------------------------
# TestDoctorHealthChecks: Basic health checks (no LLM)
# ---------------------------------------------------------------------------


class TestDoctorHealthChecks:
    def test_directories_check(self, hive_env, capsys):
        """Doctor checks directory existence."""
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert len(out) > 200, f"Doctor output too short ({len(out)} chars)"
        assert "Directories" in out
        assert "Working Memory" in out
        assert "Dependencies" in out
        assert "Data Quality" in out
        assert "OK" in out
        assert "checks passed" in out or "issue(s) found" in out

    def test_memory_check(self, hive_env, capsys):
        """Doctor checks memory.md existence."""
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "Working Memory" in out
        assert "memory.md" in out
        assert "OK" in out

    def test_rules_check(self, hive_env, capsys):
        """Doctor checks rules.md existence."""
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "Working Memory" in out
        assert "rules.md" in out
        assert "OK" in out

    def test_missing_memory_reports_issue(self, hive_env, capsys):
        """Missing memory.md is flagged as an issue."""
        (hive_env / "working" / "memory.md").unlink()
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "MISSING" in out
        assert "memory.md" in out
        assert "issue(s) found" in out


# ---------------------------------------------------------------------------
# TestDoctorVersionDrift
# ---------------------------------------------------------------------------


class TestDoctorVersionDrift:
    def test_stale_install_detected(self, hive_env, capsys):
        """Doctor warns when installed version differs from dev version."""
        with patch("keephive.commands.doctor._get_installed_version", return_value="0.5.0"):
            from keephive.commands.doctor import cmd_doctor

            cmd_doctor([])
        out = capsys.readouterr().out
        assert "STALE" in out
        assert "0.5.0" in out
        assert "uv tool install" in out

    def test_matching_versions_ok(self, hive_env, capsys):
        """Doctor shows OK when installed version matches dev."""
        from keephive import __version__

        with patch("keephive.commands.doctor._get_installed_version", return_value=__version__):
            from keephive.commands.doctor import cmd_doctor

            cmd_doctor([])
        out = capsys.readouterr().out
        assert "Installed version matches" in out

    def test_no_install_no_warning(self, hive_env, capsys):
        """No warning when keephive isn't installed globally."""
        with (
            patch("keephive.commands.doctor._get_installed_version", return_value=None),
            patch("keephive.commands.doctor._check_installed_deps", return_value=[]),
        ):
            from keephive.commands.doctor import cmd_doctor

            cmd_doctor([])
        out = capsys.readouterr().out
        assert "STALE" not in out

    def test_stale_deps_detected(self, hive_env, capsys):
        """Doctor warns when tool env is missing required deps."""
        from keephive import __version__

        with (
            patch("keephive.commands.doctor._get_installed_version", return_value=__version__),
            patch("keephive.commands.doctor._check_installed_deps", return_value=["anthropic"]),
        ):
            from keephive.commands.doctor import cmd_doctor

            cmd_doctor([])
        out = capsys.readouterr().out
        assert "STALE DEPS" in out
        assert "anthropic" in out
        assert "uv tool install" in out

    def test_stale_hint_includes_no_cache(self, hive_env, capsys):
        """The stale reinstall hint must include --no-cache to actually rebuild."""
        with patch("keephive.commands.doctor._get_installed_version", return_value="0.9.0"):
            from keephive.commands.doctor import cmd_doctor

            cmd_doctor([])
        out = capsys.readouterr().out
        assert "--no-cache" in out, "Reinstall hint must include --no-cache"

    def test_content_drift_detected(self, hive_env, capsys):
        """Doctor warns when installed and dev cli.py have different content."""
        from keephive import __version__

        with (
            patch("keephive.commands.doctor._get_installed_version", return_value=__version__),
            patch("keephive.commands.doctor._check_content_drift", return_value=True),
            patch("keephive.commands.doctor._check_installed_deps", return_value=[]),
        ):
            from keephive.commands.doctor import cmd_doctor

            cmd_doctor([])
        out = capsys.readouterr().out
        assert "content" in out.lower() or "STALE" in out, "Doctor must warn about content drift"
        assert "--no-cache" in out, "Content drift hint must include --no-cache"


# ---------------------------------------------------------------------------
# TestDoctorDuplicateDetection
# ---------------------------------------------------------------------------


class TestDoctorDuplicateDetection:
    def test_deterministic_detects_duplicates(self, hive_env, capsys):
        """SequenceMatcher (HIVE_SKIP_LLM) detects similar TODOs."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] TODO: Write comprehensive tests for standup",
                "- [10:05:00] TODO: Write comprehensive tests for standup command",
            ],
        )
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "duplicate" in out.lower(), (
            f"Should detect near-identical TODOs as duplicates. Output:\n{out}"
        )
        assert len(out) > 100, f"Doctor output too short ({len(out)} chars)"

    def test_deterministic_no_duplicates(self, hive_env, capsys):
        """SequenceMatcher correctly reports no duplicates for distinct TODOs."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] TODO: Fix the login bug",
                "- [10:05:00] TODO: Deploy to production",
            ],
        )
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "No duplicate" in out
        assert len(out) > 100, f"Doctor output too short ({len(out)} chars)"

    def test_display_semantic_duplicates(self, hive_env, capsys):
        """display_duplicate_results shows duplicate groups correctly."""
        from keephive.commands.doctor import display_duplicate_results

        response = DoctorDuplicatesResponse(
            duplicate_groups=[
                DuplicateGroup(
                    entries=["Python uses GIL", "CPython has a Global Interpreter Lock"],
                    suggestion="Consolidate into: CPython uses the GIL (Global Interpreter Lock)",
                )
            ],
            orphaned_todos=[],
        )

        issue_count = display_duplicate_results(response)

        out = capsys.readouterr().out
        # Rich adds ANSI codes around keywords, check parts separately
        assert "duplicate" in out.lower()
        assert "group" in out.lower()
        assert "Consolidate" in out
        assert issue_count == 1

    def test_display_orphaned_todos(self, hive_env, capsys):
        """display_duplicate_results shows orphaned TODOs."""
        from keephive.commands.doctor import display_duplicate_results

        response = DoctorDuplicatesResponse(
            duplicate_groups=[],
            orphaned_todos=["Fix the old API endpoint"],
        )

        issue_count = display_duplicate_results(response)

        out = capsys.readouterr().out
        assert "orphaned" in out.lower()
        assert issue_count == 1

    def test_display_clean_result(self, hive_env, capsys):
        """display_duplicate_results shows clean when no issues."""
        from keephive.commands.doctor import display_duplicate_results

        response = DoctorDuplicatesResponse(
            duplicate_groups=[],
            orphaned_todos=[],
        )

        issue_count = display_duplicate_results(response)

        out = capsys.readouterr().out
        assert "No duplicate" in out
        assert issue_count == 0

    def test_display_multiple_groups_and_orphans(self, hive_env, capsys):
        """display_duplicate_results handles multiple groups + orphans."""
        from keephive.commands.doctor import display_duplicate_results

        response = DoctorDuplicatesResponse(
            duplicate_groups=[
                DuplicateGroup(entries=["A", "A copy"], suggestion="Keep A"),
                DuplicateGroup(entries=["B", "B again"], suggestion="Keep B"),
            ],
            orphaned_todos=["Old task X", "Old task Y"],
        )

        issue_count = display_duplicate_results(response)
        out = capsys.readouterr().out
        assert issue_count == 4  # 2 groups + 2 orphans
        assert "duplicate" in out.lower()
        assert "orphaned" in out.lower()
        assert "A copy" in out
        assert "B again" in out
        assert "Old task X" in out
        assert "Old task Y" in out
        assert "Keep A" in out
        assert "Keep B" in out

    def test_single_todo_handled_cleanly(self, hive_env, capsys):
        """With only 1 TODO, doctor handles it without error."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] TODO: Only one task",
            ],
        )
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "Directories" in out
        assert "Data Quality" in out
        assert len(out) > 200, f"Doctor output too short ({len(out)} chars)"


# ---------------------------------------------------------------------------
# TestDoctorStaleAndAccumulation
# ---------------------------------------------------------------------------


class TestDoctorStaleAndAccumulation:
    def test_stale_todos_detected(self, hive_env, capsys):
        """TODOs older than 7 days are flagged."""
        make_daily(
            hive_env,
            10,
            [
                "- [10:00:00] TODO: Very old task",
            ],
        )
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "older than" in out and "days" in out
        assert "1 TODO(s)" in out, f"Should report stale count. Output:\n{out}"

    def test_accumulation_warning(self, hive_env, capsys):
        """More than 10 open TODOs triggers accumulation warning."""
        entries = [f"- [10:{i:02d}:00] TODO: Task number {i}" for i in range(12)]
        make_daily(hive_env, 0, entries)

        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "12 open TODOs" in out, f"Should show count of 12 open TODOs. Output:\n{out}"
        assert "consolidating" in out.lower(), f"Should recommend consolidating. Output:\n{out}"

    def test_hygiene_corrections_surfaced(self, hive_env, capsys):
        """Doctor surfaces hygiene corrections from daily log."""
        make_daily(
            hive_env,
            0,
            [
                "- [10:00:00] CORRECTION: removed dead export from utils.py that was never imported",
            ],
        )
        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out
        assert "hygiene correction" in out.lower()
        assert "1" in out, f"Should show correction count. Output:\n{out}"
        assert len(out) > 200, f"Doctor output too short ({len(out)} chars)"


# ---------------------------------------------------------------------------
# TestDoctorModels
# ---------------------------------------------------------------------------


class TestDoctorModels:
    def test_valid_response_with_groups(self):
        data = {
            "duplicate_groups": [
                {
                    "entries": ["Task A", "Task A duplicate"],
                    "suggestion": "Keep 'Task A'",
                },
            ],
            "orphaned_todos": ["Old API task"],
        }
        resp = DoctorDuplicatesResponse.model_validate(data)
        assert len(resp.duplicate_groups) == 1
        assert len(resp.orphaned_todos) == 1

    def test_empty_response(self):
        data = {
            "duplicate_groups": [],
            "orphaned_todos": [],
        }
        resp = DoctorDuplicatesResponse.model_validate(data)
        assert len(resp.duplicate_groups) == 0
        assert len(resp.orphaned_todos) == 0

    def test_multiple_groups(self):
        data = {
            "duplicate_groups": [
                {"entries": ["A", "A copy"], "suggestion": "Keep A"},
                {"entries": ["B", "B again"], "suggestion": "Keep B"},
            ],
            "orphaned_todos": [],
        }
        resp = DoctorDuplicatesResponse.model_validate(data)
        assert len(resp.duplicate_groups) == 2


# ---------------------------------------------------------------------------
# TestHiveEntryAllowlist: allowlist coverage and prefix matching
# ---------------------------------------------------------------------------


class TestHiveEntryAllowlist:
    def test_known_entries_not_flagged(self, hive_env):
        """Every entry in _EXPECTED_HIVE_ENTRIES is accepted without warning."""
        from keephive.commands.doctor import (
            _EXPECTED_HIVE_ENTRIES,
            _check_unexpected_hive_entries,
        )

        # Create every expected entry (as files or dirs depending on name)
        for name in _EXPECTED_HIVE_ENTRIES:
            path = hive_env / name
            if path.exists():
                continue
            if "." in name and not name.startswith(".git"):
                path.write_text("")
            else:
                path.mkdir(exist_ok=True)

        unexpected = _check_unexpected_hive_entries(hive_env)
        assert unexpected == [], f"False positives from allowlist: {unexpected}"

    def test_prefix_matching_accepts_loop_files(self, hive_env):
        """Dynamic .loop-* files are accepted via prefix matching."""
        from keephive.commands.doctor import _check_unexpected_hive_entries

        (hive_env / ".loop-abc123.json").write_text("{}")
        (hive_env / ".loop-done-abc123").write_text("")
        (hive_env / ".loop-prompt-abc123.txt").write_text("")

        unexpected = _check_unexpected_hive_entries(hive_env)
        loop_names = [n for n in unexpected if n.startswith(".loop-")]
        assert loop_names == [], f"Loop files flagged as unexpected: {loop_names}"

    def test_prefix_matching_accepts_ui_queue_project(self, hive_env):
        """Dynamic .ui-queue-{project} files are accepted via prefix matching."""
        from keephive.commands.doctor import _check_unexpected_hive_entries

        (hive_env / ".ui-queue-myproject").write_text("")

        unexpected = _check_unexpected_hive_entries(hive_env)
        assert ".ui-queue-myproject" not in unexpected

    def test_truly_unknown_files_still_flagged(self, hive_env):
        """Files not in allowlist and not matching any prefix are flagged."""
        from keephive.commands.doctor import _check_unexpected_hive_entries

        (hive_env / ".mystery-file.json").write_text("{}")
        (hive_env / "random-dir").mkdir()

        unexpected = _check_unexpected_hive_entries(hive_env)
        assert ".mystery-file.json" in unexpected
        assert "random-dir" in unexpected

    def test_storage_dotfiles_covered_by_allowlist(self):
        """Every dotfile helper in storage.py produces a name in _EXPECTED_HIVE_ENTRIES."""
        from keephive.commands.doctor import _EXPECTED_HIVE_ENTRIES
        from keephive.storage import (
            auto_improve_trusted_file,
            custom_tasks_file,
            daemon_config_file,
            daemon_pid_file,
            daemon_state_file,
            dismissed_improvements_file,
            force_cli_file,
            hive_dir,
            index_file,
            kb_queue_file,
            llm_paused_file,
            pending_facts_file,
            pending_improvements_file,
            pending_rules_file,
            recall_stats_file,
            stats_file,
        )

        hd = hive_dir()
        helpers = [
            auto_improve_trusted_file,
            custom_tasks_file,
            daemon_config_file,
            daemon_pid_file,
            daemon_state_file,
            dismissed_improvements_file,
            force_cli_file,
            index_file,
            kb_queue_file,
            llm_paused_file,
            pending_facts_file,
            pending_improvements_file,
            pending_rules_file,
            recall_stats_file,
            stats_file,
        ]

        missing = []
        for fn in helpers:
            path = fn()
            if path.parent == hd:
                name = path.name
                if name not in _EXPECTED_HIVE_ENTRIES:
                    missing.append(f"{fn.__name__}() -> {name}")

        assert missing == [], f"Storage dotfiles not in allowlist: {missing}"

    def test_checkup_uses_all_daemon_tasks(self):
        """checkup Stage 2 derives its task list from daemon._TASK_DEFAULTS."""
        from keephive.commands.daemon import _TASK_DEFAULTS

        expected_tasks = set(_TASK_DEFAULTS.keys())
        assert "wander" in expected_tasks, "wander must be in _TASK_DEFAULTS"
        assert "reflect-draft" in expected_tasks, "reflect-draft must be in _TASK_DEFAULTS"
        assert len(expected_tasks) == 7, f"Expected 7 daemon tasks, got {len(expected_tasks)}"
