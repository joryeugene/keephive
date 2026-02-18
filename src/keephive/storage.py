"""File I/O for keephive data: daily logs, memory.md, rules.md.

All paths point to ~/.claude/hive/ by default. Same data files as the
bash version, no migration needed.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path


def parse_date_arg(arg: str) -> str:
    """Parse a date argument into an ISO date string.

    Accepts:
        ""           -> today
        "today"      -> today
        "yesterday"  -> yesterday's date
        "3"          -> 3 days ago
        "2026-02-15" -> literal ISO date
    """
    if not arg or arg == "today":
        return date.today().isoformat()

    if arg == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()

    if arg.isdigit():
        days_ago = int(arg)
        return (date.today() - timedelta(days=days_ago)).isoformat()

    # Try ISO date
    if re.match(r"^\d{4}-\d{2}-\d{2}$", arg):
        try:
            date.fromisoformat(arg)
            return arg
        except ValueError:
            pass

    return arg  # Let it fail downstream with a clear message


def safe_read_text(path: Path) -> str:
    """Read file text, replacing bad bytes instead of crashing."""
    return path.read_text(errors="replace")


def hive_dir() -> Path:
    """Root hive directory, respecting HIVE_HOME env var."""
    return Path(os.environ.get("HIVE_HOME", Path.home() / ".claude" / "hive"))


def working_dir() -> Path:
    return hive_dir() / "working"


def daily_dir() -> Path:
    return hive_dir() / "daily"


def knowledge_dir() -> Path:
    return hive_dir() / "knowledge"


def guides_dir() -> Path:
    return knowledge_dir() / "guides"


def prompts_dir() -> Path:
    return knowledge_dir() / "prompts"


def archive_dir() -> Path:
    return hive_dir() / "archive"


def drafts_dir() -> Path:
    return working_dir() / "drafts"


def notes_dir() -> Path:
    return working_dir() / "notes"


NOTE_SLOT_COUNT = 10


def active_slot() -> int:
    """Read active note slot number (1-10). Default 1."""
    marker = working_dir() / ".note-active"
    if marker.exists():
        try:
            n = int(marker.read_text().strip())
            if 1 <= n <= NOTE_SLOT_COUNT:
                return n
        except (ValueError, OSError):
            pass
    return 1


def set_active_slot(n: int) -> None:
    """Write active note slot number (1-10)."""
    if not 1 <= n <= NOTE_SLOT_COUNT:
        raise ValueError(f"Slot must be 1-{NOTE_SLOT_COUNT}, got {n}")
    marker = working_dir() / ".note-active"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(n))


def slot_file(n: int) -> Path:
    """Path to note slot file. Slots 1-10."""
    return working_dir() / f"note-{n}.md"


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    for d in [working_dir(), daily_dir(), knowledge_dir(),
              guides_dir(), prompts_dir(), archive_dir(), notes_dir()]:
        d.mkdir(parents=True, exist_ok=True)


def today() -> str:
    return date.today().isoformat()


def yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def daily_file(day: str | None = None) -> Path:
    """Path to a daily log file. Defaults to today."""
    if day is None:
        day = today()
    return daily_dir() / f"{day}.md"


def ensure_daily(day: str | None = None) -> Path:
    """Ensure today's daily file exists with header. Returns path."""
    ensure_dirs()
    path = daily_file(day)
    if not path.exists():
        path.write_text(f"# Daily Log: {day or today()}\n\n")
    return path


def memory_file() -> Path:
    return working_dir() / "memory.md"


def rules_file() -> Path:
    return working_dir() / "rules.md"


def read_memory() -> str:
    """Read working memory, empty string if missing."""
    f = memory_file()
    return f.read_text() if f.exists() else ""


def read_rules() -> str:
    """Read working rules, empty string if missing."""
    f = rules_file()
    return f.read_text() if f.exists() else ""


def backup_and_write(path: Path, content: str) -> None:
    """Backup a file then write new content."""
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(content)


def append_to_daily(text: str, day: str | None = None) -> Path:
    """Append a line to the daily log. Returns the daily file path."""
    path = ensure_daily(day)
    with open(path, "a") as f:
        f.write(text + "\n")
    return path


def stale_days() -> int:
    return int(os.environ.get("HIVE_STALE_DAYS", "30"))


def capture_budget() -> int:
    return int(os.environ.get("HIVE_CAPTURE_BUDGET", "4000"))


# ---- Counting / querying ----

def count_stale_facts() -> int:
    """Count facts in memory.md with verified dates older than stale threshold."""
    mem = memory_file()
    if not mem.exists():
        return 0

    cutoff = date.today() - timedelta(days=stale_days())
    count = 0

    for line in mem.read_text().splitlines():
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            try:
                vdate = date.fromisoformat(m.group(1))
                if vdate < cutoff:
                    count += 1
            except ValueError:
                pass
    return count


def get_stale_facts() -> list[tuple[int, str, str]]:
    """Get stale facts with line numbers and the raw line.

    Returns list of (line_number_1based, fact_text, raw_line).
    """
    mem = memory_file()
    if not mem.exists():
        return []

    cutoff = date.today() - timedelta(days=stale_days())
    results = []

    for i, line in enumerate(mem.read_text().splitlines(), 1):
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            try:
                vdate = date.fromisoformat(m.group(1))
                if vdate < cutoff:
                    # Strip the verified tag to get the fact text
                    fact = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line).lstrip("- ").strip()
                    results.append((i, fact, line))
            except ValueError:
                pass
    return results


def count_daily_entries(day: str | None = None, exclude_noise: bool = True) -> int:
    """Count meaningful entries in a daily log file."""
    path = daily_file(day)
    if not path.exists():
        return 0

    count = 0
    cats_re = re.compile(r"^- (DECISION|FACT|CORRECTION|TODO|INSIGHT):")

    for line in safe_read_text(path).splitlines():
        line = line.rstrip()
        m = re.match(r"^- \[(\d{2}:\d{2}:\d{2})\]\s*(.*)", line)
        if m:
            rest = m.group(2)
            upper = rest.upper()
            if exclude_noise and ("SESSION" in upper or "COMPACTED" in upper or "COMPACTION" in upper):
                continue
            count += 1
        elif cats_re.match(line):
            count += 1
    return count


def get_meaningful_entries(day: str | None = None, limit: int = 8) -> list[str]:
    """Extract meaningful entries from a daily log, formatted for display.

    Returns formatted entry strings (with ~ prefix for categorized entries).
    """
    path = daily_file(day)
    if not path.exists():
        return []

    cats_re = re.compile(r"(DECISION|FACT|CORRECTION|TODO|INSIGHT):")
    entries: list[str] = []
    in_summary = False
    summary_is_clean: bool | None = None

    for line in safe_read_text(path).splitlines():
        line = line.rstrip()
        if line.startswith("### Session Summary"):
            in_summary = True
            summary_is_clean = None
            continue
        if line.startswith("### ") or line.startswith("# "):
            in_summary = False
            continue

        m = re.match(r"^- \[(\d{2}:\d{2}:\d{2})\]\s*(.*)", line)
        if m:
            ts, rest = m.group(1), m.group(2)
            upper = rest.upper()
            if "SESSION" in upper or "COMPACTED" in upper or "COMPACTION" in upper:
                continue
            if cats_re.match(rest):
                if in_summary:
                    if summary_is_clean is None:
                        summary_is_clean = True
                    if summary_is_clean:
                        entries.append(f"  ~ [{ts}] {rest}")
                else:
                    entries.append(f"  ~ [{ts}] {rest}")
            else:
                in_summary = False
                entries.append(f"  {line[2:]}")
            continue

        if re.match(r"^- " + r"(DECISION|FACT|CORRECTION|TODO|INSIGHT):", line):
            if in_summary:
                if summary_is_clean is None:
                    summary_is_clean = True
                if summary_is_clean:
                    entries.append(f"  ~ {line[2:]}")
            continue

        if in_summary and line.strip() and summary_is_clean is None:
            summary_is_clean = False

    # Truncate long entries, return last N
    result = []
    for e in entries[-limit:]:
        if len(e) > 120:
            result.append(e[:120] + "...")
        else:
            result.append(e)
    return result


def collect_todos() -> tuple[list[tuple[str, str, str]], set[str]]:
    """Collect all TODOs and DONEs from last 30 days of daily logs.

    Returns (todos_list, done_set) where todos_list is [(date_str, time_str, text)]
    and done_set is {text.lower()}. time_str is "HH:MM" or "" if no timestamp.
    """
    d = daily_dir()
    if not d.exists():
        return [], set()

    cutoff = (date.today() - timedelta(days=30)).isoformat()
    todos: list[tuple[str, str, str]] = []
    dones: set[str] = set()

    for fpath in sorted(d.glob("*.md")):
        fname = fpath.stem
        if fname < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            line = line.rstrip()
            # TODO entries with timestamp
            m = re.match(r"^- \[(\d{2}:\d{2}):\d{2}\]\s*TODO:\s*(.*)", line)
            if m:
                todos.append((fname, m.group(1), m.group(2).strip()))
                continue
            # TODO entries without timestamp
            m = re.match(r"^- TODO:\s*(.*)", line)
            if m:
                todos.append((fname, "", m.group(1).strip()))
                continue
            # DONE entries
            m = re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*DONE:\s*(.*)", line)
            if m:
                dones.add(m.group(1).strip().lower())
                continue
            m = re.match(r"^- DONE:\s*(.*)", line)
            if m:
                dones.add(m.group(1).strip().lower())

    return todos, dones


def open_todos() -> list[tuple[str, str, str]]:
    """Return open TODOs (not completed), deduplicated.

    Returns list of (date_str, time_str, text).
    """
    todos, dones = collect_todos()
    open_list = [(d, t, text) for d, t, text in todos if text.lower() not in dones]
    return _dedup_todos(open_list)


def _normalize_todo_text(text: str) -> str:
    """Normalize TODO text for comparison.

    Strips common prefixes like [audit], [reflect], [daily], timestamps,
    normalizes whitespace and punctuation.
    """
    s = text.strip().lower()
    # Strip bracketed prefixes: [audit], [reflect], [daily], etc.
    s = re.sub(r"^\[[\w-]+\]\s*", "", s)
    # Strip leading timestamps if present
    s = re.sub(r"^\d{2}:\d{2}(:\d{2})?\s*", "", s)
    # Normalize whitespace
    s = re.sub(r"\s+", " ", s)
    # Strip trailing punctuation
    s = s.rstrip(".,;:!?")
    return s


def _dedup_todos(todos: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Remove near-duplicate TODOs, keeping most recent.

    Uses a two-pass approach:
    1. Fast exact-content dedup (after normalization)
    2. Fuzzy SequenceMatcher for remaining items (threshold 0.8)
    """
    from difflib import SequenceMatcher

    if not todos:
        return []

    # Pass 1: exact content dedup (after normalization)
    seen_normalized: dict[str, int] = {}  # normalized -> index in result
    result: list[tuple[str, str, str]] = []

    for d, t, text in todos:
        norm = _normalize_todo_text(text)
        if norm in seen_normalized:
            idx = seen_normalized[norm]
            rd, _, _ = result[idx]
            if d > rd:
                result[idx] = (d, t, text)
        else:
            seen_normalized[norm] = len(result)
            result.append((d, t, text))

    # Pass 2: fuzzy dedup on remaining items
    deduped: list[tuple[str, str, str]] = []
    for d, t, text in result:
        norm = _normalize_todo_text(text)
        is_dup = False
        for i, (rd, _rt, rtext) in enumerate(deduped):
            rnorm = _normalize_todo_text(rtext)
            if SequenceMatcher(None, norm, rnorm).ratio() >= 0.8:
                if d > rd:
                    deduped[i] = (d, t, text)
                is_dup = True
                break
        if not is_dup:
            deduped.append((d, t, text))

    return deduped


def recent_dones(days: int = 3) -> list[tuple[str, str]]:
    """Return recently completed TODOs from daily logs.

    Returns list of (date_str, text) for DONEs in the last N days.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    dones: list[tuple[str, str]] = []
    d = daily_dir()
    if not d.exists():
        return dones
    for fpath in sorted(d.glob("*.md")):
        if fpath.stem < cutoff:
            continue
        for line in safe_read_text(fpath).splitlines():
            m = re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*DONE:\s*(.*)", line)
            if m:
                dones.append((fpath.stem, m.group(1).strip()))
                continue
            m2 = re.match(r"^- DONE:\s*(.*)", line)
            if m2:
                dones.append((fpath.stem, m2.group(1).strip()))
    return dones


# ---- Recurring tasks ----

FREQ_ALIASES = {"daily": 1.0, "weekly": 7.0, "monthly": 30.0}

# Regex for frequency strings: daily, weekly, monthly, 2d, 12h, etc.
FREQ_RE = re.compile(r"^(\d+)([dh])$")


def parse_freq(freq_str: str) -> float:
    """Parse a frequency string to interval in days.

    Accepts: daily, weekly, monthly, Nd (N days), Nh (N hours).
    Returns interval in fractional days.
    """
    if freq_str in FREQ_ALIASES:
        return FREQ_ALIASES[freq_str]
    m = FREQ_RE.match(freq_str)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "d":
            return float(n)
        if unit == "h":
            return n / 24.0
    raise ValueError(f"Invalid frequency: {freq_str!r}. Use daily, weekly, monthly, Nd, or Nh.")


def is_valid_freq(freq_str: str) -> bool:
    """Check if a frequency string is valid."""
    try:
        parse_freq(freq_str)
        return True
    except ValueError:
        return False


def recurring_file() -> Path:
    return working_dir() / "recurring.md"


def due_recurring() -> list[tuple[str, str, int]]:
    """Return (frequency, text, days_overdue) for due recurring tasks."""
    rf = recurring_file()
    if not rf.exists():
        return []

    content = safe_read_text(rf)
    tasks: list[tuple[str, str]] = []
    last_done: dict[str, str] = {}  # text_lower -> date_or_datetime_str
    in_completed = False

    for line in content.splitlines():
        line = line.rstrip()
        if line.startswith("## Last Completed"):
            in_completed = True
            continue
        if line.startswith("## ") or line.startswith("# "):
            if in_completed:
                in_completed = False
            continue

        if not in_completed:
            m = re.match(r"^- \[([^\]]+)\]\s*(.*)", line)
            if m and is_valid_freq(m.group(1)):
                tasks.append((m.group(1), m.group(2).strip()))
        else:
            m = re.match(r"^- (.+?):\s*(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?)", line)
            if m:
                last_done[m.group(1).strip().lower()] = m.group(2)

    result: list[tuple[str, str, int]] = []
    now = datetime.now()
    t = date.today()
    for freq, text in tasks:
        interval_days = parse_freq(freq)
        last = last_done.get(text.lower())
        if last:
            try:
                if "T" in last:
                    last_dt = datetime.fromisoformat(last)
                    elapsed_days = (now - last_dt).total_seconds() / 86400
                else:
                    last_date = date.fromisoformat(last)
                    elapsed_days = float((t - last_date).days)
                overdue_days = int(elapsed_days - interval_days)
                if overdue_days >= 0:
                    result.append((freq, text, overdue_days))
            except ValueError:
                result.append((freq, text, int(interval_days)))
        else:
            result.append((freq, text, int(interval_days)))

    return result


def mark_recurring_done(pattern: str) -> tuple[str, str] | None:
    """Mark a recurring task as done by pattern match.

    Returns (task_text, done_str) on success, None if no match.
    Pure data function: no console output.
    """
    rf = recurring_file()
    if not rf.exists():
        return None

    content = safe_read_text(rf)

    # Find matching task
    match_text = None
    match_freq = None
    task_re = re.compile(r"^- \[([^\]]+)\]\s*(.*)")
    for line in content.splitlines():
        m = task_re.match(line)
        if m and is_valid_freq(m.group(1)) and pattern.lower() in m.group(2).lower():
            match_freq = m.group(1)
            match_text = m.group(2).strip()
            break

    if not match_text:
        return None

    # Use datetime for hour-based tasks, date for day-based
    uses_hours = match_freq and match_freq.endswith("h")
    if uses_hours:
        done_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    else:
        done_str = date.today().isoformat()

    # Update or add Last Completed entry
    lines = content.splitlines(keepends=True)
    found_entry = False
    for i, line in enumerate(lines):
        m = re.match(r"^- (.+?):\s*\d{4}-\d{2}-\d{2}", line)
        if m and m.group(1).strip().lower() == match_text.lower():
            lines[i] = f"- {match_text}: {done_str}\n"
            found_entry = True
            break

    if not found_entry:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"- {match_text}: {done_str}\n")

    rf.write_text("".join(lines))
    return (match_text, done_str)


def recent_daily_files(days: int = 7) -> list[Path]:
    """Return recent daily log files, most recent first."""
    d = daily_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), reverse=True)
    return files[:days]


def index_file() -> Path:
    return hive_dir() / ".index.json"


def version_context() -> str:
    """Gather system version info for verify/reflect prompts."""
    import subprocess
    lines = []
    for cmd, label in [
        (["node", "--version"], "Node.js"),
        (["python3", "--version"], "Python"),
        (["claude", "--version"], "Claude Code"),
        (["uv", "--version"], "uv"),
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                lines.append(f"{label}: {r.stdout.strip()}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return "\n".join(lines)


def get_key_entries_past_days(days: int = 7, limit: int = 10) -> list[tuple[str, str]]:
    """FACT/CORRECTION/DECISION/INSIGHT entries from past N days.

    Excludes today. Skips TODOs (already shown in their own section).
    Returns list of (date_str, entry_line) most recent first.
    """
    dd = daily_dir()
    if not dd.exists():
        return []

    today_str = date.today().isoformat()
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    results: list[tuple[str, str]] = []

    for fpath in sorted(dd.glob("*.md"), reverse=True):
        day_str = fpath.stem
        # Skip today (already shown) and files outside range
        if day_str >= today_str or day_str < cutoff:
            continue
        # Only date-named files
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day_str):
            continue

        entries = get_meaningful_entries(day=day_str, limit=20)
        for entry in entries:
            # Only keep categorized entries (~ prefix), skip TODOs
            if not entry.strip().startswith("~"):
                continue
            if "TODO:" in entry:
                continue
            results.append((day_str, entry.strip()))
            if len(results) >= limit:
                return results

    return results


# ---- Usage Stats ----

def stats_file() -> Path:
    """Path to the stats JSON file."""
    return hive_dir() / ".stats.json"


def _detect_source() -> str:
    """Detect invocation context from environment."""
    if os.environ.get("CLAUDECODE"):
        return "claude_code"
    return "terminal"


def read_stats() -> dict:
    """Read stats from disk. Returns empty dict structure on missing/corrupt file."""
    sf = stats_file()
    if not sf.exists():
        return {"days": {}}
    try:
        with open(sf, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.loads(f.read())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if "days" not in data:
            data["days"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"days": {}}


def _write_stats(data: dict) -> None:
    """Write stats atomically with exclusive lock."""
    sf = stats_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    with open(sf, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(data, indent=2))
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def track_event(
    category: str,
    name: str,
    project: str = "",
    source: str = "",
) -> None:
    """Increment a daily counter. Handles nested project structure + source tracking.

    Args:
        category: Top-level category (commands, hooks, meta)
        name: Event name within category (e.g. "remember", "sessionstart")
        project: Full cwd path or ~ for home. Tracked per-project when provided.
        source: Invocation source (terminal, claude_code, mcp, hook). Auto-detected if empty.
    """
    try:
        data = read_stats()
        day = date.today().isoformat()

        if day not in data["days"]:
            data["days"][day] = {}
        day_data = data["days"][day]

        # Increment category counter
        if category not in day_data:
            day_data[category] = {}
        if name not in day_data[category]:
            day_data[category][name] = 0
        day_data[category][name] += 1

        # Track source
        src = source or _detect_source()
        if "sources" not in day_data:
            day_data["sources"] = {}
        if src not in day_data["sources"]:
            day_data["sources"][src] = 0
        day_data["sources"][src] += 1

        # Track per-project
        if project and category == "commands":
            # Normalize project path: replace home dir with ~
            home = str(Path.home())
            proj_key = project.replace(home, "~") if project.startswith(home) else project

            if "projects" not in day_data:
                day_data["projects"] = {}
            if proj_key not in day_data["projects"]:
                day_data["projects"][proj_key] = {
                    "commands": 0,
                    "sessions": 0,
                    "by_command": {},
                }
            proj = day_data["projects"][proj_key]
            proj["commands"] += 1
            if name not in proj["by_command"]:
                proj["by_command"][name] = 0
            proj["by_command"][name] += 1

        _write_stats(data)
    except Exception:
        pass  # Never block hooks or commands
