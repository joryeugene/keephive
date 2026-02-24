"""Tests for wander seed selection, storage, and daemon task."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_daily(hive_env: Path, days_ago: int, content: str) -> None:
    """Write a daily log file for testing recurring-topic detection."""
    d = date(2026, 2, 23) - timedelta(days=days_ago)
    daily_dir = hive_env / "daily"
    daily_dir.mkdir(exist_ok=True)
    (daily_dir / f"{d.isoformat()}.md").write_text(content, encoding="utf-8")


def _write_seeds(hive_env: Path, seeds: list[str]) -> None:
    (hive_env / ".wander-seeds.json").write_text(json.dumps(seeds, indent=2), encoding="utf-8")


def _write_todo(hive_env: Path, content: str) -> None:
    (hive_env / "TODO.md").write_text(content, encoding="utf-8")


def _write_daily_with_todo(hive_env: Path, days_ago: int, todo_text: str) -> None:
    """Write a daily log containing a hive-tracked TODO entry (real format)."""
    d = date(2026, 2, 23) - timedelta(days=days_ago)
    daily_dir = hive_env / "daily"
    daily_dir.mkdir(exist_ok=True)
    (daily_dir / f"{d.isoformat()}.md").write_text(
        f"# Log {d.isoformat()}\n\n- [10:00:00] TODO: {todo_text}\n",
        encoding="utf-8",
    )


def _write_memory(hive_env: Path, content: str) -> None:
    (hive_env / "working" / "memory.md").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Seed selection
# ---------------------------------------------------------------------------


class TestSeedSelection:
    def test_user_queued_priority(self, hive_env):
        """User-queued seeds are returned FIFO, popped from the file."""
        _write_seeds(hive_env, ["topic-alpha", "topic-beta"])
        # Write memory lines to ensure cross-pollination would fire otherwise
        _write_memory(hive_env, "# Working Memory\n\n- Alpha fact here\n- Beta fact there\n")

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        assert seed == "topic-alpha"
        assert source == "user-queued"

        # File should now have only "topic-beta" remaining
        remaining = json.loads((hive_env / ".wander-seeds.json").read_text())
        assert remaining == ["topic-beta"]

    def test_cross_pollination_no_word_overlap(self, hive_env):
        """Cross-pollination pairs two memory lines with zero significant-word overlap."""
        # Clear seeds, write memory with two clearly disjoint lines
        _write_memory(
            hive_env,
            "# Working Memory\n\n"
            "- Python interpreter handles bytecode compilation process\n"
            "- Nginx load balancing distributes traffic across servers\n",
        )

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        assert source == "cross-pollination"
        assert "/" in seed  # combined as "line_a / line_b"

    def test_recurring_topic_detected(self, hive_env):
        """A word appearing in 3+ daily logs is returned as recurring-topic."""
        _write_memory(hive_env, "# Working Memory\n")
        # Write 5 daily logs all mentioning "daemon"
        word = "daemon"
        for i in range(5):
            _write_daily(
                hive_env, i, f"# Log\n\nThe {word} task ran correctly. {word} daemon check.\n"
            )

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        assert source == "recurring-topic"
        # Seed comes from the log content — any non-stopword appearing 3+ times
        assert seed is not None
        assert len(seed) >= 4

    def test_stale_todo_fallback(self, hive_env):
        """A TODO from 8 days ago in daily logs is returned as stale seed."""
        _write_memory(hive_env, "# Working Memory\n")
        # Single daily log with a TODO — no recurring-topic (only 1 log), no cross-pollination
        _write_daily_with_todo(hive_env, 8, "Refactor authentication module")

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        assert source == "stale-todo"
        assert "Refactor" in seed

    def test_no_seed_returns_none(self, hive_env):
        """Returns (None, None) when hive is empty."""
        _write_memory(hive_env, "# Working Memory\n")
        # No seeds file, no useful daily logs, no TODO

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        assert seed is None
        assert source is None

    def test_stopwords_not_returned_as_recurring_topic(self, hive_env):
        """Common stopwords must not trigger recurring-topic even if frequent."""
        _write_memory(hive_env, "# Working Memory\n")
        # All logs only contain STOPWORDS
        for i in range(5):
            _write_daily(
                hive_env, i, "# Log\n\nThis hive just will have been with that what they\n"
            )

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        # Should NOT return recurring-topic (falls through to stale-todo or None)
        assert source != "recurring-topic"

    def test_user_queued_wins_over_cross_pollination(self, hive_env):
        """User-queued always beats cross-pollination, even when memory is rich."""
        _write_seeds(hive_env, ["my-priority-seed"])
        _write_memory(
            hive_env,
            "# Working Memory\n\n"
            "- Python interpreter handles bytecode compilation process\n"
            "- Nginx load balancing distributes traffic across servers\n",
        )

        from keephive.commands.wander import select_wander_seed

        _, source = select_wander_seed(date(2026, 2, 23))
        assert source == "user-queued"

    def test_todo_md_file_not_used_as_seed_source(self, hive_env):
        """TODOs written only to TODO.md (outside hive tracking) are not returned."""
        _write_memory(hive_env, "# Working Memory\n")
        # Write a manual TODO.md — open_todos() reads daily logs, not TODO.md directly
        _write_todo(hive_env, "- [ ] Some manually-managed task\n")

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        assert source != "stale-todo"

    def test_todo_not_returned_when_younger_than_7_days(self, hive_env):
        """A TODO from 3 days ago in daily logs is not stale enough to seed wander."""
        _write_memory(hive_env, "# Working Memory\n")
        _write_daily_with_todo(hive_env, 3, "Recent task that should not trigger")

        from keephive.commands.wander import select_wander_seed

        seed, source = select_wander_seed(date(2026, 2, 23))
        assert source != "stale-todo"


# ---------------------------------------------------------------------------
# Wander storage round-trips
# ---------------------------------------------------------------------------


class TestWanderStorage:
    def test_write_then_list_roundtrip(self, hive_env):
        """Writing a doc and listing it returns the same hypothesis and question."""
        from keephive.storage import list_wander_docs, write_wander_doc

        doc = {
            "seed": "testing loop",
            "seed_source": "user-queued",
            "thinking": "Tests and code are mirrors of each other.",
            "connections": [],
            "hypothesis": "The test is the specification.",
            "question": "What if tests were written first, always?",
            "used_web_search": False,
        }
        path = write_wander_doc(doc, "testing loop")
        assert path.exists()

        docs = list_wander_docs()
        assert len(docs) == 1
        assert docs[0]["hypothesis"] == "The test is the specification."
        assert docs[0]["question"] == "What if tests were written first, always?"
        assert docs[0]["seed_source"] == "user-queued"
        assert docs[0]["filename"] == path.name

    def test_write_with_connections(self, hive_env):
        """Connections are written to the markdown and readable back."""
        from keephive.storage import read_wander_doc, write_wander_doc

        doc = {
            "seed": "memory patterns",
            "seed_source": "cross-pollination",
            "thinking": "Memory and tests are alike.",
            "connections": [
                {"memory_fragment": "Python stores state", "connection": "So do tests"},
            ],
            "hypothesis": "State is the problem.",
            "question": "What if there were no state?",
            "used_web_search": True,
        }
        path = write_wander_doc(doc, "memory patterns")
        content = read_wander_doc(path.name)

        assert "Python stores state" in content
        assert "**Web search:** yes" in content

    def test_list_empty_dir(self, hive_env):
        """list_wander_docs on empty hive returns []."""
        from keephive.storage import list_wander_docs

        result = list_wander_docs()
        assert result == []

    def test_used_web_search_flag_round_trips(self, hive_env):
        """used_web_search=True is preserved through write → list."""
        from keephive.storage import list_wander_docs, write_wander_doc

        doc = {
            "seed": "test",
            "seed_source": "stale-todo",
            "thinking": "x",
            "connections": [],
            "hypothesis": "y",
            "question": "z",
            "used_web_search": True,
        }
        write_wander_doc(doc, "test")
        docs = list_wander_docs()
        assert docs[0]["used_web_search"] is True

    def test_add_and_get_seeds_roundtrip(self, hive_env):
        """Seeds added via add_wander_seed appear in get_wander_seeds."""
        from keephive.storage import add_wander_seed, get_wander_seeds

        add_wander_seed("curious topic")
        seeds = get_wander_seeds()
        assert "curious topic" in seeds

    def test_add_seed_deduplicates(self, hive_env):
        """Adding the same seed twice results in exactly one entry."""
        from keephive.storage import add_wander_seed, get_wander_seeds

        add_wander_seed("unique topic")
        add_wander_seed("unique topic")
        seeds = get_wander_seeds()
        assert seeds.count("unique topic") == 1

    def test_pop_wander_seed_fifo(self, hive_env):
        """pop_wander_seed returns seeds in FIFO order and removes them."""
        from keephive.storage import add_wander_seed, get_wander_seeds, pop_wander_seed

        add_wander_seed("first")
        add_wander_seed("second")

        first = pop_wander_seed()
        assert first == "first"
        assert get_wander_seeds() == ["second"]

    def test_pop_wander_seed_empty_returns_none(self, hive_env):
        """pop_wander_seed on empty queue returns None without error."""
        from keephive.storage import pop_wander_seed

        result = pop_wander_seed()
        assert result is None

    def test_read_wander_doc_missing_returns_empty(self, hive_env):
        """read_wander_doc for a nonexistent file returns empty string."""
        from keephive.storage import read_wander_doc

        result = read_wander_doc("nonexistent-file.md")
        assert result == ""


# ---------------------------------------------------------------------------
# Daemon task
# ---------------------------------------------------------------------------


class TestWanderTask:
    def test_returns_false_when_no_seed(self, hive_env, monkeypatch):
        """_task_wander returns False immediately when no seed is available."""
        monkeypatch.setattr(
            "keephive.commands.wander.select_wander_seed",
            lambda _today: (None, None),
        )

        from keephive.commands.daemon import _task_wander

        result = _task_wander()
        assert result is False

    def test_returns_true_on_success(self, hive_env, monkeypatch):
        """_task_wander writes a wander doc and returns True on success."""
        from keephive.models import WanderDocument

        monkeypatch.setattr(
            "keephive.commands.wander.select_wander_seed",
            lambda _today: ("test seed topic", "user-queued"),
        )
        # run_claude_pipe is imported locally inside _task_wander — patch at source
        monkeypatch.setattr(
            "keephive.claude.run_claude_pipe",
            lambda *args, **kwargs: WanderDocument(
                seed="test seed topic",
                seed_source="user-queued",
                thinking="Free thinking about testing.",
                connections=[],
                hypothesis="Tests are specifications.",
                question="What if tests were written first?",
                used_web_search=False,
            ),
        )

        from keephive.commands.daemon import _task_wander
        from keephive.storage import list_wander_docs

        result = _task_wander()
        assert result is True

        # Wander doc should be on disk
        docs = list_wander_docs()
        assert len(docs) == 1
        assert docs[0]["hypothesis"] == "Tests are specifications."

    def test_returns_false_on_claude_pipe_error(self, hive_env, monkeypatch):
        """_task_wander returns False and does not write when LLM call fails."""
        from keephive.claude import ClaudePipeError

        monkeypatch.setattr(
            "keephive.commands.wander.select_wander_seed",
            lambda _today: ("error seed", "stale-todo"),
        )

        def _fail(*args, **kwargs):
            raise ClaudePipeError("claude timeout")

        monkeypatch.setattr(
            "keephive.claude.run_claude_pipe",
            _fail,
        )

        from keephive.commands.daemon import _task_wander
        from keephive.storage import list_wander_docs

        result = _task_wander()
        assert result is False
        # No docs written
        assert list_wander_docs() == []

    def test_daily_log_updated_on_success(self, hive_env, monkeypatch):
        """_task_wander appends to the daily log with hypothesis and question."""
        from keephive.models import WanderDocument

        monkeypatch.setattr(
            "keephive.commands.wander.select_wander_seed",
            lambda _today: ("log seed", "recurring-topic"),
        )
        monkeypatch.setattr(
            "keephive.claude.run_claude_pipe",
            lambda *args, **kwargs: WanderDocument(
                seed="log seed",
                seed_source="recurring-topic",
                thinking="Recurring patterns reveal system behavior.",
                connections=[],
                hypothesis="Patterns are the signal.",
                question="Which patterns repeat in the last 30 days?",
                used_web_search=False,
            ),
        )

        from keephive.commands.daemon import _task_wander
        from keephive.storage import hive_dir

        _task_wander()

        # Check daily log was written
        daily_files = list((hive_dir() / "daily").glob("*.md"))
        assert len(daily_files) == 1
        content = daily_files[0].read_text()
        assert "wander" in content
        assert "Patterns are the signal." in content
