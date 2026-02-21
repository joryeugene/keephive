"""Tests for centralized clock module."""

from __future__ import annotations

from datetime import date

import pytest


def test_get_today_default():
    """Without HIVE_DATE, returns real date."""
    from keephive.clock import get_today

    assert get_today() == date.today()


def test_get_today_override(monkeypatch):
    """HIVE_DATE overrides get_today()."""
    monkeypatch.setenv("HIVE_DATE", "2026-01-15")
    from keephive.clock import get_today

    assert get_today() == date(2026, 1, 15)


def test_yesterday_respects_override(monkeypatch):
    """yesterday_iso() uses overridden date."""
    monkeypatch.setenv("HIVE_DATE", "2026-03-01")
    from keephive.clock import yesterday_iso

    assert yesterday_iso() == "2026-02-28"


def test_today_iso_override(monkeypatch):
    """today_iso() returns override date."""
    monkeypatch.setenv("HIVE_DATE", "2026-06-15")
    from keephive.clock import today_iso

    assert today_iso() == "2026-06-15"


def test_get_now_uses_override_date(monkeypatch):
    """get_now() uses override date but real time."""
    monkeypatch.setenv("HIVE_DATE", "2026-01-15")
    from keephive.clock import get_now

    now = get_now()
    assert now.date() == date(2026, 1, 15)
    # Time portion should be current (within a second of now)
    import datetime as dt

    real_time = dt.datetime.now().time()
    assert abs(now.hour - real_time.hour) <= 1  # Allow hour boundary


def test_invalid_format_raises(monkeypatch):
    """Invalid HIVE_DATE raises ValueError."""
    monkeypatch.setenv("HIVE_DATE", "not-a-date")
    from keephive.clock import get_today

    with pytest.raises(ValueError):
        get_today()


def test_leap_year_override(monkeypatch):
    """Feb 29 on a leap year works."""
    monkeypatch.setenv("HIVE_DATE", "2024-02-29")
    from keephive.clock import get_today, yesterday_iso

    assert get_today() == date(2024, 2, 29)
    assert yesterday_iso() == "2024-02-28"


def test_year_boundary_override(monkeypatch):
    """Dec 31 -> Jan 1 boundary."""
    monkeypatch.setenv("HIVE_DATE", "2026-01-01")
    from keephive.clock import get_today, yesterday_iso

    assert get_today() == date(2026, 1, 1)
    assert yesterday_iso() == "2025-12-31"


def test_no_override_default(monkeypatch):
    """Unset HIVE_DATE uses real date."""
    monkeypatch.delenv("HIVE_DATE", raising=False)
    from keephive.clock import get_today, today_iso

    assert get_today() == date.today()
    assert today_iso() == date.today().isoformat()
