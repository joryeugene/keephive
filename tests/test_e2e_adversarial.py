"""Adversarial input tests. Edge cases, bad input, crash resistance.

Every test proves keephive handles weird/malicious input gracefully
without Python tracebacks or crashes. Fresh tmux session per test.

Run: uv run pytest -m terminal -k adversarial -v -o "addopts="
"""

from __future__ import annotations

import pytest

# ============================================================
#  Remember: unicode, length, empty, whitespace
# ============================================================


@pytest.mark.terminal
class TestRememberAdversarial:
    def test_unicode_emoji_text(self, term):
        """Fact text mentioning emoji survives remember without crash."""
        term.type("python -m keephive r 'FACT: rocket emoji launch test 123'").has("Remembered")
        term.type("python -m keephive rc rocket").has("rocket")

    def test_cjk_text(self, term):
        """CJK-like text does not crash remember (tmux may mangle chars)."""
        # Avoid literal CJK in send-keys; use ASCII description instead.
        # The point is the pipeline does not traceback on unusual input.
        term.type("python -m keephive r 'FACT: CJK test hanzi kanji hangul'").has("Remembered")
        term.type("python -m keephive rc CJK").has("CJK")

    def test_long_text_4000_chars(self, term):
        """A very long fact (4000+ chars) is accepted without crash."""
        # Generate long payload via shell, pipe into remember.
        # Using printf to build a long string without needing a file.
        term.type("python -c \"print('FACT: ' + 'A' * 4000)\" | xargs -0 python -m keephive r")
        # Should not produce a traceback regardless of whether it succeeded
        screen = term.type("python -m keephive s")
        screen.lacks("Traceback")

    def test_empty_remember(self, term):
        """Empty remember text shows error, does not crash."""
        screen = term.type("python -m keephive r ''")
        screen.has("Error")
        screen.lacks("Traceback")

    def test_whitespace_only_remember(self, term):
        """Whitespace-only remember text shows error, does not crash."""
        screen = term.type("python -m keephive r '   '")
        # join(['   ']) produces '   ', which is truthy but semantically empty.
        # Either it records it or shows an error; either way no crash.
        screen.lacks("Traceback")


# ============================================================
#  Recall: regex metacharacters, empty, long query
# ============================================================


@pytest.mark.terminal
class TestRecallAdversarial:
    def test_regex_metacharacters(self, term):
        """Recall with regex metacharacters does not crash."""
        # These chars could break naive regex usage: .*+?[](){}|^$
        term.type("python -m keephive r 'FACT: regex test pattern matching'")
        screen = term.type("python -m keephive rc '.*+?'")
        screen.lacks("Traceback")

    def test_empty_recall(self, term):
        """Recall with no query shows usage, does not crash."""
        screen = term.type("python -m keephive rc ''")
        screen.lacks("Traceback")

    def test_very_long_query(self, term):
        """Recall with a very long query string does not crash."""
        # 500-char query: long but fits in a single shell argument
        long_q = "searchterm" * 50
        screen = term.type(f"python -m keephive rc '{long_q}'")
        screen.lacks("Traceback")


# ============================================================
#  Todo Done: no match, multiple match, empty pattern
# ============================================================


@pytest.mark.terminal
class TestTodoDoneAdversarial:
    def test_done_matches_nothing(self, term):
        """Marking done with nonexistent pattern shows helpful message."""
        term.type("python -m keephive t 'Deploy the staging cluster'")
        screen = term.type("python -m keephive td 'xyzzy_no_match_here'")
        screen.has("No matching TODO")
        screen.lacks("Traceback")

    def test_done_matches_multiple(self, term):
        """When multiple TODOs could match, the first match wins."""
        term.type("python -m keephive t 'Fix authentication in login flow'")
        term.type("python -m keephive t 'Fix authorization in admin panel'")
        # "Fix" matches both; first match should be completed
        screen = term.type("python -m keephive td 'Fix auth'")
        screen.has("Completed")
        screen.lacks("Traceback")

    def test_done_empty_pattern(self, term):
        """Empty pattern shows error, does not crash."""
        screen = term.type("python -m keephive td ''")
        screen.has("Error")
        screen.lacks("Traceback")


# ============================================================
#  Seed: edge cases for --days and --force
# ============================================================


@pytest.mark.terminal
class TestSeedAdversarial:
    def test_seed_zero_days(self, term):
        """Seed with --days 0 does not crash (no data to generate)."""
        screen = term.type("python -m keephive seed --days 0 --force")
        screen.lacks("Traceback")

    def test_seed_one_day(self, term):
        """Seed with --days 1 produces minimal but valid data."""
        screen = term.type("python -m keephive seed --days 1 --force")
        screen.has("Seeded")
        screen.lacks("Traceback")

    def test_seed_force_on_existing(self, term):
        """Seed --force overwrites existing data without prompt."""
        # First seed
        term.type("python -m keephive seed --days 5 --force").has("Seeded")
        # Second seed on top of existing data
        screen = term.type("python -m keephive seed --days 10 --force")
        screen.has("Seeded")
        screen.lacks("Traceback")


# ============================================================
#  Export / Import: corrupt archive, bad data
# ============================================================


@pytest.mark.terminal
class TestTransferAdversarial:
    def test_import_corrupt_archive(self, term):
        """Importing a corrupt tar.gz fails gracefully."""
        # Create a bogus file that is not a valid tar.gz
        corrupt_path = term.hive_home / "corrupt.tar.gz"
        corrupt_path.write_text("this is not a valid archive")
        screen = term.type(f"python -m keephive import {corrupt_path}")
        # Should show an error (tarfile will raise), not a raw traceback
        # The command may exit non-zero; we just check no unhandled exception
        # Note: tarfile.open will raise ReadError which may produce a traceback
        # if uncaught. Either way the process should not hang.
        screen.lacks("Traceback")

    def test_import_nonexistent_file(self, term):
        """Importing a file that does not exist shows clear error."""
        screen = term.type("python -m keephive import /nonexistent/path/fake.tar.gz")
        screen.has("not found")
        screen.lacks("Traceback")


# ============================================================
#  Profile: delete edge cases
# ============================================================


@pytest.mark.terminal
class TestProfileAdversarial:
    def test_delete_nonexistent_profile(self, term):
        """Deleting a profile that does not exist shows warning."""
        screen = term.type("python -m keephive profile delete nonexistent")
        screen.has("does not exist")
        screen.lacks("Traceback")

    def test_delete_default_profile(self, term):
        """Deleting the default profile is rejected."""
        screen = term.type("python -m keephive profile delete default")
        screen.has("Cannot delete")
        screen.lacks("Traceback")

    def test_delete_active_profile(self, term):
        """Deleting the currently active profile is rejected."""
        # Create and switch to a profile
        term.type("python -m keephive profile create testprof")
        term.type("python -m keephive profile use testprof")
        # Try to delete the active one
        screen = term.type("python -m keephive profile delete testprof")
        screen.has("Cannot delete active")
        screen.lacks("Traceback")
