"""Tests for storage.read_live_sessions() and storage.is_ghost_session()."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


# ---- Helpers ----


def make_jsonl_content(n_user_messages: int, target_size_bytes: int = 0) -> bytes:
    """Build realistic JSONL bytes with n_user_messages and optional padding.

    Padding is added as a single large assistant turn at the end.
    """
    lines = []
    base_ts = "2026-02-22T10:00:00Z"

    for i in range(n_user_messages):
        user_line = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": f"user message {i}"},
            "timestamp": base_ts,
            "uuid": f"user-{i:04d}-aaaa-bbbb-cccc-dddddddddddd",
        })
        lines.append(user_line)
        asst_line = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"response {i}"}],
            },
            "timestamp": base_ts,
            "uuid": f"asst-{i:04d}-aaaa-bbbb-cccc-dddddddddddd",
        })
        lines.append(asst_line)

    content = ("\n".join(lines) + "\n").encode()

    if target_size_bytes > len(content):
        padding_needed = target_size_bytes - len(content)
        pad_line = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "x" * padding_needed}],
            },
            "timestamp": base_ts,
        })
        content = content + (pad_line + "\n").encode()

    return content


def write_session(
    cc_projects_dir: Path,
    cwd: str,
    session_id: str,
    content: bytes,
    mtime: float | None = None,
) -> Path:
    """Write JSONL to {cc_projects_dir}/{encoded_cwd}/{session_id}.jsonl."""
    encoded = cwd.replace("/", "-")
    proj_dir = cc_projects_dir / encoded
    proj_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = proj_dir / f"{session_id}.jsonl"
    jsonl_path.write_bytes(content)
    if mtime is not None:
        os.utime(jsonl_path, (mtime, mtime))
    return jsonl_path


def cc_projects_dir(hive_env: Path) -> Path:
    return Path(os.environ["HIVE_CC_PROJECTS_DIR"])


# ---- is_ghost_session ----


class TestIsGhostSession:
    def test_ghost_all_zeros_short_duration(self):
        from keephive.storage import is_ghost_session

        s = {
            "prompts": 0,
            "tools": {},
            "compacted": False,
            "started": "2026-02-22T10:00:00",
            "ended": "2026-02-22T10:00:03",
        }
        assert is_ghost_session(s) is True

    def test_not_ghost_has_prompts(self):
        from keephive.storage import is_ghost_session

        s = {
            "prompts": 1,
            "tools": {},
            "compacted": False,
            "started": "2026-02-22T10:00:00",
            "ended": "2026-02-22T10:00:03",
        }
        assert is_ghost_session(s) is False

    def test_not_ghost_has_tools(self):
        from keephive.storage import is_ghost_session

        s = {
            "prompts": 0,
            "tools": {"Edit": 1},
            "compacted": False,
            "started": "2026-02-22T10:00:00",
            "ended": "2026-02-22T10:00:03",
        }
        assert is_ghost_session(s) is False

    def test_not_ghost_compacted(self):
        from keephive.storage import is_ghost_session

        s = {
            "prompts": 0,
            "tools": {},
            "compacted": True,
            "started": "2026-02-22T10:00:00",
            "ended": "2026-02-22T10:00:03",
        }
        assert is_ghost_session(s) is False

    def test_not_ghost_long_duration(self):
        from keephive.storage import is_ghost_session

        s = {
            "prompts": 0,
            "tools": {},
            "compacted": False,
            "started": "2026-02-22T10:00:00",
            "ended": "2026-02-22T10:00:10",
        }
        assert is_ghost_session(s) is False

    def test_not_ghost_missing_timestamps(self):
        from keephive.storage import is_ghost_session

        s = {"prompts": 0, "tools": {}, "compacted": False}
        assert is_ghost_session(s) is False

    def test_not_ghost_bad_timestamp(self):
        from keephive.storage import is_ghost_session

        s = {
            "prompts": 0,
            "tools": {},
            "compacted": False,
            "started": "garbage",
            "ended": "also-garbage",
        }
        assert is_ghost_session(s) is False

    def test_boundary_exactly_5s(self):
        """Exactly 5 seconds is NOT a ghost — boundary is < 5."""
        from keephive.storage import is_ghost_session

        s = {
            "prompts": 0,
            "tools": {},
            "compacted": False,
            "started": "2026-02-22T10:00:00",
            "ended": "2026-02-22T10:00:05",
        }
        assert is_ghost_session(s) is False


# ---- read_live_sessions ----


class TestReadLiveSessions:
    def test_no_active_dirs_returns_empty(self, hive_env):
        from keephive.storage import read_live_sessions

        assert read_live_sessions(active_dirs=[]) == []

    def test_valid_session_returned(self, hive_env):
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/myproject"
        content = make_jsonl_content(n_user_messages=2)
        write_session(cc_projects_dir(hive_env), cwd, "sess-001", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert len(result) == 1
        s = result[0]
        assert s["is_live"] is True
        assert s["user_messages"] == 2
        assert s["session_id"] == "sess-001"

    def test_stale_file_filtered(self, hive_env):
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/staleproject"
        content = make_jsonl_content(n_user_messages=3)
        old_mtime = time.time() - (60 * 60)  # 60 min ago
        write_session(cc_projects_dir(hive_env), cwd, "stale-sess", content, mtime=old_mtime)

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert result == []

    def test_empty_file_skipped(self, hive_env):
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/emptyproject"
        write_session(cc_projects_dir(hive_env), cwd, "empty-sess", b"", mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert result == []

    def test_malformed_jsonl_skipped(self, hive_env):
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/badproject"
        write_session(
            cc_projects_dir(hive_env), cwd, "bad-sess",
            b"{{{not json}}}\n{also bad}\n", mtime=time.time()
        )

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert result == []

    def test_zero_user_messages_filtered(self, hive_env):
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/assistantonly"
        lines = [
            json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": f"r{i}"}]},
                "timestamp": "2026-02-22T10:00:00Z",
            })
            for i in range(5)
        ]
        content = ("\n".join(lines) + "\n").encode()
        write_session(cc_projects_dir(hive_env), cwd, "nouser-sess", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert result == []

    def test_session_dict_fields(self, hive_env):
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/fieldtest"
        content = make_jsonl_content(n_user_messages=3)
        write_session(cc_projects_dir(hive_env), cwd, "fields-sess", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert len(result) == 1
        s = result[0]
        for field in ("session_id", "started", "user_messages", "is_live"):
            assert field in s, f"Missing field: {field}"
        assert s["is_live"] is True

    def test_message_count_25kb_file(self, hive_env):
        """BUG-4 fix: messages in the 14760-20480 byte overlap zone are not double-counted."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/overlapping"
        n_msgs = 10
        content = make_jsonl_content(n_user_messages=n_msgs, target_size_bytes=25 * 1024)
        assert len(content) >= 25 * 1024

        write_session(cc_projects_dir(hive_env), cwd, "overlap-sess", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=60)
        assert len(result) == 1
        count = result[0]["user_messages"]
        assert count == n_msgs, (
            f"BUG-4: Expected {n_msgs}, got {count}. "
            f"{'Double-counted overlap zone.' if count > n_msgs else ''}"
        )

    def test_message_count_large_50kb(self, hive_env):
        """50KB file: tail starts at byte 40960, no overlap with first 20KB chunk."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/large50kb"
        n_msgs = 8
        content = make_jsonl_content(n_user_messages=n_msgs, target_size_bytes=50 * 1024)
        write_session(cc_projects_dir(hive_env), cwd, "large-sess", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=60)
        assert len(result) == 1
        assert result[0]["user_messages"] == n_msgs
