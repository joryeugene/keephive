"""LLM E2E tests: real claude -p calls with controlled data.

These tests are SLOW (10-30s each) and require the claude CLI.
They are skipped by default. Run with: uv run pytest -m llm -v

Each test:
1. Sets up controlled hive data
2. Makes a real LLM call via the command
3. Saves the output for later analysis
4. Asserts structural validity (not content quality, that's analyze_e2e.py's job)
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta

import pytest

pytestmark = pytest.mark.llm


def _skip_if_no_claude():
    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")


def _smart_input(prompt):
    """Shared input handler for interactive lifecycle prompts."""
    if "(w)" in prompt:
        return "w"
    if "(u)" in prompt:
        return "u"
    if "(y)" in prompt:
        return "y"
    return "y"


class TestVerifyLLM:
    def test_verify_catches_stale(self, llm_hive_env, save_e2e_output, capsys):
        """Verify detects an obviously false fact as STALE."""
        _skip_if_no_claude()

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n- Python 2.7 is the latest Python release [verified:2020-01-01]\n"
        )

        from keephive.commands.verify import cmd_verify

        cmd_verify([])
        out = capsys.readouterr().out

        save_e2e_output(
            "verify_stale",
            out,
            {
                "input_fact": "Python 2.7 is the latest Python release",
                "expected": "STALE verdict with Python 3.x correction",
            },
        )

        assert "STALE" in out
        mem = (llm_hive_env / "working" / "memory.md").read_text()
        assert "2.7" not in mem or "3." in mem

    def test_verify_confirms_valid(self, llm_hive_env, save_e2e_output, capsys):
        """Verify confirms a true fact as VALID."""
        _skip_if_no_claude()

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n"
            "- Pydantic uses BaseModel as its core class [verified:2020-01-01]\n"
        )

        from keephive.commands.verify import cmd_verify

        cmd_verify([])
        out = capsys.readouterr().out

        save_e2e_output(
            "verify_valid",
            out,
            {
                "input_fact": "Pydantic uses BaseModel as its core class",
                "expected": "VALID verdict",
            },
        )

        assert "VALID" in out or "UNCERTAIN" in out


class TestReflectLLM:
    def test_reflect_finds_patterns(self, llm_hive_env, save_e2e_output, capsys):
        """Reflect analyze detects repeated topics across days."""
        _skip_if_no_claude()

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n- keephive uses pytest for testing [verified:2026-02-15]\n"
        )

        for i in range(5):
            d = date.today() - timedelta(days=i)
            daily = llm_hive_env / "daily" / f"{d.isoformat()}.md"
            daily.write_text(
                f"# Daily Log: {d.isoformat()}\n\n"
                f"- [10:00:00] FACT: Ran testing session {i + 1}\n"
                f"- [10:05:00] TODO: Fix test flake in module {i + 1}\n"
                f"- [10:10:00] DECISION: Use pytest fixtures for test {i + 1}\n"
            )

        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["analyze"])
        out = capsys.readouterr().out

        save_e2e_output(
            "reflect_patterns",
            out,
            {
                "input": "5 daily logs with repeated testing theme",
                "expected": "Pattern identified mentioning testing",
            },
        )

        assert "pattern" in out.lower() or "Pattern" in out or "Recurring" in out


class TestDoctorLLM:
    def test_doctor_finds_duplicates(self, llm_hive_env, save_e2e_output, capsys):
        """Doctor identifies semantically duplicate TODOs."""
        _skip_if_no_claude()

        today_str = date.today().isoformat()
        daily = llm_hive_env / "daily" / f"{today_str}.md"
        daily.write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] TODO: Fix the authentication bug in login\n"
            "- [10:01:00] TODO: Resolve auth issue in the login flow\n"
            "- [10:02:00] TODO: Add unit tests for payment module\n"
            "- [10:03:00] TODO: Write tests for payment processing\n"
            "- [10:04:00] TODO: Update deployment documentation\n"
        )

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n- Testing is important [verified:2026-02-15]\n"
        )

        from keephive.commands.doctor import cmd_doctor

        cmd_doctor([])
        out = capsys.readouterr().out

        save_e2e_output(
            "doctor_duplicates",
            out,
            {
                "input_todos": [
                    "Fix the authentication bug in login",
                    "Resolve auth issue in the login flow",
                    "Add unit tests for payment module",
                    "Write tests for payment processing",
                    "Update deployment documentation",
                ],
                "expected": "At least 1 duplicate group identified",
            },
        )

        assert "duplicate" in out.lower()


class TestStandupLLM:
    def test_standup_coherent(self, llm_hive_env, save_e2e_output, capsys):
        """Standup produces coherent summary from varied activity."""
        _skip_if_no_claude()

        today_str = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        (llm_hive_env / "daily" / f"{yesterday}.md").write_text(
            f"# Daily Log: {yesterday}\n\n"
            "- [14:00:00] TODO: Implement user auth\n"
            "- [15:00:00] DECISION: Use JWT for session tokens\n"
            "- [16:00:00] DONE: Implement user auth\n"
        )

        (llm_hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [09:00:00] FACT: JWT tokens expire after 24h by default\n"
            "- [09:30:00] TODO: Add refresh token flow\n"
            "- [10:00:00] INSIGHT: Rate limiting needed before launch\n"
        )

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n- Project uses FastAPI backend [verified:2026-02-15]\n"
        )

        from keephive.commands.standup import cmd_standup

        cmd_standup([])
        out = capsys.readouterr().out

        save_e2e_output(
            "standup_coherent",
            out,
            {
                "input": "DONEs, open TODOs, decisions, insights over 2 days",
                "expected": "Summary references auth work and open items",
            },
        )

        assert "Standup" in out
        assert "Done" in out or "auth" in out.lower()


class TestAuditLLM:
    def test_audit_full_synthesis(self, llm_hive_env, save_e2e_output, capsys):
        """Audit runs all 4 LLM calls and produces synthesis."""
        _skip_if_no_claude()

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n"
            "- Python 2.7 is outdated [verified:2020-01-01]\n"
            "- keephive uses Pydantic models [verified:2026-02-15]\n"
            "- Tests should cover edge cases [verified:2026-02-14]\n"
        )

        for i in range(3):
            d = date.today() - timedelta(days=i)
            daily = llm_hive_env / "daily" / f"{d.isoformat()}.md"
            daily.write_text(
                f"# Daily Log: {d.isoformat()}\n\n"
                f"- [10:00:00] TODO: Refactor module {i + 1}\n"
                f"- [10:05:00] FACT: Found bug in handler {i + 1}\n"
                f"- [10:10:00] DECISION: Use strategy pattern for {i + 1}\n"
            )

        (llm_hive_env / "working" / "rules.md").write_text(
            "# Working Rules\n\n"
            "## When You Learn Something New\n"
            '-> hive r "FACT: what you learned"\n'
        )

        from keephive.commands.audit import cmd_audit

        cmd_audit([])
        out = capsys.readouterr().out

        save_e2e_output(
            "audit_synthesis",
            out,
            {
                "input": "Stale facts, TODOs, decisions across 3 days",
                "expected": "Connection, Tension, Play, Wild Card sections",
            },
        )

        # Audit should produce at least some of the synthesis markers
        has_synthesis = any(
            marker in out
            for marker in ["Connection", "Tension", "Play", "Wild Card", "Vault", "Cleaner"]
        )
        assert has_synthesis, f"Expected synthesis markers in output:\n{out[:500]}"


class TestReflectDraftLLM:
    def test_draft_produces_guide(self, llm_hive_env, save_e2e_output, capsys, monkeypatch):
        """rf draft gathers entries and produces a guide via LLM."""
        _skip_if_no_claude()

        # Create daily logs with testing-related entries
        for i in range(5):
            d = date.today() - timedelta(days=i)
            daily = llm_hive_env / "daily" / f"{d.isoformat()}.md"
            daily.write_text(
                f"# Daily Log: {d.isoformat()}\n\n"
                f"- [10:00:00] FACT: pytest fixtures simplify test setup {i + 1}\n"
                f"- [10:05:00] DECISION: Use conftest.py for shared test fixtures {i + 1}\n"
                f"- [10:10:00] INSIGHT: Testing edge cases catches 80% of bugs {i + 1}\n"
            )

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n- keephive uses pytest [verified:2026-02-15]\n"
        )

        # Simulate "w" to write the guide
        monkeypatch.setattr("builtins.input", lambda prompt: "w")

        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["draft", "testing"])
        out = capsys.readouterr().out

        save_e2e_output(
            "reflect_draft",
            out,
            {
                "input": "5 days of testing-related entries",
                "expected": "Guide about testing practices",
            },
        )

        # Guide should be written
        guide_path = llm_hive_env / "knowledge" / "guides" / "testing.md"
        assert guide_path.exists(), f"Guide not created. Output:\n{out[:500]}"
        content = guide_path.read_text()
        assert len(content) > 50, "Guide content too short"


class TestRecallDeepLLM:
    def test_deep_query_expands_search(self, llm_hive_env, save_e2e_output, capsys):
        """--deep flag triggers LLM query expansion."""
        _skip_if_no_claude()

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n"
            "- hive v checks stale facts against codebase [verified:2026-02-15]\n"
            "- Moat = verification, not persistence [verified:2026-02-15]\n"
        )

        from keephive.commands.remember import cmd_recall

        cmd_recall(["--deep", "verification"])
        out = capsys.readouterr().out

        save_e2e_output(
            "recall_deep",
            out,
            {
                "input": "verification (deep query expansion)",
                "expected": "Results found, possibly expanded via LLM",
            },
        )

        assert "result" in out.lower()


class TestLifecycleBestPath:
    """Full pipeline: daily logs -> analyze -> apply -> draft -> recall.

    Tests the "best path" with clean, well-categorized deployment entries
    across 5 days, plus a stale fact in memory that contradicts the logs.
    """

    def test_full_pipeline(self, llm_hive_env, save_e2e_output, capsys, monkeypatch):
        """Run analyze -> apply -> draft -> recall sequentially."""
        _skip_if_no_claude()

        from keephive.models import ReflectAnalyzeResponse

        today_str = date.today().isoformat()

        # ---- Setup: seed memory with one stale/contradicted fact ----
        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n"
            "- Deployment takes approximately 15 minutes [verified:2025-06-01]\n"
            "- Project uses Docker containers [verified:2026-02-15]\n"
        )

        # Day 1 (4 days ago)
        d1 = date.today() - timedelta(days=4)
        (llm_hive_env / "daily" / f"{d1.isoformat()}.md").write_text(
            f"# Daily Log: {d1.isoformat()}\n\n"
            "- [10:00:00] FACT: We deploy using blue-green deployment on AWS ECS\n"
            "- [10:30:00] DECISION: Chose blue-green over rolling updates to eliminate downtime\n"
        )

        # Day 2 (3 days ago)
        d2 = date.today() - timedelta(days=3)
        (llm_hive_env / "daily" / f"{d2.isoformat()}.md").write_text(
            f"# Daily Log: {d2.isoformat()}\n\n"
            "- [10:00:00] FACT: Deployment pipeline completes in about 8 minutes\n"
            "- [10:30:00] TODO: Add health check endpoint before the deploy step\n"
        )

        # Day 3 (2 days ago)
        d3 = date.today() - timedelta(days=2)
        (llm_hive_env / "daily" / f"{d3.isoformat()}.md").write_text(
            f"# Daily Log: {d3.isoformat()}\n\n"
            "- [10:00:00] FACT: Blue-green deployment reduced production downtime to zero\n"
            "- [10:30:00] INSIGHT: Canary releases would catch regressions before full promotion\n"
        )

        # Day 4 (yesterday)
        d4 = date.today() - timedelta(days=1)
        (llm_hive_env / "daily" / f"{d4.isoformat()}.md").write_text(
            f"# Daily Log: {d4.isoformat()}\n\n"
            "- [10:00:00] DECISION: Adopted 10 percent canary traffic before full promotion\n"
            "- [10:30:00] FACT: ECS service connect handles automatic load balancing\n"
        )

        # Day 5 (today)
        (llm_hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] DONE: Add health check endpoint before deploy step\n"
            "- [10:30:00] FACT: Health checks reduced failed deployments by half\n"
        )

        monkeypatch.setattr("builtins.input", _smart_input)

        # ---- Stage 1: Analyze ----
        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["analyze"])
        out_analyze = capsys.readouterr().out

        save_e2e_output(
            "lifecycle_best_analyze",
            out_analyze,
            {
                "stage": "analyze",
                "input": "5 daily logs about deployment + stale 15-min fact in memory",
            },
        )

        analyze_path = llm_hive_env / ".last-analyze.json"
        assert analyze_path.exists(), f"No .last-analyze.json created. Output:\n{out_analyze[:500]}"

        data = json.loads(analyze_path.read_text())
        response = ReflectAnalyzeResponse.model_validate(data)

        assert len(response.patterns) >= 1, (
            f"Expected >= 1 pattern (deployment topic across 5 days). "
            f"Got {len(response.patterns)}: {response.patterns}"
        )
        assert len(response.additions) >= 1, (
            f"Expected >= 1 addition (new facts not in memory). "
            f"Got {len(response.additions)}: {response.additions}"
        )
        assert len(response.contradictions) >= 1, (
            f"Expected >= 1 contradiction (15 min vs 8 min). "
            f"Got {len(response.contradictions)}: {response.contradictions}"
        )

        # ---- Stage 2: Apply ----
        initial_memory = (llm_hive_env / "working" / "memory.md").read_text()

        cmd_reflect(["apply"])
        out_apply = capsys.readouterr().out

        save_e2e_output(
            "lifecycle_best_apply",
            out_apply,
            {
                "stage": "apply",
                "additions_count": len(response.additions),
                "contradictions_count": len(response.contradictions),
            },
        )

        final_memory = (llm_hive_env / "working" / "memory.md").read_text()
        assert final_memory != initial_memory, "Memory should have changed after apply"
        assert today_str in final_memory, f"Expected today's date {today_str} in verified tags"
        # Contradiction resolved: "15 minutes" gone or "8 minutes" now present
        assert "15 minutes" not in final_memory or "8 min" in final_memory.lower(), (
            "Contradiction should be resolved: '15 minutes' replaced or '8 min' present"
        )
        assert "Done:" in out_apply, f"Apply should print summary. Output:\n{out_apply[:300]}"

        # ---- Stage 3: Draft ----
        cmd_reflect(["draft", "deployment"])
        out_draft = capsys.readouterr().out

        save_e2e_output(
            "lifecycle_best_draft",
            out_draft,
            {
                "stage": "draft",
                "topic": "deployment",
            },
        )

        guide_path = llm_hive_env / "knowledge" / "guides" / "deployment.md"
        assert guide_path.exists(), f"Guide not created. Output:\n{out_draft[:500]}"
        guide_content = guide_path.read_text()
        assert len(guide_content) > 100, f"Guide too short ({len(guide_content)} chars)"
        assert "deploy" in guide_content.lower(), "Guide should mention deployment"

        # ---- Stage 4: Recall ----
        from keephive.commands.remember import cmd_recall

        cmd_recall(["deployment"])
        out_recall = capsys.readouterr().out

        save_e2e_output(
            "lifecycle_best_recall",
            out_recall,
            {
                "stage": "recall",
                "query": "deployment",
            },
        )

        assert "result" in out_recall.lower(), (
            f"Expected recall results. Output:\n{out_recall[:500]}"
        )
        # Results should span multiple tiers (memory updated, guide created, daily logs exist)
        out_lower = out_recall.lower()
        tiers_found = sum(1 for t in ["working", "knowledge", "daily"] if t in out_lower)
        assert tiers_found >= 2, (
            f"Expected results from >= 2 tiers, found {tiers_found}. Output:\n{out_recall[:500]}"
        )


class TestSoulUpdateLLM:
    def test_soul_update_writes_valid_soul_md(self, llm_hive_env, save_e2e_output):
        """soul-update writes SOUL.md with ## Summary after real LLM call.

        Bug caught: the LLM prompt → Pydantic → file write chain was never
        verified in tests. Throttle tests exist but content was always mocked.
        """
        _skip_if_no_claude()

        today_str = date.today().isoformat()
        (llm_hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] FACT: keephive uses run_claude_pipe for all LLM calls\n"
            "- [10:05:00] DECISION: Use Pydantic models for all LLM responses\n"
            "- [10:10:00] INSIGHT: Throttling prevents LLM call spam in daemon tasks\n"
            "- [10:15:00] DONE: Fix soul-update end-to-end verification gap\n"
        )

        # Clear throttle by setting last_run to 2 hours ago
        from keephive.storage import write_daemon_state

        two_hours_ago = (datetime.now() - timedelta(hours=2)).isoformat()
        write_daemon_state({"soul-update": {"last_run": two_hours_ago}})

        from keephive.commands.daemon import _task_soul_update

        did_work = _task_soul_update()

        soul_path = llm_hive_env / "SOUL.md"
        soul_content = soul_path.read_text() if soul_path.exists() else ""
        save_e2e_output(
            "soul_update_writes",
            soul_content,
            {"expected": "SOUL.md written with ## Summary section and >100 chars"},
        )

        assert did_work is True, "soul-update returned False — check daemon.log for error"
        assert soul_path.exists(), "SOUL.md not created"
        assert len(soul_content) > 100, f"SOUL.md too short ({len(soul_content)} chars)"
        assert "## Summary" in soul_content, "SOUL.md missing ## Summary section"


class TestLifecycleWorstPath:
    """Pipeline handles noisy, contradictory data without crashing.

    Tests with duplicate TODOs, contradictory numbers, filler lines that
    are not categorized (lunch, meetings, weekend), and vague thoughts.
    """

    def test_noisy_pipeline(self, llm_hive_env, save_e2e_output, capsys, monkeypatch):
        """Noisy data: analyze -> apply -> draft. Pipeline must not crash."""
        _skip_if_no_claude()

        from keephive.models import ReflectAnalyzeResponse

        today_str = date.today().isoformat()

        # ---- Setup: memory with contradicted fact ----
        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n"
            "- API average response time is 500ms [verified:2025-01-01]\n"
            "- Team uses Slack for communication [verified:2026-02-10]\n"
        )

        # Day 1 (4 days ago): noise = lunch plans
        d1 = date.today() - timedelta(days=4)
        (llm_hive_env / "daily" / f"{d1.isoformat()}.md").write_text(
            f"# Daily Log: {d1.isoformat()}\n\n"
            "- [10:00:00] FACT: API response time is 200ms average under normal load\n"
            "someone mentioned lunch plans but nothing actionable\n"
            "- [10:30:00] TODO: Fix the login bug\n"
        )

        # Day 2 (3 days ago): noise = standup notes, contradictory spike
        d2 = date.today() - timedelta(days=3)
        (llm_hive_env / "daily" / f"{d2.isoformat()}.md").write_text(
            f"# Daily Log: {d2.isoformat()}\n\n"
            "- [10:00:00] FACT: API response time spiked to 800ms during the Redis outage\n"
            "- [10:15:00] CORRECTION: The 800ms was incident-only, baseline is still 200ms\n"
            "notes from standup meeting, nothing specific\n"
        )

        # Day 3 (2 days ago): noise = partial thought, duplicate TODO
        d3 = date.today() - timedelta(days=2)
        (llm_hive_env / "daily" / f"{d3.isoformat()}.md").write_text(
            f"# Daily Log: {d3.isoformat()}\n\n"
            "- [10:00:00] TODO: Fix the login bug\n"
            "- [10:30:00] FACT: Login bug was caused by expired JWT secret rotation\n"
            "partial thought about maybe refactoring something later\n"
        )

        # Day 4 (yesterday): noise = weekend plans
        d4 = date.today() - timedelta(days=1)
        (llm_hive_env / "daily" / f"{d4.isoformat()}.md").write_text(
            f"# Daily Log: {d4.isoformat()}\n\n"
            "- [10:00:00] DECISION: Use Redis cluster with 3 nodes for session caching\n"
            "- [10:30:00] FACT: Redis cluster has 3 nodes in production\n"
            "unrelated weekend plans discussion\n"
        )

        # Day 5 (today): duplicate TODO after DONE
        (llm_hive_env / "daily" / f"{today_str}.md").write_text(
            f"# Daily Log: {today_str}\n\n"
            "- [10:00:00] DONE: Fix the login bug\n"
            "- [10:15:00] FACT: JWT secret rotation is now fully automated\n"
            "- [10:30:00] TODO: Fix the login bug\n"
        )

        monkeypatch.setattr("builtins.input", _smart_input)

        # ---- Stage 1: Analyze ----
        from keephive.commands.reflect import cmd_reflect

        cmd_reflect(["analyze"])
        out_analyze = capsys.readouterr().out

        save_e2e_output(
            "lifecycle_worst_analyze",
            out_analyze,
            {
                "stage": "analyze",
                "input": "5 noisy daily logs with contradictions and duplicates",
            },
        )

        # Pipeline must survive noise
        analyze_path = llm_hive_env / ".last-analyze.json"
        assert analyze_path.exists(), (
            f"Pipeline crashed, no .last-analyze.json. Output:\n{out_analyze[:500]}"
        )

        data = json.loads(analyze_path.read_text())
        response = ReflectAnalyzeResponse.model_validate(data)

        # At least one field should be non-empty
        non_empty = (
            len(response.patterns) > 0
            or len(response.additions) > 0
            or len(response.contradictions) > 0
        )
        assert non_empty, (
            f"Expected at least one non-empty field. "
            f"patterns={len(response.patterns)}, "
            f"additions={len(response.additions)}, "
            f"contradictions={len(response.contradictions)}"
        )
        assert "Traceback" not in out_analyze, "Python traceback in analyze output"

        # ---- Stage 2: Apply ----
        cmd_reflect(["apply"])
        out_apply = capsys.readouterr().out

        save_e2e_output(
            "lifecycle_worst_apply",
            out_apply,
            {
                "stage": "apply",
                "additions_count": len(response.additions),
                "contradictions_count": len(response.contradictions),
            },
        )

        final_memory = (llm_hive_env / "working" / "memory.md").read_text()

        # Memory must still be parseable (not corrupted by noise)
        assert final_memory.strip(), "Memory.md is empty after apply"

        # Noise must NOT leak into memory
        mem_lower = final_memory.lower()
        assert "lunch" not in mem_lower, "Noise 'lunch' leaked into memory"
        assert "weekend" not in mem_lower, "Noise 'weekend' leaked into memory"
        assert "standup meeting" not in mem_lower, "Noise 'standup meeting' leaked into memory"

        # Apply ran to completion (handles both cases: items to review, or nothing to review)
        has_summary = "Done:" in out_apply
        has_nothing = "no additions or contradictions" in out_apply.lower()
        assert has_summary or has_nothing, (
            f"Apply should print summary or indicate nothing to review. Output:\n{out_apply[:300]}"
        )

        # ---- Stage 3: Draft ----
        cmd_reflect(["draft", "api-performance"])
        out_draft = capsys.readouterr().out

        save_e2e_output(
            "lifecycle_worst_draft",
            out_draft,
            {
                "stage": "draft",
                "topic": "api-performance",
            },
        )

        guide_path = llm_hive_env / "knowledge" / "guides" / "api-performance.md"
        if guide_path.exists():
            guide_content = guide_path.read_text()
            content_lower = guide_content.lower()
            # Guide should be topical, not noise
            assert "api" in content_lower or "response" in content_lower, (
                "Guide exists but doesn't mention API or response time"
            )
            # Noise must not appear in guide
            assert "lunch" not in content_lower, "Noise 'lunch' leaked into guide"
            assert "weekend" not in content_lower, "Noise 'weekend' leaked into guide"
            assert "meeting" not in content_lower, "Noise 'meeting' leaked into guide"
        else:
            # No entries matched the topic. Acceptable if the word matching didn't find lines.
            assert "No entries found" in out_draft, (
                f"No guide created and no 'No entries found'. Output:\n{out_draft[:500]}"
            )


# ============================================================
#  Priority 4: Verify Evidence Compounding
# ============================================================


class TestVerifyEvidenceCompounding:
    """Verify that prior verification evidence is used in subsequent runs.

    The evidence system (store_evidence / get_evidence_for_fact) tracks
    verify_count, last_reason, and source_locations. On a second verify run,
    _build_verify_prompt includes "Previous evidence (date): ..." context.
    """

    def test_second_verify_uses_prior_evidence(self, llm_hive_env, save_e2e_output, capsys):
        """Run verify twice on the same fact. Second run should have prior evidence stored."""
        _skip_if_no_claude()

        fact_text = "Pydantic uses BaseModel as its core class"
        (llm_hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- {fact_text} [verified:2020-01-01]\n"
        )

        from keephive.commands.verify import cmd_verify

        # Run 1: First verification
        cmd_verify([])
        out1 = capsys.readouterr().out

        save_e2e_output(
            "verify_evidence_run1",
            out1,
            {
                "fact": fact_text,
                "expected": "VALID verdict, evidence stored",
            },
        )

        # Check evidence was stored
        from keephive.storage import get_evidence_for_fact

        evidence = get_evidence_for_fact(fact_text)
        assert evidence is not None, "Evidence should be stored after first verify"
        assert evidence["verify_count"] >= 1, (
            f"verify_count should be >= 1, got {evidence['verify_count']}"
        )
        assert evidence.get("last_reason"), "last_reason should be non-empty"

        # The prompt for a second run would include this evidence.
        # We verify the evidence structure is correct for prompt injection.
        from keephive.commands.verify import _build_verify_prompt

        prompt = _build_verify_prompt([(1, fact_text, f"- {fact_text}")], "test")
        assert "Previous evidence" in prompt, (
            f"Second verify prompt should include prior evidence. Prompt:\n{prompt[:500]}"
        )

        save_e2e_output(
            "verify_evidence_prompt",
            prompt,
            {
                "fact": fact_text,
                "expected": "Prompt includes 'Previous evidence'",
            },
        )

    def test_evidence_tracks_correction_count(self, llm_hive_env, save_e2e_output, capsys):
        """STALE verdicts increment correction_count in evidence."""
        _skip_if_no_claude()

        fact_text = "Python 2.7 is the latest Python release"
        (llm_hive_env / "working" / "memory.md").write_text(
            f"# Working Memory\n\n- {fact_text} [verified:2020-01-01]\n"
        )

        from keephive.commands.verify import cmd_verify

        cmd_verify([])
        out = capsys.readouterr().out

        save_e2e_output(
            "verify_evidence_correction",
            out,
            {
                "fact": fact_text,
                "expected": "STALE verdict, correction_count >= 1",
            },
        )

        from keephive.storage import get_evidence_for_fact

        evidence = get_evidence_for_fact(fact_text)
        assert evidence is not None, "Evidence should be stored"
        # If the fact was found STALE (which it should be), correction_count increments
        if "STALE" in out:
            assert evidence.get("correction_count", 0) >= 1, (
                f"Expected correction_count >= 1, got {evidence}"
            )


# ============================================================
#  Priority 5: Reflect Apply Persistence
# ============================================================


class TestReflectApplyPersistence:
    """Verify that reflect apply writes correct [verified:YYYY-MM-DD] tags to memory.md."""

    def test_apply_writes_verified_tags(self, llm_hive_env, save_e2e_output, capsys, monkeypatch):
        """After reflect apply, new facts in memory.md have today's verified date tag."""
        _skip_if_no_claude()

        today_str = date.today().isoformat()

        (llm_hive_env / "working" / "memory.md").write_text(
            "# Working Memory\n\n- Old fact from long ago [verified:2025-01-01]\n"
        )

        # Create daily logs with clear, promotable facts
        for i in range(4):
            d = date.today() - timedelta(days=i)
            daily = llm_hive_env / "daily" / f"{d.isoformat()}.md"
            daily.write_text(
                f"# Daily Log: {d.isoformat()}\n\n"
                f"- [10:00:00] FACT: Docker multi-stage builds reduce image size by 60 percent\n"
                f"- [10:05:00] FACT: GitHub Actions uses YAML workflow files\n"
            )

        monkeypatch.setattr("builtins.input", _smart_input)

        from keephive.commands.reflect import cmd_reflect

        # Stage 1: Analyze
        cmd_reflect(["analyze"])
        out_analyze = capsys.readouterr().out

        analyze_path = llm_hive_env / ".last-analyze.json"
        assert analyze_path.exists(), f"No .last-analyze.json created. Output:\n{out_analyze[:500]}"

        # Stage 2: Apply
        cmd_reflect(["apply"])
        out_apply = capsys.readouterr().out

        save_e2e_output(
            "reflect_apply_persistence",
            out_apply,
            {
                "stage": "apply",
                "expected": "Memory updated with [verified:YYYY-MM-DD] tags",
            },
        )

        final_memory = (llm_hive_env / "working" / "memory.md").read_text()

        # Memory should contain today's date in verified tags
        assert today_str in final_memory, (
            f"Expected today's date {today_str} in verified tags. Memory:\n{final_memory[:500]}"
        )

        # Every verified line should have a well-formed tag
        import re

        verified_lines = [
            ln
            for ln in final_memory.splitlines()
            if ln.strip().startswith("- ") and "[verified:" in ln
        ]
        assert len(verified_lines) >= 1, "Expected at least 1 verified fact after apply"

        for ln in verified_lines:
            assert re.search(r"\[verified:\d{4}-\d{2}-\d{2}\]", ln), (
                f"Malformed verified tag in line: {ln}"
            )
