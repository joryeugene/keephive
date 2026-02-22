"""Tests for storage.read_live_sessions() and storage.is_ghost_session()."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# ---- Helpers ----


def make_jsonl_content(n_user_messages: int, target_size_bytes: int = 0) -> bytes:
    """Build realistic JSONL bytes with n_user_messages and optional padding.

    Padding is added as a single large assistant turn at the end.
    """
    lines = []
    base_ts = "2026-02-22T10:00:00Z"

    for i in range(n_user_messages):
        user_line = json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": f"user message {i}"},
                "timestamp": base_ts,
                "uuid": f"user-{i:04d}-aaaa-bbbb-cccc-dddddddddddd",
            }
        )
        lines.append(user_line)
        asst_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"response {i}"}],
                },
                "timestamp": base_ts,
                "uuid": f"asst-{i:04d}-aaaa-bbbb-cccc-dddddddddddd",
            }
        )
        lines.append(asst_line)

    content = ("\n".join(lines) + "\n").encode()

    if target_size_bytes > len(content):
        padding_needed = target_size_bytes - len(content)
        pad_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "x" * padding_needed}],
                },
                "timestamp": base_ts,
            }
        )
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

    def test_idle_session_still_returned(self, hive_env):
        """Sessions idle for >30 min are NOT filtered when active_dirs confirms the process is alive.

        active_dirs comes from lsof — positive confirmation the process is running.
        Filtering by mtime would incorrectly hide long-idle but genuinely active sessions.
        """
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/staleproject"
        content = make_jsonl_content(n_user_messages=3)
        old_mtime = time.time() - (60 * 60)  # 60 min ago — simulates idle session
        write_session(cc_projects_dir(hive_env), cwd, "stale-sess", content, mtime=old_mtime)

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert len(result) == 1
        assert result[0]["session_id"] == "stale-sess"
        assert result[0]["is_live"] is True

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
            cc_projects_dir(hive_env),
            cwd,
            "bad-sess",
            b"{{{not json}}}\n{also bad}\n",
            mtime=time.time(),
        )

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert result == []

    def test_zero_user_messages_filtered(self, hive_env):
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/assistantonly"
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"r{i}"}],
                    },
                    "timestamp": "2026-02-22T10:00:00Z",
                }
            )
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
        """50KB file: tail read activates for the end of the file."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/large50kb"
        n_msgs = 8
        content = make_jsonl_content(n_user_messages=n_msgs, target_size_bytes=50 * 1024)
        write_session(cc_projects_dir(hive_env), cwd, "large-sess", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=60)
        assert len(result) == 1
        assert result[0]["user_messages"] == n_msgs


# ---- Helpers for tool_counts tests ----


def make_jsonl_with_tools(tool_sequences: list[list[str]], user_turns: bool = True) -> bytes:
    """Generate JSONL content for testing tool_counts parsing.

    Args:
        tool_sequences: list of lists, each inner list is tool names for one assistant turn
        user_turns: if True, interleave user turns with timestamps
    Returns:
        bytes: JSONL content
    """
    lines: list[str] = []
    ts = "2026-01-01T00:00:00Z"
    for tools in tool_sequences:
        if user_turns:
            lines.append(json.dumps({"type": "user", "timestamp": ts}))
        content = [{"type": "tool_use", "name": t} for t in tools]
        lines.append(
            json.dumps({"type": "assistant", "message": {"content": content}})
        )
    return ("\n".join(lines) + "\n").encode()


# ---- TestToolCountsParsing ----


class TestToolCountsParsing:
    def test_tool_counts_basic(self, hive_env):
        """Two assistant turns each with Edit+Write yields {Edit:2, Write:2}."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/toolbasic"
        content = make_jsonl_with_tools([["Edit", "Write"], ["Edit", "Write"]])
        write_session(cc_projects_dir(hive_env), cwd, "tc-basic", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert len(result) == 1
        tc = result[0]["tool_counts"]
        assert tc == {"Edit": 2, "Write": 2}

    def test_tool_counts_same_tool_accumulates(self, hive_env):
        """Read in 3 separate turns accumulates to 3."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/toolaccum"
        content = make_jsonl_with_tools([["Read"], ["Read"], ["Read"]])
        write_session(cc_projects_dir(hive_env), cwd, "tc-accum", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert len(result) == 1
        assert result[0]["tool_counts"]["Read"] == 3

    def test_tool_counts_empty_name_skipped(self, hive_env):
        """tool_use with empty name string is not included in tool_counts."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/toolempty"
        content = make_jsonl_with_tools([["", "Edit"]])
        write_session(cc_projects_dir(hive_env), cwd, "tc-empty", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert len(result) == 1
        tc = result[0]["tool_counts"]
        assert "" not in tc
        assert tc == {"Edit": 1}

    def test_tool_counts_non_list_content_safe(self, hive_env):
        """Assistant record with string content (not list) yields empty tool_counts."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/toolnonlist"
        # Build manually: user turn + assistant with string content
        lines = [
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z"}),
            json.dumps({"type": "assistant", "message": {"content": "a plain string"}}),
        ]
        content = ("\n".join(lines) + "\n").encode()
        write_session(cc_projects_dir(hive_env), cwd, "tc-nonlist", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=30)
        assert len(result) == 1
        assert result[0]["tool_counts"] == {}

    def test_tool_counts_in_tail_large_file(self, hive_env):
        """Tool uses in the tail section of a >53KB file are still counted.

        Creates a file where the first ~36KB is user-only padding (no tools),
        then appends assistant turns with tool_use. The tail-read logic must
        pick up tools that fall beyond the 32KB head chunk.
        """
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/tooltail"
        ts = "2026-01-01T00:00:00Z"

        # Build ~36KB of user-only lines (no tool_use at all)
        padding_lines: list[str] = []
        while len("\n".join(padding_lines).encode()) < 36_000:
            idx = len(padding_lines)
            padding_lines.append(
                json.dumps({
                    "type": "user",
                    "timestamp": ts,
                    "message": {"role": "user", "content": f"padding message {idx:06d}"},
                })
            )
            padding_lines.append(
                json.dumps({
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": f"ack {idx:06d}"}]},
                })
            )

        # Now add assistant turns with tool_use AFTER the padding
        tool_lines: list[str] = []
        for t in ["Bash", "Grep", "Glob"]:
            tool_lines.append(json.dumps({"type": "user", "timestamp": ts}))
            tool_lines.append(
                json.dumps({
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": t}]},
                })
            )

        # Add enough extra padding at the end to push total > 53KB
        # so the tail seek doesn't overlap with the head chunk
        extra_pad: list[str] = []
        combined_so_far = "\n".join(padding_lines + tool_lines).encode()
        while len(combined_so_far) + len("\n".join(extra_pad).encode()) < 55_000:
            idx = len(extra_pad)
            extra_pad.append(
                json.dumps({
                    "type": "user",
                    "timestamp": ts,
                    "message": {"role": "user", "content": f"tail-pad {idx:06d}"},
                })
            )

        all_lines = padding_lines + tool_lines + extra_pad
        content = ("\n".join(all_lines) + "\n").encode()
        assert len(content) > 53_000, f"File too small: {len(content)} bytes"

        write_session(cc_projects_dir(hive_env), cwd, "tc-tail", content, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd], recency_minutes=60)
        assert len(result) == 1
        tc = result[0]["tool_counts"]
        # The tools are in the MIDDLE of the file. With head=32KB and tail=20KB,
        # at least some should be captured.
        assert len(tc) > 0, f"Expected non-empty tool_counts, got: {tc}"

    def test_dedup_active_dirs_same_cwd_twice(self, hive_env):
        """Passing same dir twice in active_dirs returns 2 sessions, not 4."""
        from keephive.storage import read_live_sessions

        cwd = "/Users/test/dedupdir"
        projdir = cc_projects_dir(hive_env)

        content_a = make_jsonl_with_tools([["Edit"]])
        content_b = make_jsonl_with_tools([["Write"]])
        write_session(projdir, cwd, "dedup-a", content_a, mtime=time.time())
        write_session(projdir, cwd, "dedup-b", content_b, mtime=time.time())

        result = read_live_sessions(active_dirs=[cwd, cwd], recency_minutes=30)
        assert len(result) == 2
