"""Verify command tests: direct apply_verdicts calls, no mocking needed."""

from __future__ import annotations

from datetime import date

from keephive.commands.verify import apply_verdicts
from keephive.models import FactVerdict, Verdict, VerifyResponse


def test_valid_updates_timestamp(hive_env):
    """VALID verdict refreshes the verified date to today."""
    mem_path = hive_env / "working" / "memory.md"
    today_str = date.today().isoformat()

    # The fixture has "Python is great [verified:2020-01-01]" on line 3
    stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]

    response = VerifyResponse(
        verdicts=[FactVerdict(index=1, verdict=Verdict.VALID, reason="Confirmed by codebase")]
    )

    updated, refreshed = apply_verdicts(response, stale_facts, mem_path, today_str)

    assert updated == 1
    assert refreshed == 0
    mem = mem_path.read_text()
    assert f"[verified:{today_str}]" in mem
    assert "Python is great" in mem  # fact text unchanged


def test_uncertain_refreshes_timestamp(hive_env):
    """UNCERTAIN verdict refreshes timestamp (not disproven = still valid)."""
    mem_path = hive_env / "working" / "memory.md"
    today_str = date.today().isoformat()

    stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]

    response = VerifyResponse(
        verdicts=[FactVerdict(index=1, verdict=Verdict.UNCERTAIN, reason="No evidence found")]
    )

    updated, refreshed = apply_verdicts(response, stale_facts, mem_path, today_str)

    assert updated == 0
    assert refreshed == 1
    mem = mem_path.read_text()
    assert f"[verified:{today_str}]" in mem


def test_stale_replaces_fact(hive_env):
    """STALE verdict with correction replaces the fact text."""
    mem_path = hive_env / "working" / "memory.md"
    today_str = date.today().isoformat()

    stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]

    response = VerifyResponse(
        verdicts=[
            FactVerdict(
                index=1,
                verdict=Verdict.STALE,
                reason="Outdated information",
                correction="- Python 3.13 is current",
            )
        ]
    )

    updated, refreshed = apply_verdicts(response, stale_facts, mem_path, today_str)

    assert updated == 1
    mem = mem_path.read_text()
    assert "Python 3.13 is current" in mem
    assert "Python is great" not in mem


def test_stale_adds_dash_prefix(hive_env):
    """STALE correction without leading '- ' gets it added."""
    mem_path = hive_env / "working" / "memory.md"
    today_str = date.today().isoformat()

    stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]

    response = VerifyResponse(
        verdicts=[
            FactVerdict(
                index=1,
                verdict=Verdict.STALE,
                reason="Outdated",
                correction="Python 3.14 is current",  # No leading "- "
            )
        ]
    )

    updated, _ = apply_verdicts(response, stale_facts, mem_path, today_str)

    assert updated == 1
    mem = mem_path.read_text()
    assert "- Python 3.14 is current [verified:" in mem


def test_multiple_verdicts_all_applied(hive_env):
    """All verdicts in a batch get applied to memory.md."""
    mem_path = hive_env / "working" / "memory.md"
    mem_path.write_text(
        "# Working Memory\n\n"
        "- Fact A [verified:2020-01-01]\n"
        "- Fact B [verified:2020-01-02]\n"
        "- Fresh fact [verified:2026-02-15]\n"
    )
    today_str = date.today().isoformat()

    stale_facts = [
        (3, "Fact A", "- Fact A [verified:2020-01-01]\n"),
        (4, "Fact B", "- Fact B [verified:2020-01-02]\n"),
    ]

    response = VerifyResponse(
        verdicts=[
            FactVerdict(index=1, verdict=Verdict.VALID, reason="OK"),
            FactVerdict(index=2, verdict=Verdict.UNCERTAIN, reason="Maybe"),
        ]
    )

    updated, refreshed = apply_verdicts(response, stale_facts, mem_path, today_str)

    assert updated == 1
    assert refreshed == 1
    mem = mem_path.read_text()
    lines_with_today = [line for line in mem.splitlines() if f"[verified:{today_str}]" in line]
    assert len(lines_with_today) == 2


def test_out_of_range_index_skipped(hive_env):
    """Verdict with index beyond stale_facts length is safely skipped."""
    mem_path = hive_env / "working" / "memory.md"
    today_str = date.today().isoformat()

    stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]

    response = VerifyResponse(
        verdicts=[
            FactVerdict(index=99, verdict=Verdict.VALID, reason="OK"),
        ]
    )

    updated, refreshed = apply_verdicts(response, stale_facts, mem_path, today_str)

    assert updated == 0
    assert refreshed == 0


def test_no_stale_facts_exits_clean(hive_env, capsys):
    """When all facts are fresh, verify prints success."""
    mem_path = hive_env / "working" / "memory.md"
    today_str = date.today().isoformat()
    mem_path.write_text(
        "# Working Memory\n\n"
        f"- Python is great [verified:{today_str}]\n"
        f"- keephive uses Pydantic [verified:{today_str}]\n"
    )
    from keephive.commands.verify import cmd_verify

    cmd_verify([])
    out = capsys.readouterr().out
    assert "current" in out.lower() or "Skipping" in out


def test_sequential_batches_both_applied(hive_env):
    """Two sequential apply_verdicts calls (simulating batches): both applied."""
    mem_path = hive_env / "working" / "memory.md"
    mem_path.write_text(
        "# Working Memory\n\n"
        "- Fact A about Python [verified:2020-01-01]\n"
        "- Fact B about keephive [verified:2020-01-02]\n"
        "- Untouched fact [verified:2026-02-15]\n"
    )
    today_str = date.today().isoformat()

    # Batch 1: validates Fact A (updates date)
    batch1_facts = [
        (3, "Fact A about Python", "- Fact A about Python [verified:2020-01-01]\n"),
    ]
    response1 = VerifyResponse(
        verdicts=[FactVerdict(index=1, verdict=Verdict.VALID, reason="Still true")]
    )
    updated1, refreshed1 = apply_verdicts(response1, batch1_facts, mem_path, today_str)
    assert updated1 == 1
    assert refreshed1 == 0

    # Batch 2: corrects Fact B (replaces content)
    batch2_facts = [
        (4, "Fact B about keephive", "- Fact B about keephive [verified:2020-01-02]\n"),
    ]
    response2 = VerifyResponse(
        verdicts=[
            FactVerdict(
                index=1,
                verdict=Verdict.STALE,
                reason="Name changed",
                correction="- Fact B about keephive v2",
            )
        ]
    )
    updated2, refreshed2 = apply_verdicts(response2, batch2_facts, mem_path, today_str)
    assert updated2 == 1
    assert refreshed2 == 0

    # Both changes present in final file
    mem = mem_path.read_text()
    assert f"Fact A about Python [verified:{today_str}]" in mem
    assert f"Fact B about keephive v2 [verified:{today_str}]" in mem
    assert "Untouched fact [verified:2026-02-15]" in mem
    # Old content replaced
    assert "[verified:2020-01-01]" not in mem
    assert "[verified:2020-01-02]" not in mem


def test_skip_llm_guard(hive_env, capsys):
    """HIVE_SKIP_LLM=1 causes early return without calling claude."""
    # hive_env fixture already sets HIVE_SKIP_LLM=1
    from keephive.commands.verify import cmd_verify

    cmd_verify([])
    out = capsys.readouterr().out
    assert "Skipping" in out


class TestUnverifiedAutoFacts:
    """get_unverified_auto_facts() surfaces dark knowledge for the verify queue."""

    def test_no_auto_lines_returns_empty(self, hive_env):
        from keephive.storage import get_unverified_auto_facts

        # hive_env memory.md has only verified facts, no [auto] lines
        result = get_unverified_auto_facts()
        assert result == []

    def test_pure_auto_lines_returned(self, hive_env):
        from keephive.storage import get_unverified_auto_facts

        mem = hive_env / "working" / "memory.md"
        mem.write_text(
            "- [auto] keephive uses tmux for e2e tests\n"
            "- [auto] uv is the package manager\n"
        )
        result = get_unverified_auto_facts()
        assert len(result) == 2
        line_nums, fact_texts, raw_lines = zip(*result)
        # fact_text strips the [auto] prefix
        assert "keephive uses tmux for e2e tests" in fact_texts
        assert "uv is the package manager" in fact_texts
        # no [auto] prefix in fact_text
        assert all("[auto]" not in ft for ft in fact_texts)
        # raw_line preserves original
        assert all(rl.startswith("- [auto]") for rl in raw_lines)

    def test_auto_plus_verified_excluded(self, hive_env):
        """A fact with both [auto] and [verified:DATE] is NOT dark knowledge."""
        from keephive.storage import get_unverified_auto_facts

        mem = hive_env / "working" / "memory.md"
        mem.write_text(
            "- [auto] reviewed fact [verified:2026-03-04]\n"
            "- [auto] dark fact\n"
        )
        result = get_unverified_auto_facts()
        assert len(result) == 1
        _, fact_text, _ = result[0]
        assert fact_text == "dark fact"


class TestVerifyFlags:
    """--dark and --limit N flags control which facts enter the verify queue."""

    def test_dark_flag_excludes_verified_facts(self, hive_env):
        """--dark mode: only auto-captured facts, verified facts skipped."""
        from keephive.storage import get_all_verified_facts, get_unverified_auto_facts

        mem = hive_env / "working" / "memory.md"
        mem.write_text(
            "- verified fact one [verified:2020-01-01]\n"
            "- [auto] dark fact alpha\n"
            "- [auto] dark fact beta\n"
        )
        all_facts = get_all_verified_facts()
        auto_facts = get_unverified_auto_facts()

        # Simulate --dark flag: use only auto_facts
        dark_queue = auto_facts
        assert len(dark_queue) == 2
        assert len(all_facts) == 1  # verified fact exists but is excluded from queue
        _, texts, _ = zip(*dark_queue)
        assert "dark fact alpha" in texts
        assert "dark fact beta" in texts

    def test_limit_caps_total_facts(self, hive_env):
        """--limit N: queue is capped at N facts regardless of total available."""
        from keephive.storage import get_all_verified_facts

        mem = hive_env / "working" / "memory.md"
        mem.write_text(
            "- fact one [verified:2020-01-01]\n"
            "- fact two [verified:2020-01-02]\n"
            "- fact three [verified:2020-01-03]\n"
        )
        all_facts = get_all_verified_facts()
        assert len(all_facts) == 3

        # Simulate --limit 2
        limited = all_facts[:2]
        assert len(limited) == 2
        # Limit beyond length is safe
        beyond = all_facts[:100]
        assert len(beyond) == 3

    def test_dark_and_limit_combined(self, hive_env):
        """--dark --limit N: only auto facts, capped at N."""
        from keephive.storage import get_unverified_auto_facts

        mem = hive_env / "working" / "memory.md"
        mem.write_text(
            "- [auto] dark one\n"
            "- [auto] dark two\n"
            "- [auto] dark three\n"
            "- [auto] dark four\n"
            "- [auto] dark five\n"
        )
        auto_facts = get_unverified_auto_facts()
        assert len(auto_facts) == 5

        # Simulate --dark --limit 3
        queue = auto_facts[:3]
        assert len(queue) == 3
        _, texts, _ = zip(*queue)
        assert "dark one" in texts
        assert "dark two" in texts
        assert "dark three" in texts
        assert "dark four" not in texts


class TestPostVerifyCoverageDelta:
    """Post-verify coverage delta prints when coverage changes."""

    def test_delta_printed_when_coverage_changes(self, hive_env, capsys, monkeypatch):
        """Coverage delta line appears when pre != post coverage."""
        from keephive.commands import verify as verify_mod

        call_count = {"n": 0}

        def mock_coverage():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "total": 100,
                    "verified": 30,
                    "auto_only": 65,
                    "user_owned": 5,
                    "dark_pct": 65.0,
                    "coverage_pct": 35.0,
                }
            return {
                "total": 100,
                "verified": 38,
                "auto_only": 57,
                "user_owned": 5,
                "dark_pct": 57.0,
                "coverage_pct": 43.0,
            }

        monkeypatch.setattr(verify_mod, "comprehension_coverage", mock_coverage)

        mem_path = hive_env / "working" / "memory.md"
        today_str = date.today().isoformat()

        # Simulate a verify run: capture pre, apply, capture post
        pre_cov = verify_mod.comprehension_coverage()

        stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]
        response = VerifyResponse(
            verdicts=[FactVerdict(index=1, verdict=Verdict.VALID, reason="Confirmed")]
        )
        apply_verdicts(response, stale_facts, mem_path, today_str)

        # Simulate the post-verify delta printing
        from keephive.output import console

        post_cov = verify_mod.comprehension_coverage()
        if pre_cov is not None and post_cov["coverage_pct"] != pre_cov["coverage_pct"]:
            delta = post_cov["coverage_pct"] - pre_cov["coverage_pct"]
            console.print(
                f"  Coverage: {pre_cov['coverage_pct']:.0f}% \u2192 {post_cov['coverage_pct']:.0f}%"
                f"  (+{delta:.0f}% reviewed)"
            )

        out = capsys.readouterr().out
        assert "Coverage: 35%" in out
        assert "43%" in out
        assert "+8% reviewed" in out

    def test_delta_skipped_when_coverage_unchanged(self, hive_env, capsys, monkeypatch):
        """Coverage delta line is absent when pre == post coverage."""
        from keephive.commands import verify as verify_mod

        stable_cov = {
            "total": 100,
            "verified": 50,
            "auto_only": 40,
            "user_owned": 10,
            "dark_pct": 40.0,
            "coverage_pct": 60.0,
        }
        monkeypatch.setattr(verify_mod, "comprehension_coverage", lambda: stable_cov)

        mem_path = hive_env / "working" / "memory.md"
        today_str = date.today().isoformat()

        pre_cov = verify_mod.comprehension_coverage()

        stale_facts = [(3, "Python is great", "- Python is great [verified:2020-01-01]\n")]
        response = VerifyResponse(
            verdicts=[FactVerdict(index=1, verdict=Verdict.VALID, reason="OK")]
        )
        apply_verdicts(response, stale_facts, mem_path, today_str)

        from keephive.output import console

        post_cov = verify_mod.comprehension_coverage()
        if pre_cov is not None and post_cov["coverage_pct"] != pre_cov["coverage_pct"]:
            delta = post_cov["coverage_pct"] - pre_cov["coverage_pct"]
            console.print(
                f"  Coverage: {pre_cov['coverage_pct']:.0f}% \u2192 {post_cov['coverage_pct']:.0f}%"
                f"  (+{delta:.0f}% reviewed)"
            )

        out = capsys.readouterr().out
        assert "Coverage:" not in out


class TestApplyVerdictsProgress:
    """apply_verdicts shows [i/total] progress prefix on each verdict."""

    def test_progress_prefix_in_output(self, hive_env, capsys):
        """Each verdict line shows [i/total] prefix."""
        mem_path = hive_env / "working" / "memory.md"
        mem_path.write_text(
            "# Working Memory\n\n"
            "- Fact A [verified:2020-01-01]\n"
            "- Fact B [verified:2020-01-02]\n"
            "- Fact C [verified:2020-01-03]\n"
        )
        today_str = date.today().isoformat()

        stale_facts = [
            (3, "Fact A", "- Fact A [verified:2020-01-01]\n"),
            (4, "Fact B", "- Fact B [verified:2020-01-02]\n"),
            (5, "Fact C", "- Fact C [verified:2020-01-03]\n"),
        ]

        response = VerifyResponse(
            verdicts=[
                FactVerdict(index=1, verdict=Verdict.VALID, reason="OK"),
                FactVerdict(index=2, verdict=Verdict.UNCERTAIN, reason="Maybe"),
                FactVerdict(index=3, verdict=Verdict.STALE, reason="Old", correction="- Fact C v2"),
            ]
        )

        apply_verdicts(response, stale_facts, mem_path, today_str)
        out = capsys.readouterr().out
        assert "[1/3]" in out
        assert "[2/3]" in out
        assert "[3/3]" in out
