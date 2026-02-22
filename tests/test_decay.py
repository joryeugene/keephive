"""Tests for storage.score_fact_decay — decay scoring with per-category thresholds.

All external dependencies (get_today, _count_fact_references, get_recall_count) are
mocked so tests are fast and deterministic.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

FIXED_TODAY = date(2026, 2, 22)


def make_score(
    fact_text: str,
    verified_date_str: str,
    *,
    refs: int = 0,
    recalls: int = 0,
) -> float:
    """Call score_fact_decay with all external reads mocked."""
    with (
        patch("keephive.storage._count_fact_references", return_value=refs),
        patch("keephive.storage.get_recall_count", return_value=recalls),
        patch("keephive.storage.get_today", return_value=FIXED_TODAY),
    ):
        from keephive.storage import score_fact_decay

        return score_fact_decay(fact_text, verified_date_str)


class TestScoreFactDecay:
    def test_brand_new_fact(self, hive_env):
        """Fact verified today scores at least recency+importance minimum."""
        score = make_score("FACT: Python is fast", FIXED_TODAY.isoformat())
        # recency=1.0*0.4 + importance=1.0*0.2 = 0.6 with 0 refs/recalls
        assert score >= 0.59

    def test_max_stale_zero_recency(self, hive_env):
        """At threshold*2 days old, recency = 0.0 → only importance contributes."""
        # FACT threshold=30, so 60 days = 2x threshold
        sixty_days_ago = (FIXED_TODAY - timedelta(days=60)).isoformat()
        score = make_score("FACT: old fact", sixty_days_ago)
        assert 0.0 <= score <= 0.21

    def test_halfway_stale(self, hive_env):
        """At exactly threshold days, recency = 0.5."""
        thirty_days_ago = (FIXED_TODAY - timedelta(days=30)).isoformat()
        score = make_score("FACT: halfway fact", thirty_days_ago)
        # recency=0.5*0.4=0.2 + importance=1.0*0.2=0.2 → ~0.4
        assert 0.35 <= score <= 0.45

    def test_refs_cap(self, hive_env):
        """References cap at 10 (ref_score=1.0); more than 10 is same as 10."""
        score_10 = make_score("FACT: fact", FIXED_TODAY.isoformat(), refs=10)
        score_20 = make_score("FACT: fact", FIXED_TODAY.isoformat(), refs=20)
        assert abs(score_10 - score_20) < 0.01

    def test_refs_increase_score(self, hive_env):
        """More references = higher score."""
        score_0 = make_score("FACT: fact", FIXED_TODAY.isoformat(), refs=0)
        score_5 = make_score("FACT: fact", FIXED_TODAY.isoformat(), refs=5)
        assert score_5 > score_0

    def test_recall_cap(self, hive_env):
        """Recall count caps at 10 (recall_score=1.0)."""
        score_10 = make_score("FACT: fact", FIXED_TODAY.isoformat(), recalls=10)
        score_20 = make_score("FACT: fact", FIXED_TODAY.isoformat(), recalls=20)
        assert abs(score_10 - score_20) < 0.01

    def test_decision_outlasts_fact(self, hive_env):
        """DECISION uses 90d threshold; at 60 days old it still has recency > 0.
        FACT uses 30d threshold; at 60 days old recency = 0."""
        sixty_days_ago = (FIXED_TODAY - timedelta(days=60)).isoformat()
        fact_score = make_score("FACT: old fact", sixty_days_ago)
        decision_score = make_score("DECISION: old decision", sixty_days_ago)
        assert decision_score > fact_score

    def test_stale_days_for_fact_categories(self, hive_env):
        """Category thresholds match documented values."""
        from keephive.storage import stale_days_for_fact

        assert stale_days_for_fact("FACT: something") == 30
        assert stale_days_for_fact("DECISION: something") == 90
        assert stale_days_for_fact("CORRECTION: something") == 60
        assert stale_days_for_fact("INSIGHT: something") == 60
        assert stale_days_for_fact("TODO: something") == 7

    def test_invalid_date_zeroes_recency(self, hive_env):
        """Non-parseable date → recency=0.0, no crash."""
        score = make_score("FACT: some fact", "not-a-date")
        # recency=0.0, importance=1.0*0.2=0.2
        assert 0.0 <= score <= 0.21

    def test_future_date_score_bounded(self, hive_env):
        """BUG-3 fix: future-dated fact must have score <= 1.0 and recency <= 1.0.

        Before the fix: max(0.0, 1.0 - negative/positive) produced recency > 1.0.
        After the fix: min(1.0, ...) caps it.
        """
        future_date = (FIXED_TODAY + timedelta(days=365)).isoformat()
        score = make_score("FACT: future fact", future_date)
        assert score <= 1.0, f"Score {score} > 1.0 for future-dated fact — BUG-3 not fixed"
        assert score >= 0.0

    def test_importance_ordering(self, hive_env):
        """CORRECTION > DECISION, DECISION >= FACT (importance weights)."""
        today = FIXED_TODAY.isoformat()
        correction = make_score("CORRECTION: fixed a bug", today)
        decision = make_score("DECISION: chose React", today)
        fact = make_score("FACT: sky is blue", today)
        assert correction >= decision
        assert decision >= fact

    @pytest.mark.parametrize(
        "days_old,prefix",
        [
            (0, "FACT"),
            (15, "FACT"),
            (60, "FACT"),
            (0, "DECISION"),
            (45, "DECISION"),
            (0, "CORRECTION"),
            (30, "CORRECTION"),
            (0, "INSIGHT"),
            (7, "TODO"),
            (200, "FACT"),
        ],
    )
    def test_score_always_in_0_to_1(self, hive_env, days_old, prefix):
        """Score is always in [0.0, 1.0] regardless of input."""
        vdate = (FIXED_TODAY - timedelta(days=days_old)).isoformat()
        score = make_score(f"{prefix}: test content here", vdate)
        assert 0.0 <= score <= 1.0, f"Score {score} out of [0,1] for {prefix} at {days_old}d"

    def test_stale_days_env_override(self, hive_env, monkeypatch):
        """HIVE_STALE_DAYS env var overrides per-category threshold for all facts."""
        monkeypatch.setenv("HIVE_STALE_DAYS", "60")
        from keephive.storage import stale_days_for_fact

        assert stale_days_for_fact("FACT: something") == 60
        assert stale_days_for_fact("DECISION: something") == 60
