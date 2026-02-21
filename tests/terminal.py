"""Real terminal driver. Types into tmux, reads the screen.

tmux send-keys types. tmux capture-pane reads. Marker echo syncs.
No new dependencies: tmux (already installed), stdlib subprocess.

Usage:
    with Terminal(tmp_path) as t:
        t.type("hive r 'FACT: real test'").has("Remembered")
        t.set_date("2026-03-01")
        t.type("hive s").has("fact")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

START_MARKER = "__HIVE_E2E_START__"
END_MARKER = "__HIVE_E2E_DONE__"
POLL_INTERVAL = 0.05  # 50ms between screen checks
DEFAULT_TIMEOUT = 10.0


class Screen:
    """The terminal screen after a command runs."""

    def __init__(self, plain: str, ansi: str, command: str):
        self.plain = plain  # ANSI stripped
        self.ansi = ansi  # Raw with ANSI codes
        self.command = command  # What was typed
        self.lines = [ln for ln in plain.strip().splitlines() if ln.strip()]

    def has(self, *texts: str) -> "Screen":
        """Assert all texts appear in output."""
        for t in texts:
            assert t in self.plain, f"Expected {t!r} in output of `{self.command}`:\n{self.plain}"
        return self

    def lacks(self, *texts: str) -> "Screen":
        """Assert none of the texts appear in output."""
        for t in texts:
            assert t not in self.plain, (
                f"Unexpected {t!r} in output of `{self.command}`:\n{self.plain}"
            )
        return self

    def has_ansi(self) -> "Screen":
        """Assert ANSI escape codes are present."""
        assert "\x1b[" in self.ansi, f"No ANSI codes in output of `{self.command}`"
        return self

    def line_count_between(self, lo: int, hi: int) -> "Screen":
        """Assert line count is within range."""
        n = len(self.lines)
        assert lo <= n <= hi, f"Expected {lo}-{hi} lines, got {n} from `{self.command}`"
        return self

    def matches(self, pattern: str) -> "Screen":
        """Assert regex pattern matches somewhere in output."""
        assert re.search(pattern, self.plain), (
            f"Pattern {pattern!r} not found in `{self.command}`:\n{self.plain}"
        )
        return self

    def __contains__(self, text: str) -> bool:
        return text in self.plain

    def __repr__(self) -> str:
        return f"Screen({len(self.lines)} lines, cmd={self.command!r})"


class Terminal:
    """Real terminal session backed by tmux.

    Creates a detached tmux session with HIVE_HOME pointing to a temp dir.
    Every command is typed via send-keys, and output is read via capture-pane.
    """

    def __init__(self, base_dir: Path, width: int = 120, height: int = 40):
        self.session = f"hive_{uuid.uuid4().hex[:8]}"
        self.hive_home = base_dir / "hive"
        self.width = width
        self.height = height
        self._alive = False
        self._history: list[dict] = []

        # Scaffold hive directory
        self.hive_home.mkdir(parents=True, exist_ok=True)
        for sub in [
            "working",
            "daily",
            "knowledge/guides",
            "knowledge/prompts",
            "working/notes",
            "archive",
        ]:
            (self.hive_home / sub).mkdir(parents=True, exist_ok=True)

        # Find project venv so `python` resolves inside tmux
        project_root = Path(__file__).resolve().parent.parent
        venv_bin = project_root / ".venv" / "bin"
        path = os.environ.get("PATH", "")
        if venv_bin.exists():
            path = f"{venv_bin}:{path}"

        self._env = {
            "HIVE_HOME": str(self.hive_home),
            "HIVE_SKIP_LLM": "1",
            "TERM": "xterm-256color",
            "HOME": os.environ.get("HOME", ""),
            "PATH": path,
            "SHELL": os.environ.get("SHELL", "/bin/zsh"),
            "VIRTUAL_ENV": str(venv_bin.parent) if venv_bin.exists() else "",
        }
        self._start()

    def _start(self) -> None:
        """Create a detached tmux session."""
        cmd = [
            "tmux",
            "new-session",
            "-d",
            "-s",
            self.session,
            "-x",
            str(self.width),
            "-y",
            str(self.height),
        ]
        for k, v in self._env.items():
            if v:
                cmd.extend(["-e", f"{k}={v}"])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"tmux new-session failed: {r.stderr}")
        self._alive = True

        # Large scrollback so long output (100+ lines) is fully captured
        subprocess.run(
            ["tmux", "set-option", "-t", self.session, "history-limit", "10000"],
            capture_output=True,
        )

        time.sleep(0.3)  # Wait for shell prompt

        # zsh's .zshrc overrides PATH from tmux -e, so re-prepend venv bin.
        # Use $PATH shell reference to keep the command short (the full PATH
        # can be 1000+ chars and corrupts tmux send-keys at fast typing speed).
        project_root = Path(__file__).resolve().parent.parent
        venv_bin = project_root / ".venv" / "bin"
        if venv_bin.exists():
            self._send(f"export PATH={venv_bin}:$PATH")
            time.sleep(0.1)

    def type(self, command: str, timeout: float = DEFAULT_TIMEOUT) -> Screen:
        """Type a command, wait for completion, return screen content.

        Uses dual markers (START/END) to cleanly delimit output regardless
        of shell prompt complexity (multi-line starship, powerline, etc.).
        """
        # Clear screen + scrollback for clean capture
        self._send("clear")
        time.sleep(0.1)
        subprocess.run(
            ["tmux", "clear-history", "-t", self.session],
            capture_output=True,
        )
        # Send command bracketed by start/end markers
        self._send(f"echo {START_MARKER}; {command}; echo {END_MARKER}")

        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self._capture(ansi=False)
            # Must check for END_MARKER as an exact line, not substring.
            # The typed command line contains "echo __HIVE_E2E_DONE__" which
            # would falsely match a substring check before the command finishes.
            if any(ln.strip() == END_MARKER for ln in raw.split("\n")):
                ansi = self._capture(ansi=True)
                screen = self._extract(raw, ansi, command)
                self._history.append(
                    {
                        "command": command,
                        "plain": screen.plain,
                        "line_count": len(screen.lines),
                        "timestamp": time.time(),
                    }
                )
                return screen
            time.sleep(POLL_INTERVAL)

        raise TimeoutError(
            f"Command did not complete in {timeout}s: {command}\n"
            f"Screen:\n{self._capture(ansi=False)}"
        )

    def send_char(self, char: str) -> None:
        """Send a single character (for y/n prompts). No Enter."""
        subprocess.run(
            ["tmux", "send-keys", "-t", self.session, char],
            capture_output=True,
            check=True,
        )

    def send_keys(self, keys: str) -> None:
        """Send tmux key sequences without Enter.

        Unlike send_char (single literal char), this handles tmux key names
        like C-c (Ctrl+C), C-d, Escape. Used to stop long-running commands.
        """
        subprocess.run(
            ["tmux", "send-keys", "-t", self.session, keys],
            capture_output=True,
            check=True,
        )

    def wait_for(self, text: str, timeout: float = 5.0) -> Screen:
        """Poll screen until text appears in the plain output.

        Used for long-running commands (like --watch) where marker-based
        sync via type() would timeout. Raises TimeoutError if not found.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self._capture(ansi=False)
            if text in raw:
                ansi = self._capture(ansi=True)
                return Screen(raw, ansi, f"(wait_for {text!r})")
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(
            f"Text {text!r} not found within {timeout}s\nScreen:\n{self._capture(ansi=False)}"
        )

    def screen(self) -> Screen:
        """Read current screen without typing anything."""
        raw = self._capture(ansi=False)
        ansi = self._capture(ansi=True)
        return Screen(raw, ansi, "(screen read)")

    def set_date(self, iso: str) -> "Terminal":
        """Set HIVE_DATE env var in the shell (persists for subsequent commands)."""
        self._send(f"export HIVE_DATE={iso}")
        time.sleep(0.1)
        return self

    def advance_days(self, n: int, from_date: str) -> "Terminal":
        """Advance HIVE_DATE by n days from a base date."""
        d = date.fromisoformat(from_date) + timedelta(days=n)
        return self.set_date(d.isoformat())

    def seed(self, days: int = 30) -> "Terminal":
        """Run hive seed in the terminal."""
        self.type(
            f"python -m keephive seed --force --days {days}",
            timeout=15,
        )
        return self

    def read_file(self, rel: str) -> str:
        """Read a file from the hive home directory."""
        return (self.hive_home / rel).read_text()

    def file_exists(self, rel: str) -> bool:
        """Check if a file exists in the hive home directory."""
        return (self.hive_home / rel).exists()

    def save_history(self, path: Path) -> None:
        """Save full command history as JSON artifact."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._history, indent=2, default=str))

    # ---- Internal ----

    def _send(self, text: str) -> None:
        """Send keystrokes + Enter to the tmux session."""
        subprocess.run(
            ["tmux", "send-keys", "-t", self.session, text, "Enter"],
            capture_output=True,
            check=True,
        )

    def _capture(self, ansi: bool = False) -> str:
        """Capture the tmux pane content including scrollback history.

        -S - captures from the start of scrollback (not just visible area).
        This is essential for commands that produce more output than the
        pane height (e.g., seq 1 100 with a 40-row pane).
        """
        cmd = ["tmux", "capture-pane", "-t", self.session, "-p", "-S", "-"]
        if ansi:
            cmd.append("-e")
        return subprocess.run(cmd, capture_output=True, text=True).stdout

    def _extract(self, raw: str, ansi: str, command: str) -> Screen:
        """Extract command output between START and END markers.

        Prompt-agnostic: works with multi-line starship, powerline, plain sh.
        The screen after clear + dual-marker command looks like:
            keephive on  dev ...                     <- prompt line 1
            > echo __START__; cmd; echo __END__      <- typed command
            __START__                                <- exact marker line
            <actual output>                          <- what we want
            __END__                                  <- exact marker line
            keephive on  dev ...                     <- new prompt

        We match lines that are exactly the marker text (stripped) to skip
        the typed command line which also contains the marker text.
        The LAST START marker is used (ignores old markers in scrollback),
        and END is searched only after that position.
        """
        lines = raw.split("\n")

        # Find the LAST START marker (handles scrollback from previous commands)
        start_idx = 0
        for i, ln in enumerate(lines):
            if ln.strip() == START_MARKER:
                start_idx = i + 1

        # Find the first END marker AFTER the start position
        end_idx = len(lines)
        for i in range(start_idx, len(lines)):
            if lines[i].strip() == END_MARKER:
                end_idx = i
                break

        output_lines = lines[start_idx:end_idx]
        clean = "\n".join(output_lines).strip()
        return Screen(clean, ansi, command)

    def close(self) -> None:
        """Kill the tmux session."""
        if self._alive:
            subprocess.run(
                ["tmux", "kill-session", "-t", self.session],
                capture_output=True,
            )
            self._alive = False

    def __enter__(self) -> "Terminal":
        return self

    def __exit__(self, *_a: object) -> None:
        self.close()
