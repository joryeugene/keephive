"""Tests for hive flow — guided maintenance pipeline."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture()
def flow_env(hive_env):
    """Isolated hive env for flow tests."""
    return hive_env


class TestFlowCountHelpers:
    def test_count_pending_facts_empty(self, flow_env):
        from keephive.commands.memory import _count_pending_facts

        assert _count_pending_facts() == 0

    def test_count_pending_facts_with_items(self, flow_env):
        from keephive.commands.memory import _count_pending_facts
        from keephive.storage import hive_dir

        pf = hive_dir() / ".pending-facts.md"
        pf.write_text("- FACT: Redis is fast\n- FACT: PostgreSQL uses MVCC\n")
        assert _count_pending_facts() == 2

    def test_count_pending_facts_ignores_headers(self, flow_env):
        from keephive.commands.memory import _count_pending_facts
        from keephive.storage import hive_dir

        pf = hive_dir() / ".pending-facts.md"
        pf.write_text("# Pending Facts\n\n- FACT: one item\n\nSome notes\n")
        assert _count_pending_facts() == 1

    def test_count_pending_rules_empty(self, flow_env):
        from keephive.commands.memory import _count_pending_rules

        assert _count_pending_rules() == 0

    def test_count_pending_rules_with_items(self, flow_env):
        from keephive.commands.memory import _count_pending_rules
        from keephive.storage import hive_dir

        pr = hive_dir() / ".pending-rules.md"
        pr.write_text("- Always test before committing\n- Never use /tmp\n- Keep functions small\n")
        assert _count_pending_rules() == 3


class TestFlowSkipsEmptyQueues:
    def test_all_queues_empty_outputs_empty_messages(self, flow_env, capsys):
        """When all queues are empty, flow prints 'queue empty' for stages 2-4."""
        # cmd_verify is imported lazily inside cmd_flow; patch at source
        with patch("keephive.commands.verify.cmd_verify"):
            from keephive.commands.flow import cmd_flow

            cmd_flow(["--skip-verify"])

        captured = capsys.readouterr()
        assert "queue empty" in captured.out or "empty" in captured.out.lower()
        assert "Traceback" not in captured.out

    def test_skip_verify_flag_suppresses_stage5(self, flow_env, capsys):
        """--skip-verify suppresses stage 5 and prints skip message."""
        from keephive.commands.flow import cmd_flow

        cmd_flow(["--skip-verify"])
        captured = capsys.readouterr()
        assert "skipped" in captured.out or "skip-verify" in captured.out
        assert "Traceback" not in captured.out


class TestFlowTriage:
    def test_triage_shows_counts(self, flow_env, capsys):
        """_triage() shows all three queue counts."""
        from keephive.storage import hive_dir

        # Add some items to queues
        (hive_dir() / ".pending-facts.md").write_text("- FACT: test1\n- FACT: test2\n")
        (hive_dir() / ".pending-rules.md").write_text("- rule one\n")

        from keephive.commands.flow import _triage

        _triage()
        captured = capsys.readouterr()
        # Should show non-zero counts
        assert "2" in captured.out  # 2 pending facts
        assert "1" in captured.out  # 1 pending rule

    def test_triage_no_crash_empty(self, flow_env, capsys):
        """_triage() runs cleanly with empty queues."""
        from keephive.commands.flow import _triage

        _triage()
        captured = capsys.readouterr()
        assert "Traceback" not in captured.out
        assert "hive flow" in captured.out


class TestFlowWithPendingItems:
    def test_flow_calls_mem_review_when_facts_pending(self, flow_env):
        """When .pending-facts.md has items, flow invokes cmd_mem_review."""
        from keephive.storage import hive_dir

        (hive_dir() / ".pending-facts.md").write_text("- FACT: test fact\n")

        called = []
        with patch(
            "keephive.commands.memory.cmd_mem_review", side_effect=lambda a: called.append("mem")
        ):
            with patch("keephive.commands.verify.cmd_verify"):
                from keephive.commands.flow import cmd_flow

                cmd_flow(["--skip-verify"])

        assert "mem" in called

    def test_flow_calls_improve_when_pending(self, flow_env):
        """When improvements are pending, flow invokes cmd_improve."""
        from keephive.storage import hive_dir

        (hive_dir() / ".pending-improvements.json").write_text(
            json.dumps([{"type": "rule", "rule": "test rule", "rationale": "test"}])
        )

        called = []
        with patch(
            "keephive.commands.improve.cmd_improve", side_effect=lambda a: called.append("improve")
        ):
            with patch("keephive.commands.verify.cmd_verify"):
                from keephive.commands.flow import cmd_flow

                cmd_flow(["--skip-verify"])

        assert "improve" in called
