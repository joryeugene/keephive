"""Centralized time functions. HIVE_DATE=YYYY-MM-DD overrides for testing.

Every date.today() and datetime.now() in the codebase should go through
these functions so that HIVE_DATE can override them all at once. This
works across subprocess boundaries (tmux sessions inherit env vars).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta


def get_today() -> date:
    """Return today's date, or the HIVE_DATE override if set."""
    override = os.environ.get("HIVE_DATE")
    if override:
        return date.fromisoformat(override)
    return date.today()


def get_now() -> datetime:
    """Return current datetime. Uses HIVE_DATE for the date portion if set."""
    override = os.environ.get("HIVE_DATE")
    if override:
        d = date.fromisoformat(override)
        return datetime.combine(d, datetime.now().time())
    return datetime.now()


def today_iso() -> str:
    """Today as ISO string (YYYY-MM-DD)."""
    return get_today().isoformat()


def yesterday_iso() -> str:
    """Yesterday as ISO string (YYYY-MM-DD)."""
    return (get_today() - timedelta(days=1)).isoformat()
