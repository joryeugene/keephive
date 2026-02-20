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


def test_skip_llm_guard(hive_env, capsys):
    """HIVE_SKIP_LLM=1 causes early return without calling claude."""
    # hive_env fixture already sets HIVE_SKIP_LLM=1
    from keephive.commands.verify import cmd_verify

    cmd_verify([])
    out = capsys.readouterr().out
    assert "Skipping" in out
