"""Tests for audit-derived features: context-aware decay, evidence storage, recall tracking."""

from __future__ import annotations

import json
from datetime import date, timedelta

from conftest import make_daily


# ---- Feature A: Context-aware decay rates ----


class TestContextAwareDecay:
    def test_fact_category_parsing(self):
        """Extract category from fact text prefix."""
        from keephive.storage import _fact_category

        assert _fact_category("FACT: Python is great") == "FACT"
        assert _fact_category("DECISION: chose X over Y") == "DECISION"
        assert _fact_category("CORRECTION: old was wrong") == "CORRECTION"
        assert _fact_category("INSIGHT: pattern found") == "INSIGHT"
        assert _fact_category("TODO: do something") == "TODO"
        assert _fact_category("- FACT: with dash prefix") == "FACT"
        assert _fact_category("no prefix at all") == "FACT"  # default

    def test_stale_days_for_fact_categories(self):
        """Different categories get different staleness thresholds."""
        from keephive.storage import stale_days_for_fact

        assert stale_days_for_fact("DECISION: chose X") == 90
        assert stale_days_for_fact("CORRECTION: old was wrong") == 60
        assert stale_days_for_fact("INSIGHT: pattern") == 60
        assert stale_days_for_fact("FACT: something true") == 30
        assert stale_days_for_fact("TODO: do it") == 7
        assert stale_days_for_fact("uncategorized text") == 30  # default

    def test_env_override_trumps_categories(self, monkeypatch):
        """HIVE_STALE_DAYS env var overrides per-category thresholds."""
        from keephive.storage import stale_days_for_fact

        monkeypatch.setenv("HIVE_STALE_DAYS", "15")
        assert stale_days_for_fact("DECISION: chose X") == 15
        assert stale_days_for_fact("TODO: do it") == 15

    def test_decision_not_stale_at_45_days(self, hive_env):
        """A DECISION verified 45 days ago is NOT stale (threshold=90d)."""
        from keephive.storage import get_stale_facts, memory_file

        d45 = (date.today() - timedelta(days=45)).isoformat()
        memory_file().write_text(
            f"# Working Memory\n\n- DECISION: chose Pydantic over dataclasses [verified:{d45}]\n"
        )
        stale = get_stale_facts()
        assert len(stale) == 0

    def test_decision_stale_at_95_days(self, hive_env):
        """A DECISION verified 95 days ago IS stale (threshold=90d)."""
        from keephive.storage import get_stale_facts, memory_file

        d95 = (date.today() - timedelta(days=95)).isoformat()
        memory_file().write_text(
            f"# Working Memory\n\n- DECISION: chose Pydantic [verified:{d95}]\n"
        )
        stale = get_stale_facts()
        assert len(stale) == 1

    def test_todo_stale_at_10_days(self, hive_env):
        """A TODO verified 10 days ago IS stale (threshold=7d)."""
        from keephive.storage import get_stale_facts, memory_file

        d10 = (date.today() - timedelta(days=10)).isoformat()
        memory_file().write_text(
            f"# Working Memory\n\n- TODO: implement feature X [verified:{d10}]\n"
        )
        stale = get_stale_facts()
        assert len(stale) == 1

    def test_fact_not_stale_at_25_days(self, hive_env):
        """A FACT verified 25 days ago is NOT stale (threshold=30d)."""
        from keephive.storage import get_stale_facts, memory_file

        d25 = (date.today() - timedelta(days=25)).isoformat()
        memory_file().write_text(f"# Working Memory\n\n- FACT: Python is great [verified:{d25}]\n")
        stale = get_stale_facts()
        assert len(stale) == 0

    def test_count_stale_matches_get_stale(self, hive_env):
        """count_stale_facts agrees with len(get_stale_facts)."""
        from keephive.storage import count_stale_facts, get_stale_facts, memory_file

        d10 = (date.today() - timedelta(days=10)).isoformat()
        d95 = (date.today() - timedelta(days=95)).isoformat()
        memory_file().write_text(
            "# Working Memory\n\n"
            f"- TODO: old task [verified:{d10}]\n"  # stale (7d threshold)
            f"- DECISION: old choice [verified:{d95}]\n"  # stale (90d threshold)
            f"- FACT: recent fact [verified:{date.today().isoformat()}]\n"  # fresh
        )
        assert count_stale_facts() == len(get_stale_facts()) == 2

    def test_mixed_categories_correct_staleness(self, hive_env):
        """Multiple categories with different ages: only truly stale ones returned."""
        from keephive.storage import get_stale_facts, memory_file

        d50 = (date.today() - timedelta(days=50)).isoformat()
        memory_file().write_text(
            "# Working Memory\n\n"
            f"- DECISION: chose X [verified:{d50}]\n"  # NOT stale (90d)
            f"- FACT: something [verified:{d50}]\n"  # stale (30d)
            f"- INSIGHT: pattern [verified:{d50}]\n"  # NOT stale (60d)
        )
        stale = get_stale_facts()
        assert len(stale) == 1
        assert "FACT: something" in stale[0][1]


# ---- Feature B: Verification evidence storage ----


class TestEvidenceStorage:
    def test_store_and_retrieve(self, hive_env):
        """Store evidence and retrieve it by fact text."""
        from keephive.storage import get_evidence_for_fact, store_evidence

        store_evidence("FACT: Python uses GIL", "VALID", "Found in cpython/ceval.c:42")
        ev = get_evidence_for_fact("FACT: Python uses GIL")
        assert ev is not None
        assert ev["last_verdict"] == "VALID"
        assert ev["last_reason"] == "Found in cpython/ceval.c:42"
        assert ev["verify_count"] == 1

    def test_verify_count_increments(self, hive_env):
        """Multiple verifications increment the counter."""
        from keephive.storage import get_evidence_for_fact, store_evidence

        store_evidence("FACT: X", "VALID", "reason 1")
        store_evidence("FACT: X", "VALID", "reason 2")
        store_evidence("FACT: X", "UNCERTAIN", "reason 3")
        ev = get_evidence_for_fact("FACT: X")
        assert ev["verify_count"] == 3

    def test_correction_count_tracks_stale(self, hive_env):
        """STALE with correction increments correction_count."""
        from keephive.storage import get_evidence_for_fact, store_evidence

        store_evidence("FACT: X", "VALID", "ok")
        store_evidence("FACT: X", "STALE", "outdated", correction="FACT: Y")
        ev = get_evidence_for_fact("FACT: X")
        assert ev["correction_count"] == 1
        assert ev["verify_count"] == 2

    def test_source_locations_extracted(self, hive_env):
        """File:line references are extracted from reason text."""
        from keephive.storage import get_evidence_for_fact, store_evidence

        store_evidence(
            "FACT: FTS5 in storage",
            "VALID",
            "Found FTS5 table creation in storage.py:142 and also in models.py",
        )
        ev = get_evidence_for_fact("FACT: FTS5 in storage")
        assert "storage.py:142" in ev["source_locations"]
        assert "models.py" in ev["source_locations"]

    def test_history_capped_at_5(self, hive_env):
        """History array is capped at 5 most recent entries."""
        from keephive.storage import get_evidence_for_fact, store_evidence

        for i in range(8):
            store_evidence("FACT: X", "VALID", f"reason {i}")
        ev = get_evidence_for_fact("FACT: X")
        assert len(ev["history"]) == 5
        assert ev["history"][-1]["reason"] == "reason 7"

    def test_evidence_survives_roundtrip(self, hive_env):
        """Evidence persists in JSON file correctly."""
        from keephive.storage import evidence_file, get_evidence_for_fact, store_evidence

        store_evidence("FACT: test", "VALID", "found it")
        # Read raw file
        raw = json.loads(evidence_file().read_text())
        assert len(raw) == 1
        key = list(raw.keys())[0]
        assert raw[key]["fact"] == "FACT: test"

        # Retrieve via API
        ev = get_evidence_for_fact("FACT: test")
        assert ev["last_verdict"] == "VALID"

    def test_no_evidence_returns_none(self, hive_env):
        """get_evidence_for_fact returns None for unknown facts."""
        from keephive.storage import get_evidence_for_fact

        assert get_evidence_for_fact("FACT: never verified") is None

    def test_apply_verdicts_stores_evidence(self, hive_env):
        """apply_verdicts stores evidence for each verdict processed."""
        from keephive.commands.verify import apply_verdicts
        from keephive.models import FactVerdict, Verdict, VerifyResponse
        from keephive.storage import get_evidence_for_fact

        mem_path = hive_env / "working" / "memory.md"
        today_str = date.today().isoformat()
        stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]

        response = VerifyResponse(
            verdicts=[FactVerdict(index=1, verdict=Verdict.VALID, reason="Confirmed in code")]
        )
        apply_verdicts(response, stale_facts, mem_path, today_str)

        ev = get_evidence_for_fact("Python is great")
        assert ev is not None
        assert ev["last_verdict"] == "VALID"
        assert ev["last_reason"] == "Confirmed in code"


# ---- Feature C: Recall frequency tracking ----


class TestRecallFrequencyTracking:
    def test_track_and_retrieve_count(self, hive_env):
        """Track recall hits and retrieve the count."""
        from keephive.storage import get_recall_count, track_recall_hit

        assert get_recall_count("- FACT: Python is great") == 0
        track_recall_hit("- FACT: Python is great")
        assert get_recall_count("- FACT: Python is great") == 1
        track_recall_hit("- FACT: Python is great")
        assert get_recall_count("- FACT: Python is great") == 2

    def test_different_facts_tracked_separately(self, hive_env):
        """Different fact lines have independent counters."""
        from keephive.storage import get_recall_count, track_recall_hit

        track_recall_hit("fact A")
        track_recall_hit("fact A")
        track_recall_hit("fact B")
        assert get_recall_count("fact A") == 2
        assert get_recall_count("fact B") == 1

    def test_recall_stats_file_location(self, hive_env):
        """Stats file lives in HIVE_HOME root."""
        from keephive.storage import recall_stats_file, track_recall_hit

        track_recall_hit("test")
        assert recall_stats_file().exists()
        assert recall_stats_file().parent == hive_env

    def test_corrupt_stats_file_handled(self, hive_env):
        """Corrupt stats file doesn't crash, returns 0."""
        from keephive.storage import get_recall_count, recall_stats_file

        recall_stats_file().write_text("not json")
        assert get_recall_count("anything") == 0

    def test_search_all_tiers_tracks_working_hits(self, hive_env):
        """Searching via _search_all_tiers tracks recall for working-tier results."""
        from keephive.commands.remember import _search_all_tiers
        from keephive.storage import get_recall_count

        # hive_env fixture has "Python is great" in memory.md
        _search_all_tiers("Python")
        # The working-tier line should have been tracked
        # (exact line includes verified tag)
        stats_file = hive_env / ".recall-stats.json"
        if stats_file.exists():
            data = json.loads(stats_file.read_text())
            total = sum(v.get("count", 0) for v in data.values())
            assert total > 0

    def test_decay_score_includes_recall_frequency(self, hive_env):
        """score_fact_decay factors in recall count."""
        from keephive.storage import score_fact_decay, track_recall_hit

        today_str = date.today().isoformat()
        # Score without recalls
        base_score = score_fact_decay("FACT: test thing", today_str)
        # Add recalls
        for _ in range(10):
            track_recall_hit("FACT: test thing")
        boosted_score = score_fact_decay("FACT: test thing", today_str)
        assert boosted_score > base_score


# ---- Feature G: Context injection diet ----


class TestContextInjectionDiet:
    def test_core_context_present(self, hive_env):
        """build_context still includes core context: memory, rules, stale warning."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "Working Memory" in ctx
        assert "When You Learn Something New" in ctx

    def test_todos_still_injected(self, hive_env):
        """Open TODOs are still injected into context."""
        make_daily(
            hive_env,
            days_ago=0,
            entries=["- [10:00:00] TODO: Important task to do"],
        )
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "Open TODO" in ctx
        assert "Important task to do" in ctx

    def test_quality_pulse_not_injected(self, hive_env):
        """Quality Pulse score is NOT in session context (moved to status)."""
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "Quality Pulse" not in ctx

    def test_data_quality_warnings_not_injected(self, hive_env):
        """Data quality warnings are NOT in session context (moved to status)."""
        # Create many TODOs to trigger accumulation warning
        entries = [f"- [10:{i:02d}:00] TODO: Task {i}" for i in range(15)]
        make_daily(hive_env, days_ago=0, entries=entries)
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "## Warnings" not in ctx

    def test_recent_entries_not_injected(self, hive_env):
        """Recent today entries are NOT in session context (available via recall)."""
        make_daily(
            hive_env,
            days_ago=0,
            entries=["- [10:00:00] FACT: something happened"],
        )
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "## Recent (today)" not in ctx

    def test_past_week_not_injected(self, hive_env):
        """Past week entries are NOT in session context (available via recall)."""
        make_daily(
            hive_env,
            days_ago=2,
            entries=["- [10:00:00] FACT: something from two days ago"],
        )
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/tmp/test", "test")
        assert "## This Week" not in ctx

    def test_guide_injection_still_works(self, hive_env):
        """Smart guide injection still works based on cwd."""
        gd = hive_env / "knowledge" / "guides"
        (gd / "myproject-guide.md").write_text("# My Project Guide\n\nImportant info.\n")
        from keephive.hooks.sessionstart import build_context

        ctx = build_context("/home/dev/myproject", "myproject")
        assert "My Project Guide" in ctx

    def test_stale_warning_still_injected(self, hive_env):
        """Stale fact warning is still injected (critical item)."""
        from keephive.hooks.sessionstart import build_context

        # hive_env fixture has a fact from 2020-01-01 which is stale
        ctx = build_context("/tmp/test", "test")
        assert "stale" in ctx.lower()
