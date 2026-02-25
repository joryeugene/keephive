"""Terminal E2E tests for hive wander CLI (commands/wander.py).

Tests list, show, seed, and empty-state behaviors via the real terminal driver.

Run: just test-e2e
Filter: just test-one "-m terminal -k TestWanderCLI"

Design notes:
  - Wander doc markdown written from Python side (term.hive_home) for determinism.
  - Filename format: YYYYMMDD-HHmmss-{slug}.md — matches list_wander_docs() parser.
  - The seed command is tested for its confirmation output, not list output
    (list only shows written docs, not pending seeds).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.terminal

# Wander doc in the exact format produced by storage.write_wander_doc().
# Using a past date so HIVE_DATE does not affect the fixture.
_WANDER_DOC_CONTENT = """\
# Wander: test pattern

**Seed source:** user-queued
**Date:** 2026-02-20 14:30
**Web search:** no

## Thinking

Some thinking about test patterns

## Connections

- **past work**: connection to past work

## Hypothesis

Unique hypothesis about pattern recognition in tests

## Open Question

What if tests could self-document?
"""

_WANDER_FILENAME = "20260220-143000-test-pattern.md"


def _write_wander_doc(hive_home: Path) -> Path:
    """Write a minimal wander doc to hive_home/wander/. Returns the path."""
    wander_dir = hive_home / "wander"
    wander_dir.mkdir(parents=True, exist_ok=True)
    path = wander_dir / _WANDER_FILENAME
    path.write_text(_WANDER_DOC_CONTENT)
    return path


@pytest.mark.terminal
class TestWanderCLI:
    def test_wander_list_shows_created_doc(self, term, save_terminal_output):
        """wander list displays hypothesis text for a manually created doc."""
        _write_wander_doc(term.hive_home)
        screen = term.type("python -m keephive wander list")
        screen.has("Unique hypothesis about pattern recognition in tests")
        screen.lacks("Traceback")
        save_terminal_output("wander/list_created_doc", term)

    def test_wander_show_displays_full_doc(self, term, save_terminal_output):
        """wander show renders full markdown including Hypothesis and Open Question."""
        _write_wander_doc(term.hive_home)
        screen = term.type("python -m keephive wander show")
        screen.has("Unique hypothesis about pattern recognition in tests")
        screen.has("What if tests could self-document?")
        screen.lacks("Traceback")
        save_terminal_output("wander/show_full_doc", term)

    def test_wander_seed_queues_and_confirms(self, term, save_terminal_output):
        """wander seed prints a Queued confirmation for the provided seed text."""
        screen = term.type("python -m keephive wander seed explore X")
        screen.has("Queued")
        screen.has("explore X")
        screen.lacks("Traceback")
        save_terminal_output("wander/seed_queued", term)

    def test_wander_empty_list_graceful(self, term, save_terminal_output):
        """wander list with no docs shows friendly empty-state message, no crash."""
        screen = term.type("python -m keephive wander list")
        screen.has_any(["No wander docs yet", "hive daemon"])
        screen.lacks("Traceback", "KeyError", "AttributeError")
        save_terminal_output("wander/empty_list", term)
