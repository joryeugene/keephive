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
    for d in [
        working_dir(),
        daily_dir(),
        knowledge_dir(),
        guides_dir(),
        prompts_dir(),
        archive_dir(),
        notes_dir(),
    ]:
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
    """Backup a file then atomically write new content."""
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(str(tmp), str(path))


def append_to_daily(text: str, day: str | None = None) -> Path:
    """Append a line to the daily log. Returns the daily file path."""
    path = ensure_daily(day)
    with open(path, "a") as f:
        f.write(text + "\n")
    return path


def stale_days() -> int:
    return int(os.environ.get("HIVE_STALE_DAYS", "30"))


# ---- Context-aware decay rates ----

CATEGORY_STALE_DAYS = {
    "DECISION": 90,
    "CORRECTION": 60,
    "INSIGHT": 60,
    "FACT": 30,
    "TODO": 7,
}


def _fact_category(fact_text: str) -> str:
    """Extract category prefix from a fact line (e.g. 'FACT: ...' -> 'FACT').

    Returns 'FACT' as default when no recognized prefix is found.
    """
    stripped = fact_text.lstrip("- ").strip()
    upper = stripped.upper()
    for cat in ("DECISION", "CORRECTION", "INSIGHT", "FACT", "TODO"):
        if upper.startswith(cat + ":") or upper.startswith(cat + " "):
            return cat
    return "FACT"


def stale_days_for_fact(fact_text: str) -> int:
    """Get staleness threshold in days for a fact based on its category prefix.

    HIVE_STALE_DAYS env var overrides all categories (backward compat).
    Otherwise: DECISION=90d, CORRECTION/INSIGHT=60d, FACT=30d, TODO=7d.
    """
    env_override = os.environ.get("HIVE_STALE_DAYS")
    if env_override:
        return int(env_override)
    cat = _fact_category(fact_text)
    return CATEGORY_STALE_DAYS.get(cat, 30)


def capture_budget() -> int:
    return int(os.environ.get("HIVE_CAPTURE_BUDGET", "4000"))


# ---- Counting / querying ----


def count_stale_facts() -> int:
    """Count facts in memory.md with verified dates older than their category threshold."""
    mem = memory_file()
    if not mem.exists():
        return 0

    today_d = date.today()
    count = 0

    for line in mem.read_text().splitlines():
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            try:
                vdate = date.fromisoformat(m.group(1))
                fact_text = (
                    re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line).lstrip("- ").strip()
                )
                threshold = stale_days_for_fact(fact_text)
                if (today_d - vdate).days > threshold:
                    count += 1
            except ValueError:
                pass
    return count


def get_stale_facts() -> list[tuple[int, str, str]]:
    """Get stale facts with line numbers and the raw line.

    Uses per-category staleness thresholds (DECISION=90d, FACT=30d, etc.).
    Returns list of (line_number_1based, fact_text, raw_line).
    """
    mem = memory_file()
    if not mem.exists():
        return []

    today_d = date.today()
    results = []

    for i, line in enumerate(mem.read_text().splitlines(), 1):
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            try:
                vdate = date.fromisoformat(m.group(1))
                fact = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line).lstrip("- ").strip()
                threshold = stale_days_for_fact(fact)
                if (today_d - vdate).days > threshold:
                    results.append((i, fact, line))
            except ValueError:
                pass
    return results


def get_all_verified_facts() -> list[tuple[int, str, str]]:
    """Get ALL facts with [verified:] tags, regardless of age.

    Returns list of (line_number_1based, fact_text, raw_line).
    """
    mem = memory_file()
    if not mem.exists():
        return []

    results = []

    for i, line in enumerate(mem.read_text().splitlines(), 1):
        m = re.search(r"\[verified:(\d{4}-\d{2}-\d{2})\]", line)
        if m:
            try:
                date.fromisoformat(m.group(1))  # validate date
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
            if exclude_noise and (
                "SESSION" in upper or "COMPACTED" in upper or "COMPACTION" in upper
            ):
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
        for i, (rd, _, rtext) in enumerate(deduped):
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


def undo_done(pattern: str = "") -> str | None:
    """Remove the most recent DONE entry matching pattern from daily logs.

    Searches recent files (newest first). If pattern is empty, removes the
    most recent DONE entry from today only.
    Returns undone text, or None if not found.
    """
    d = daily_dir()
    if not d.exists():
        return None

    files = sorted(d.glob("*.md"), reverse=True)
    # With no pattern, only search today; with pattern, search last 3 days
    search_files = files[:1] if not pattern else files[:3]

    for fpath in search_files:
        lines = safe_read_text(fpath).splitlines(keepends=True)
        match_idx = None
        match_text = None
        for i in range(len(lines) - 1, -1, -1):
            m = re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*DONE:\s*(.*)", lines[i])
            if not m:
                m = re.match(r"^- DONE:\s*(.*)", lines[i])
            if m:
                text = m.group(1).strip()
                if not pattern or pattern.lower() in text.lower():
                    match_idx = i
                    match_text = text
                    break
        if match_idx is not None:
            del lines[match_idx]
            fpath.write_text("".join(lines))
            return match_text

    return None


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


def _parse_recurring_status() -> list[tuple[str, str, float]]:
    """Parse recurring.md, return (freq, text, overdue_days_float) for all tasks.

    overdue_days >= 0: already due/overdue.
    overdue_days < 0: -(days until due).
    """
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

    result: list[tuple[str, str, float]] = []
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
                result.append((freq, text, elapsed_days - interval_days))
            except ValueError:
                result.append((freq, text, float(interval_days)))
        else:
            result.append((freq, text, float(interval_days)))

    return result


def due_recurring() -> list[tuple[str, str, int]]:
    """Return (frequency, text, days_overdue) for due recurring tasks."""
    return [(f, t, int(o)) for f, t, o in _parse_recurring_status() if o >= 0]


def all_recurring() -> list[tuple[str, str, int]]:
    """Return (freq, text, overdue_days) for ALL recurring tasks.

    overdue_days >= 0: due/overdue. overdue_days < 0: -(days until due).
    Sorted: most overdue first, then by time remaining ascending.
    """
    raw = _parse_recurring_status()
    result = [(f, t, int(o)) for f, t, o in raw]
    return sorted(result, key=lambda x: -x[2])


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

        # Track hourly activity
        hour_key = datetime.now().strftime("%H")  # "00"-"23"
        if "hours" not in day_data:
            day_data["hours"] = {}
        day_data["hours"][hour_key] = day_data["hours"].get(hour_key, 0) + 1

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


# ---- FTS5 full-text search ----


def fts_db_path() -> Path:
    return hive_dir() / ".fts.db"


def rebuild_fts_index() -> None:
    """Build or rebuild FTS5 index from daily logs and archive."""
    import sqlite3

    db = fts_db_path()
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(line, date, tier)")
        con.execute("DELETE FROM fts")
        for tier, directory in [("daily", daily_dir()), ("archive", archive_dir())]:
            if directory.exists():
                for f in directory.rglob("*.md"):
                    for line in safe_read_text(f).splitlines():
                        if line.strip():
                            con.execute(
                                "INSERT INTO fts VALUES (?, ?, ?)",
                                (line, f.stem, tier),
                            )
        for slot in range(1, NOTE_SLOT_COUNT + 1):
            note_path = slot_file(slot)
            if note_path.exists():
                for line in safe_read_text(note_path).splitlines():
                    if line.strip():
                        con.execute(
                            "INSERT INTO fts VALUES (?, ?, ?)",
                            (line, f"note-{slot}", "notes"),
                        )
        con.commit()
    finally:
        con.close()


def fts_search(query: str, limit: int = 10) -> list[dict]:
    """FTS5 search over daily logs and archive. Falls back to [] on failure."""
    import sqlite3

    db = fts_db_path()
    if not db.exists():
        try:
            rebuild_fts_index()
        except Exception:
            return []
    try:
        con = sqlite3.connect(str(db))
        try:
            rows = con.execute(
                "SELECT line, date, tier, rank FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        finally:
            con.close()
        results = []
        for line, date_str, tier, rank in rows:
            # rank is negative in FTS5; more negative = better match
            score = max(1, int(60 + rank * 5))
            results.append({"tier": tier, "line": line, "date": date_str, "score": score})
        return results
    except Exception:
        return []


def count_log_entries_with_prefix(prefix: str, days: int = 90) -> int:
    """Count daily log entries containing a given prefix across recent logs."""
    count = 0
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    dd = daily_dir()
    if not dd.exists():
        return 0
    for f in sorted(dd.glob("*.md")):
        if f.stem >= cutoff:
            for line in safe_read_text(f).splitlines():
                if prefix in line:
                    count += 1
    return count


def last_log_entry_with_prefix(prefix: str) -> str:
    """Return text after prefix from the most recent matching log entry."""
    dd = daily_dir()
    if not dd.exists():
        return ""
    for f in sorted(dd.glob("*.md"), reverse=True):
        for line in reversed(safe_read_text(f).splitlines()):
            if prefix in line:
                idx = line.index(prefix)
                return line[idx + len(prefix) :].strip()
    return ""


# ---- Recall frequency tracking ----


def recall_stats_file() -> Path:
    """Path to the recall frequency tracking file."""
    return hive_dir() / ".recall-stats.json"


def track_recall_hit(fact_line: str) -> None:
    """Increment recall counter for a fact line. Silent on error."""
    import hashlib

    try:
        sf = recall_stats_file()
        data: dict = {}
        if sf.exists():
            try:
                data = json.loads(sf.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}

        key = hashlib.sha256(fact_line.strip().encode()).hexdigest()[:16]
        entry = data.get(key, {"count": 0, "last": ""})
        entry["count"] = entry.get("count", 0) + 1
        entry["last"] = date.today().isoformat()
        data[key] = entry

        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(data))
    except Exception:
        pass  # Never block recall operations


def get_recall_count(fact_line: str) -> int:
    """Get recall count for a fact line."""
    import hashlib

    sf = recall_stats_file()
    if not sf.exists():
        return 0
    try:
        data = json.loads(sf.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    key = hashlib.sha256(fact_line.strip().encode()).hexdigest()[:16]
    return data.get(key, {}).get("count", 0)


# ---- Verification evidence storage ----


def evidence_file() -> Path:
    """Path to the verification evidence sidecar."""
    return working_dir() / "evidence.json"


def read_evidence() -> dict:
    """Read evidence data from disk. Returns empty dict on missing/corrupt."""
    ef = evidence_file()
    if not ef.exists():
        return {}
    try:
        return json.loads(ef.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def write_evidence(data: dict) -> None:
    """Write evidence data atomically."""
    ef = evidence_file()
    ef.parent.mkdir(parents=True, exist_ok=True)
    ef.write_text(json.dumps(data, indent=2))


def store_evidence(
    fact_text: str, verdict: str, reason: str, correction: str | None = None
) -> None:
    """Store verification evidence for a fact.

    Tracks verify count, correction count, source locations extracted from reason,
    and keeps a compact history (last 5 verifications).
    """
    import hashlib

    data = read_evidence()
    key = hashlib.sha256(fact_text.strip().encode()).hexdigest()[:16]

    entry = data.get(
        key,
        {
            "fact": fact_text,
            "verify_count": 0,
            "correction_count": 0,
            "history": [],
        },
    )

    entry["fact"] = fact_text
    entry["verify_count"] = entry.get("verify_count", 0) + 1
    if verdict == "STALE" and correction:
        entry["correction_count"] = entry.get("correction_count", 0) + 1

    entry["last_verdict"] = verdict
    entry["last_reason"] = reason
    entry["last_date"] = date.today().isoformat()

    # Extract file:line references from the reason text
    locations = re.findall(r"[\w/.-]+\.(?:py|js|ts|md|json|yaml|toml|cfg|ini|txt)(?::\d+)?", reason)
    if locations:
        entry["source_locations"] = locations[:5]

    # Keep history compact (last 5 verifications)
    history = entry.get("history", [])
    history.append(
        {
            "date": date.today().isoformat(),
            "verdict": verdict,
            "reason": reason[:200],
        }
    )
    entry["history"] = history[-5:]

    data[key] = entry
    write_evidence(data)


def get_evidence_for_fact(fact_text: str) -> dict | None:
    """Get stored evidence for a fact, or None if no evidence exists."""
    import hashlib

    data = read_evidence()
    key = hashlib.sha256(fact_text.strip().encode()).hexdigest()[:16]
    return data.get(key)


# ---- Memory decay scoring ----

IMPORTANCE_WEIGHTS = {"CORRECTION": 1.5, "DECISION": 1.2, "FACT": 1.0, "INSIGHT": 1.0}


def _count_fact_references(fact_text: str) -> int:
    """Count how many times this fact is referenced in the last 30 daily log files."""
    keywords = [w.lower() for w in fact_text.split() if len(w) > 4]
    if not keywords:
        return 0
    dd = daily_dir()
    if not dd.exists():
        return 0
    files = sorted(dd.glob("*.md"), reverse=True)[:30]
    count = 0
    for f in files:
        text = safe_read_text(f).lower()
        if any(kw in text for kw in keywords):
            count += 1
    return count


def ui_queue_path(project: str | None = None) -> "Path":
    """Path to the UI feedback queue file.

    When project is provided, returns a project-scoped path (.ui-queue-{project})
    so multiple concurrent hive serve instances don't consume each other's feedback.
    Falls back to the global .ui-queue when project is None.
    """
    if project:
        return hive_dir() / f".ui-queue-{project}"
    return hive_dir() / ".ui-queue"


def score_fact_decay(fact_text: str, verified_date_str: str) -> float:
    """Score a fact for decay. Lower score = better candidate for archiving.

    Components:
      recency (0.0-1.0, weight 0.4): 1.0 = today, 0.0 = threshold+ days ago
        Uses per-category threshold (DECISION=90d, FACT=30d, etc.)
      references (0.0-1.0, weight 0.2): how often it appears in daily logs
      importance (weight 0.2): by category prefix (CORRECTION > DECISION > FACT/INSIGHT)
      recall_freq (0.0-1.0, weight 0.2): how often recalled via hive rc
    """
    # Recency: use category-aware threshold instead of fixed 60 days
    threshold = stale_days_for_fact(fact_text)
    try:
        vdate = date.fromisoformat(verified_date_str)
        days_old = (date.today() - vdate).days
        recency = max(0.0, 1.0 - days_old / (threshold * 2.0))
    except ValueError:
        recency = 0.0

    refs = _count_fact_references(fact_text)
    ref_score = min(1.0, refs / 10.0)

    importance = 1.0
    for cat, weight in IMPORTANCE_WEIGHTS.items():
        if fact_text.upper().startswith(cat):
            importance = weight
            break

    # Recall frequency: facts recalled often are more valuable
    recall_count = get_recall_count(fact_text)
    recall_score = min(1.0, recall_count / 10.0)

    return recency * 0.4 + ref_score * 0.2 + importance * 0.2 + recall_score * 0.2


# ---- Session tracking ----


def track_session_event(
    session_id: str,
    event: str,
    project: str = "",
    tool_name: str = "",
) -> None:
    """Track a session lifecycle event in daily stats.

    Creates or updates a session entry keyed by session_id under the current day.

    Args:
        session_id: Claude Code session identifier.
        event: One of "start", "prompt", "tool", "compact".
        project: Full cwd path (normalized to ~ prefix).
        tool_name: Tool name for "tool" events (Edit, Write, etc.).
    """
    if not session_id:
        return
    try:
        data = read_stats()
        day = date.today().isoformat()

        if day not in data["days"]:
            data["days"][day] = {}
        day_data = data["days"][day]

        if "sessions" not in day_data:
            day_data["sessions"] = {}
        sessions = day_data["sessions"]

        now = datetime.now().isoformat(timespec="seconds")

        if session_id not in sessions:
            home = str(Path.home())
            proj_key = project.replace(home, "~") if project.startswith(home) else project
            sessions[session_id] = {
                "project": proj_key,
                "started": now,
                "last_seen": now,
                "prompts": 0,
                "tools": {},
                "compacted": False,
            }

        session = sessions[session_id]
        session["last_seen"] = now

        if event == "prompt":
            session["prompts"] += 1
        elif event == "tool" and tool_name:
            session["tools"][tool_name] = session["tools"].get(tool_name, 0) + 1
        elif event == "compact":
            session["compacted"] = True

        _write_stats(data)
    except Exception:
        pass  # Never block hooks


def read_sessions(days_back: int = 30) -> list[dict]:
    """Read all session records from the last N days.

    Returns a flat list sorted by started timestamp. Each dict includes
    session_id and day keys for reference.
    """
    data = read_stats()
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    result: list[dict] = []
    for day_str, day_data in data.get("days", {}).items():
        if day_str < cutoff:
            continue
        for sid, sdata in day_data.get("sessions", {}).items():
            entry = {**sdata, "session_id": sid, "day": day_str}
            result.append(entry)
    result.sort(key=lambda x: x.get("started", ""))
    return result


def session_metrics(days_back: int = 30) -> dict:
    """Compute derived session metrics from the last N days.

    Returns dict with total_sessions, sessions_today, sessions_this_week,
    avg/median prompts per session, avg duration, tool breakdown,
    compaction rate, per-project counts, and daily session counts.
    """
    from statistics import median

    sessions = read_sessions(days_back=days_back)
    today_str = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    total = len(sessions)
    today_count = sum(1 for s in sessions if s["day"] == today_str)
    week_count = sum(1 for s in sessions if s["day"] >= week_ago)

    # Prompts
    prompt_counts = [s.get("prompts", 0) for s in sessions]
    avg_prompts = sum(prompt_counts) / total if total else 0.0
    med_prompts = float(median(prompt_counts)) if prompt_counts else 0.0

    # Duration (minutes)
    durations: list[float] = []
    for s in sessions:
        started = s.get("started", "")
        last_seen = s.get("last_seen", "")
        if started and last_seen:
            try:
                t0 = datetime.fromisoformat(started)
                t1 = datetime.fromisoformat(last_seen)
                mins = (t1 - t0).total_seconds() / 60.0
                if mins >= 0:
                    durations.append(mins)
            except ValueError:
                pass
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    # Tool breakdown
    tool_totals: dict[str, int] = {}
    for s in sessions:
        for tool, count in s.get("tools", {}).items():
            tool_totals[tool] = tool_totals.get(tool, 0) + count
    total_tool_uses = sum(tool_totals.values()) or 1
    tool_pct = {t: c / total_tool_uses for t, c in tool_totals.items()}

    # Compaction rate
    compacted = sum(1 for s in sessions if s.get("compacted"))
    compaction_rate = compacted / total if total else 0.0

    # Per-project
    by_project: dict[str, int] = {}
    for s in sessions:
        proj = s.get("project", "")
        if proj:
            by_project[proj] = by_project.get(proj, 0) + 1

    # Daily session counts (14 days for sparkline)
    daily_sessions: list[tuple[str, int]] = []
    for i in range(13, -1, -1):
        d = date.today() - timedelta(days=i)
        day_s = d.isoformat()
        count = sum(1 for s in sessions if s["day"] == day_s)
        daily_sessions.append((day_s, count))

    return {
        "total_sessions": total,
        "sessions_today": today_count,
        "sessions_this_week": week_count,
        "avg_prompts_per_session": avg_prompts,
        "median_prompts_per_session": med_prompts,
        "avg_duration_minutes": avg_duration,
        "tool_totals": tool_totals,
        "tool_pct": tool_pct,
        "compaction_rate": compaction_rate,
        "sessions_by_project": by_project,
        "daily_sessions": daily_sessions,
    }
