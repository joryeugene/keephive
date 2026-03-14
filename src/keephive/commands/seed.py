"""hive seed: populate current profile with realistic demo data."""

from __future__ import annotations

import json
import random
from datetime import timedelta
from importlib import resources

from keephive.clock import get_today
from keephive.output import console, show_hint
from keephive.storage import (
    active_profile_label,
    ensure_dirs,
    guides_dir,
    hive_dir,
    prompts_dir,
    recall_stats_file,
    rules_file,
    slot_file,
    stats_file,
    working_dir,
)


def _load_entries() -> dict:
    """Load demo entries from package data."""
    ref = resources.files("keephive.data.demo").joinpath("entries.json")
    return json.loads(ref.read_text(encoding="utf-8"))


def cmd_seed(args: list[str]) -> None:
    """Seed current hive directory with demo data."""
    days = 60
    force = "--force" in args

    for arg in args:
        if arg.startswith("--days"):
            # --days=N or --days N
            if "=" in arg:
                days = int(arg.split("=", 1)[1])
            else:
                idx = args.index(arg)
                if idx + 1 < len(args):
                    days = int(args[idx + 1])

    target = hive_dir()
    label = active_profile_label()
    has_data = any(
        f.exists() and f.stat().st_size > 0
        for f in [
            target / "working" / "memory.md",
            target / ".stats.json",
        ]
    )

    console.print(f"[bold]Target: {label}[/bold]  ({target})")

    if has_data and not force:
        from keephive.output import prompt_yn

        if not prompt_yn(f"Overwrite existing data in {label}?"):
            console.print("[dim]Cancelled[/dim]")
            return

    ensure_dirs()
    entries = _load_entries()
    rng = random.Random(42)

    _seed_daily_logs(entries, days, rng)
    _seed_memory(entries)
    _seed_stats(entries, days, rng)
    _seed_guides(entries)
    _seed_prompts(entries)
    _seed_recurring(entries)
    _seed_notes(entries)
    _seed_evidence(entries, rng)
    _seed_rules(entries)
    _seed_recall_stats(entries, rng)
    _seed_pending_rules()
    _seed_soul()

    console.print(f"[ok]Seeded {days} days of demo data[/ok]")
    console.print(f"[dim]Data: {target}[/dim]")
    show_hint("hive s", "see seeded data")


def _seed_soul() -> None:
    """Write sample SOUL.md."""
    from keephive.commands.setup import _SOUL_TEMPLATE
    from keephive.storage import soul_file

    sf = soul_file()
    if not sf.exists():
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(_SOUL_TEMPLATE.format(project="demo-project"))


def _seed_daily_logs(entries: dict, days: int, rng: random.Random) -> None:
    """Generate daily log files with realistic entry distribution."""
    from keephive.storage import daily_dir

    dd = daily_dir()
    dd.mkdir(parents=True, exist_ok=True)

    today = get_today()
    facts = entries["facts"]
    decisions = entries["decisions"]
    insights = entries["insights"]
    corrections = entries["corrections"]
    todos = entries["todos"]
    dones = entries["dones"]
    projects = entries["projects"]

    all_entries = (
        [("FACT", f) for f in facts]
        + [("DECISION", d) for d in decisions]
        + [("INSIGHT", i) for i in insights]
        + [("CORRECTION", c) for c in corrections]
    )

    todo_idx = 0
    done_idx = 0

    for day_offset in range(days, 0, -1):
        d = today - timedelta(days=day_offset)
        day_str = d.isoformat()
        weekday = d.weekday()  # 0=Mon, 6=Sun

        # Weekend: 0-3 entries. Weekday: 3-12 entries.
        if weekday >= 5:
            n_entries = rng.randint(0, 3)
        else:
            n_entries = rng.randint(3, 12)

        if n_entries == 0:
            continue

        lines = [f"# Daily Log: {day_str}\n"]

        # Session marker
        proj = rng.choice(projects)
        lines.append(f"- [09:{rng.randint(0, 59):02d}:00] session [{proj}] ~/code/{proj}\n")

        # Generate entries with realistic timestamps
        hour = 9
        for _ in range(n_entries):
            hour = min(hour + rng.randint(0, 2), 22)
            minute = rng.randint(0, 59)
            ts = f"{hour:02d}:{minute:02d}:{rng.randint(0, 59):02d}"

            roll = rng.random()
            if roll < 0.15 and todo_idx < len(todos):
                lines.append(f"- [{ts}] TODO: {todos[todo_idx]}")
                todo_idx += 1
            elif roll < 0.25 and done_idx < len(dones):
                lines.append(f"- [{ts}] DONE: {dones[done_idx]}")
                done_idx += 1
            else:
                cat, text = rng.choice(all_entries)
                lines.append(f"- [{ts}] {cat}: {text}")

        (dd / f"{day_str}.md").write_text("\n".join(lines) + "\n")


def _seed_memory(entries: dict) -> None:
    """Write memory.md with verified facts at varying ages."""
    mem_path = working_dir() / "memory.md"
    today = get_today()

    lines = ["# Working Memory\n"]
    for item in entries["memory_facts"]:
        age = item["age_days"]
        verified_date = (today - timedelta(days=age)).isoformat()
        lines.append(f"- {item['text']} [verified:{verified_date}]")

    mem_path.write_text("\n".join(lines) + "\n")


def _seed_stats(entries: dict, days: int, rng: random.Random) -> None:
    """Generate .stats.json with realistic usage patterns."""
    today = get_today()
    projects = entries["projects"]
    commands = [
        "r",
        "rc",
        "s",
        "v",
        "t",
        "td",
        "l",
        "e",
        "st",
        "a",
        "rf",
        "su",
        "dr",
        "go",
        "n",
        "k",
    ]
    hook_names = ["sessionstart", "precompact", "posttooluse", "userpromptsubmit"]

    data: dict = {"days": {}}

    for day_offset in range(days, 0, -1):
        d = today - timedelta(days=day_offset)
        day_str = d.isoformat()
        weekday = d.weekday()

        if weekday >= 5 and rng.random() < 0.4:
            continue  # Skip some weekend days

        # Command counts
        cmd_counts: dict[str, int] = {}
        n_cmds = rng.randint(5, 40) if weekday < 5 else rng.randint(0, 10)
        for _ in range(n_cmds):
            cmd = rng.choice(commands)
            cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1

        # Hook counts
        hook_counts: dict[str, int] = {}
        for hook in hook_names:
            hook_counts[hook] = rng.randint(1, max(2, n_cmds // 3))

        # Hourly activity
        hours: dict[str, int] = {}
        active_hours = rng.sample(range(8, 22), min(8, n_cmds // 2 + 1))
        for h in active_hours:
            hours[f"{h:02d}"] = rng.randint(1, 8)

        # Sessions
        n_sessions = rng.randint(1, 5) if weekday < 5 else rng.randint(0, 2)
        sessions: dict[str, dict] = {}
        for s in range(n_sessions):
            sid = f"demo-{day_str}-{s}"
            proj = rng.choice(projects)
            start_hour = rng.randint(8, 18)
            duration_mins = rng.randint(10, 180)
            started = f"{day_str}T{start_hour:02d}:{rng.randint(0, 59):02d}:00"
            end_hour = min(23, start_hour + duration_mins // 60)
            last_seen = f"{day_str}T{end_hour:02d}:{rng.randint(0, 59):02d}:00"
            sessions[sid] = {
                "project": f"~/code/{proj}",
                "started": started,
                "last_seen": last_seen,
                "prompts": rng.randint(5, 200),
                "tools": {"Edit": rng.randint(1, 20), "Read": rng.randint(1, 30)},
                "compacted": rng.random() > 0.4,
            }

        # Sources
        sources = {"claude_code": rng.randint(3, 30), "terminal": rng.randint(0, 10)}

        # Per-project
        proj_data: dict[str, dict] = {}
        for proj in rng.sample(projects, rng.randint(1, min(3, len(projects)))):
            proj_data[f"~/code/{proj}"] = {
                "commands": rng.randint(2, 15),
                "sessions": rng.randint(1, 3),
                "by_command": {"r": rng.randint(1, 5), "s": rng.randint(1, 3)},
            }

        data["days"][day_str] = {
            "commands": cmd_counts,
            "hooks": hook_counts,
            "hours": hours,
            "sources": sources,
            "sessions": sessions,
            "projects": proj_data,
        }

    sf = stats_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(data, indent=2))


def _seed_guides(entries: dict) -> None:
    """Write knowledge guides."""
    gd = guides_dir()
    gd.mkdir(parents=True, exist_ok=True)

    for name, content in entries.get("guides", {}).items():
        (gd / f"{name}.md").write_text(content)


def _seed_prompts(entries: dict) -> None:
    """Write prompt templates."""
    pd = prompts_dir()
    pd.mkdir(parents=True, exist_ok=True)

    for name, content in entries.get("prompts", {}).items():
        (pd / f"{name}.md").write_text(content)


def _seed_recurring(entries: dict) -> None:
    """Write recurring.md."""
    rf = working_dir() / "recurring.md"
    lines = ["# Recurring Tasks\n"]

    for task in entries.get("recurring", []):
        lines.append(f"- [{task['freq']}] {task['text']}")

    lines.append("\n## Last Completed")
    rf.write_text("\n".join(lines) + "\n")


def _seed_notes(entries: dict) -> None:
    """Write note slot files."""
    for slot_str, content in entries.get("notes", {}).items():
        slot = int(slot_str)
        sf = slot_file(slot)
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(content)


def _seed_evidence(entries: dict, rng: random.Random) -> None:
    """Write evidence.json with verification history."""
    import hashlib

    from keephive.storage import evidence_file

    today = get_today()
    data: dict = {}

    for item in entries["memory_facts"][:10]:
        key = hashlib.sha256(item["text"].strip().encode()).hexdigest()[:16]
        age = item["age_days"]
        verify_date = (today - timedelta(days=max(0, age - 2))).isoformat()
        data[key] = {
            "fact": item["text"],
            "verify_count": rng.randint(1, 5),
            "correction_count": 0,
            "last_verdict": "CURRENT",
            "last_reason": "Verified against codebase configuration",
            "last_date": verify_date,
            "history": [
                {
                    "date": verify_date,
                    "verdict": "CURRENT",
                    "reason": "Verified against codebase configuration",
                }
            ],
        }

    ef = evidence_file()
    ef.parent.mkdir(parents=True, exist_ok=True)
    ef.write_text(json.dumps(data, indent=2))


def _seed_rules(entries: dict) -> None:
    """Write rules.md from entries rules array."""
    rules = entries.get("rules", [])
    if not rules:
        return
    rf = rules_file()
    rf.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Rules\n"]
    for rule in rules:
        lines.append(f"- {rule}")
    rf.write_text("\n".join(lines) + "\n")


def _seed_recall_stats(entries: dict, rng: random.Random) -> None:
    """Write .recall-stats.json with realistic recall counts keyed by fact hash."""
    import hashlib

    recall_map = entries.get("recall_stats", {})
    if not recall_map:
        return

    today = get_today()
    memory_facts = entries.get("memory_facts", [])
    data: dict = {}

    for item in memory_facts:
        text = item["text"].strip()
        # Match facts to recall topics by keyword overlap
        for topic, count in recall_map.items():
            if topic.lower() in text.lower():
                key = hashlib.sha256(text.encode()).hexdigest()[:16]
                age = item["age_days"]
                last_date = (today - timedelta(days=max(0, age - rng.randint(0, 3)))).isoformat()
                data[key] = {"count": count, "last": last_date}
                break

    sf = recall_stats_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(data, indent=2))


def _seed_pending_rules() -> None:
    """Write .pending-rules.md with sample pending suggestions."""
    path = hive_dir() / ".pending-rules.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Pending Rule Suggestions\n\n"
        "- [3 sessions: repeated_error] Verify API response schema before updating TypeScript interfaces\n"
        "- [2 sessions: missed_test] Add negative test cases when implementing error handling paths\n"
    )
