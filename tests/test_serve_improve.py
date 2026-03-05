"""Tests for serve.py improve queue and rules pending panels + endpoints."""

from __future__ import annotations

from keephive.storage import (
    pending_rules_file,
    read_pending_improvements,
    rules_file,
    write_pending_improvements,
)


class TestImproveQueuePanel:
    def test_empty_queue(self, hive_env):
        from keephive.commands.serve import _get_improve_queue_data, _render_improve_queue_panel

        data = _get_improve_queue_data()
        html = _render_improve_queue_panel(data)
        assert "No pending improvements" in html

    def test_with_items(self, hive_env):
        from keephive.commands.serve import _get_improve_queue_data, _render_improve_queue_panel

        write_pending_improvements([
            {"type": "skill", "name": "test-guide", "rationale": "Makes things better"},
            {"type": "rule", "name": "new-rule", "rationale": "Prevents mistakes", "trusted": True},
        ])
        data = _get_improve_queue_data()
        html = _render_improve_queue_panel(data)
        assert "test-guide" in html
        assert "new-rule" in html
        assert "[auto]" in html
        assert "(2)" in html


class TestRulesPendingPanel:
    def test_empty(self, hive_env):
        from keephive.commands.serve import _get_rules_pending_data, _render_rules_pending_panel

        data = _get_rules_pending_data()
        html = _render_rules_pending_panel(data)
        assert "No pending rules" in html

    def test_with_pending_rules(self, hive_env):
        from keephive.commands.serve import _get_rules_pending_data, _render_rules_pending_panel

        pf = pending_rules_file()
        pf.write_text("- Always verify before committing [auto:proposed-by-kingbee]\n")
        data = _get_rules_pending_data()
        html = _render_rules_pending_panel(data)
        assert "Always verify" in html
        assert "(1)" in html

    def test_with_experiment(self, hive_env, monkeypatch):
        from keephive.commands.serve import _get_rules_pending_data, _render_rules_pending_panel

        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        rf = rules_file()
        content = rf.read_text() if rf.exists() else "# Rules\n\n"
        rf.write_text(content + "- Test rule [experiment:7d:2026-03-11]\n")
        data = _get_rules_pending_data()
        assert len(data["experiments"]) == 1
        html = _render_rules_pending_panel(data)
        assert "Active experiments" in html


class TestImproveEndpoints:
    def test_accept_improvement(self, hive_env):
        write_pending_improvements([
            {"type": "rule", "name": "test", "rule": "Always verify"},
        ])
        # Simulate accept by calling the same logic the endpoint uses
        from keephive.commands.improve import _apply_improvement

        items = read_pending_improvements()
        item = items.pop(0)
        _apply_improvement(item)
        write_pending_improvements(items)

        assert read_pending_improvements() == []
        # Rule should be queued in pending-rules
        pf = pending_rules_file()
        assert pf.exists()
        assert "Always verify" in pf.read_text()

    def test_dismiss_improvement(self, hive_env):
        from keephive.storage import append_dismissed_improvements, read_dismissed_improvements

        write_pending_improvements([
            {"type": "skill", "name": "test-guide", "content": "# Test"},
        ])
        items = read_pending_improvements()
        item = items.pop(0)
        append_dismissed_improvements([{
            "type": item.get("type", "?"),
            "name": (item.get("name") or "")[:80],
            "dismissed_at": "2026-03-04T12:00:00",
        }])
        write_pending_improvements(items)

        assert read_pending_improvements() == []
        dismissed = read_dismissed_improvements()
        assert len(dismissed) == 1
        assert dismissed[0]["name"] == "test-guide"


class TestRulesEndpoints:
    def test_accept_rule(self, hive_env):
        pf = pending_rules_file()
        pf.write_text("- Check types before committing [auto:proposed-by-kingbee]\n")

        # Simulate accept
        import re

        lines = [ln for ln in pf.read_text().splitlines() if ln.strip().startswith("- ")]
        rule_text = lines[0].lstrip("- ").strip()
        rule_text = re.sub(r"\s*\[auto:[^\]]*\]", "", rule_text).strip()

        rf = rules_file()
        if not rf.exists():
            rf.write_text("# Working Rules\n\n")
        with rf.open("a") as f:
            f.write(f"- {rule_text}\n")

        assert "Check types before committing" in rf.read_text()
        # Auto tag stripped
        assert "[auto:" not in rf.read_text().split("Check types")[1]

    def test_dismiss_rule(self, hive_env):
        pf = pending_rules_file()
        pf.write_text("- Rule A\n- Rule B\n")

        lines = [ln for ln in pf.read_text().splitlines() if ln.strip().startswith("- ")]
        lines.pop(0)  # Dismiss first
        remaining = [ln for ln in pf.read_text().splitlines() if not ln.strip().startswith("- ")]
        remaining.extend(lines)
        pf.write_text("\n".join(remaining) + "\n" if remaining else "")

        content = pf.read_text()
        assert "Rule A" not in content
        assert "Rule B" in content

    def test_try_rule(self, hive_env, monkeypatch):
        monkeypatch.setenv("HIVE_DATE", "2026-03-04")
        pf = pending_rules_file()
        pf.write_text("- Always run tests [auto:proposed-by-kingbee]\n")

        import re

        lines = [ln for ln in pf.read_text().splitlines() if ln.strip().startswith("- ")]
        rule_text = lines[0].lstrip("- ").strip()
        rule_text = re.sub(r"\s*\[auto:[^\]]*\]", "", rule_text).strip()

        from keephive.commands.memory import cmd_rule_try

        cmd_rule_try([rule_text, "--days", "7"])

        rf = rules_file()
        content = rf.read_text()
        assert "[experiment:7d:2026-03-11]" in content
        assert "Always run tests" in content


class TestSettingsViewHasNewPanels:
    def test_settings_includes_improve_and_rules(self):
        from keephive.commands.serve import VIEWS

        settings = VIEWS["settings"]
        all_panels = []
        for col in settings["cols"]:
            all_panels.extend(col)
        assert "improve-queue" in all_panels
        assert "rules-pending" in all_panels
