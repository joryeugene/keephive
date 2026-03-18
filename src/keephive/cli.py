"""CLI dispatch for keephive. Flat command table, no framework."""

from __future__ import annotations

import re
import sys

from keephive import __version__

# Per-command help strings, keyed by canonical command name.
HELP: dict[str, str] = {
    "status": "Usage: hive s [--watch|-w] [--interval N]\n  Status overview (facts, TODOs, stale warnings)\n  --watch/-w      Live refresh when data changes\n  --interval N    Seconds between checks (default 2)",
    "remember": "Usage: hive r <text>\n  Save insight to daily log\n  Prefix with FACT:/DECISION:/TODO:/INSIGHT:/CORRECTION: for categorization",
    "recall": "Usage: hive rc <query> [--deep] [--json]\n  Search all memory tiers\n  --deep  Expand search with AI when few results found",
    "verify": "Usage: hive v [--check] [--json] [--verbose]\n  Verify facts against codebase using LLM\n  --check  Quick stale count, exit code 1 if stale\n  --verbose  Show raw LLM output",
    "reflect": "Usage: hive rf [scan|analyze|apply|draft <topic>|insights]\n  scan       Quick log scan (no AI)\n  analyze    Pattern detection with AI (~20s)\n  apply      Review and graduate analysis to memory\n  draft      Draft a knowledge guide from logs\n  insights   Session quality patterns from /insights data (no AI)",
    "log": "Usage: hive l [date|summarize] [--watch|-w] [--interval N]\n  View daily log. Date: today, yesterday, N (days ago), YYYY-MM-DD\n  summarize  AI summary of today's entries (3-5 bullets)\n  --watch/-w      Live refresh when log changes\n  --interval N    Seconds between checks (default 2)",
    "edit": "Usage: hive e [target]\n  Targets: memory, rules, soul, settings, local, today, note\n  No args: show available targets",
    "todo": "Usage: hive todo [done <pat>] [repeat [freq] [text]] [--watch|-w] [--interval N]\n  todo         List open TODOs\n  todo done X  Mark TODO matching X complete\n  todo repeat  List/add recurring tasks\n  --watch/-w      Live refresh when TODOs change\n  --interval N    Seconds between checks (default 2)",
    "note": 'Usage: hive n [show|copy|clear|list|<slot>|<template>]\n  n          Open active slot in $EDITOR\n  n.3        Switch to slot 3, open editor (1-9, 0=10)\n  4          Open slot 4 in $EDITOR (bare-digit shorthand)\n  n show     Print content\n  n copy     Copy to clipboard\n  n clear    Archive and clear\n  n list     Show all slots\n  n <N> todo  Extract TODOs from slot N and add to daily log\n  4 "text"   Append text to slot 4 without opening editor',
    "knowledge": "Usage: hive k [name|edit <name>|rm <name>]\n  k           List all guides and prompts\n  k <name>    View guide (prefix match)\n  k edit X    Create/edit guide\n  k rm X      Remove guide\n  Frontmatter: tags, projects, always (always: true injects into every session)",
    "audit": "Usage: hive a [-v] [--json]\n  Quality Pulse: 3-perspective LLM analysis + synthesis\n  -v      Show full perspective essays\n  --json  Machine-readable output",
    "doctor": "Usage: hive dr\n  Health check: hooks, MCP, deps, data integrity\n  Uses LLM for semantic TODO dedup (deterministic fallback if unavailable)",
    "gc": "Usage: hive gc [--dry-run]\n  Archive daily logs older than 30 days\n  --dry-run  Show what would be archived without doing it",
    "standup": "Usage: hive su\n  Generate standup summary from daily logs + GitHub PRs\n  Uses LLM for formatting. Copies to clipboard.",
    "stats": "Usage: hive st [-p <project>] [date]\n  Usage statistics. Date: today, N (days ago), YYYY-MM-DD",
    "mem": "Usage: hive m [rm|review] <text>\n  Add or remove working memory facts\n  hive m <text>      Add fact to memory.md\n  hive m rm <pat>    Remove line matching pattern\n  hive m review      Review pending auto-captured facts",
    "flow": "Usage: hive flow [--skip-verify]\n  Guided maintenance flow: review pending facts, rules, improvements, then verify\n  --skip-verify  Skip the LLM verify stage",
    "improve": "Usage: hive improve [review|list|clear-stale]\n  Review or list KingBee self-improvement proposals",
    "checkup": "Usage: hive checkup [--snapshot|--diff|--json]\n  Production health check: hooks, daemon, queues, soul, data integrity\n  --snapshot  Git-snapshot current hive state (before testing)\n  --diff      Show diff since last snapshot\n  --json      Machine-readable output",
    "daemon": (
        "Usage: hive daemon [start|stop|status|run|edit|log|enable|disable]\n"
        "  Manage the KingBee background daemon\n"
        "  start/stop           Launch/kill daemon process\n"
        "  status               Show task schedule and last-run times\n"
        "  run <task>           Trigger immediately (soul-update, self-improve, ...)\n"
        "  enable/disable <task>  Toggle scheduled execution\n"
        "  edit                 Open daemon.json in $EDITOR\n"
        "  log                  View last 50 lines of daemon.log"
    ),
    "rule": "Usage: hive rule [rm|review|learn|try] <text>  (alias: rl = rule learn)\n  Add or remove behavioral rules\n  hive rule <text>      Add rule\n  hive rule rm <pat>    Remove matching rule\n  hive rule review      Review pending rule suggestions\n  hive rule learn       Learn rules from /insights friction data\n  hive rule learn --dry-run   Preview without queuing\n  hive rule try <text> [--days N]  Add experimental rule (auto-expires)\n  hive rl               Shortcut for 'hive rule learn'",
    "session": "Usage: hive go [mode|prompt]\n  Modes: todo, verify, learn, reflect\n  Or load a custom prompt from knowledge/prompts/",
    "skill": "Usage: hive sk [publish <name>|unpublish <name>|sync|find <q>]\n  Manage skill plugins",
    "update": "Usage: hive up\n  Upgrade keephive to the latest version in-place",
    "setup": "Usage: hive setup\n  Initial setup: register MCP server + hooks in ~/.claude/",
    "ps": "Usage: hive ps\n  Local hive map: active claude sessions, recent project activity, git state",
    "set": "Usage: hive set [key] [value]\n  View/change settings\n  No args: show all settings\n  hive set sound on/off   Toggle audio notifications",
    "config": "Usage: hive config llm-backend [list|set <name>|auto]\n  Manage LLM backend selection\n  auto          Reset to auto-detect\n  list          Show detected backends\n  set <name>    Persist preferred backend",
    "sound-test": "Usage: hive sound-test [error]\n  Play configured notification sound\n  No args: play success sound\n  error: play error sound",
    "serve": "Usage: hive serve [port] [--hot]\n  Live web dashboard at localhost:3847 (default)\n  Views: / (home) /dev /know (guides+memory+notes) /stats\n  --hot  Watch source files, auto-restart on change",
    "ui": "Usage: hive ui [install|clear]\n  ui           Show pending UI feedback queue\n  ui-install   Print bookmarklet URL (drag to bookmarks bar)\n  ui-clear     Clear pending feedback",
    "profile": "Usage: hive profile [list|use|create|delete] [name] [--seed]\n  list          Show all profiles\n  use <name>    Switch to profile\n  create <name> Create new profile\n  delete <name> Delete profile and data\n  --seed        Populate with demo data on create",
    "seed": "Usage: hive seed [--days N] [--force]\n  Seed current profile with realistic demo data\n  --days N   Number of days of history (default 45)\n  --force    Overwrite existing data without prompt",
    "export": "Usage: hive export [output_path]\n  Export current profile data as tar.gz archive\n  Default: ./hive-{profile}-YYYYMMDD.tar.gz",
    "import": "Usage: hive import <path.tar.gz> [--profile name]\n  Import data from archive\n  --profile name  Create new profile and import there",
    "telemetry": "Usage: hive telemetry\n  Show usage telemetry (session counts, token usage)",
    "wander": (
        "Usage: hive wander [list|show [slug]|seed <text>|run]  (alias: w, wr)\n"
        "  list (l)    Recent wander documents\n"
        "  show (s)    Show a specific wander doc (default: most recent)\n"
        "  seed (sd)   Queue a topic for the next wander run\n"
        "  run (r)     Trigger wander immediately\n"
        "  KingBee's free-thinking log. Runs daily at 14:00 when enabled.\n"
        "  Enable: hive daemon enable wander\n"
    ),
    "inbox": "Usage: hive inbox [--days N]\n  What KingBee generated for you today.\n  Shows wander docs, morning briefings, standup drafts, and review queues.\n  --days N  Days of history to include (default 2 = today + yesterday, max 30)",
    "run": (
        'Usage: hive run "<task>" [--max-time DURATION] [--background] [--at HH:MM] [--tonight]  (alias: rn)\n'
        "  Run an autonomous iteration loop on a task.\n"
        "  Modes:\n"
        "    (no flags)     In-session stop-hook loop\n"
        "    --background   New tmux window (requires tmux)\n"
        "    --at HH:MM     Schedule via daemon\n"
        "    --tonight      Schedule for tonight at 22:00\n"
        "  Options:\n"
        "    --max-time DURATION   Time limit: 2h, 30m, 90m, 3600 (seconds). Default: no limit.\n"
        "    --safe         Read-only mode\n"
        "  Subcommands:\n"
        "    hive run status          Active loops\n"
        "    hive run cancel [id]     Cancel loop(s)\n"
        "    hive run cancel --all    Cancel all\n"
        "    hive run history         Past loops\n"
        "    hive run review          Review extracted facts"
    ),
    "privacy": (
        "Usage: hive privacy [on|off|cli|status]  (alias: pv)\n"
        "  Manage LLM privacy and billing controls.\n"
        "    on      Block all LLM calls (full kill switch)\n"
        "    off     Full reset — unblocks LLM calls and allows all backends\n"
        "    cli     Route all LLM calls through claude -p only (ignore API keys)\n"
        "    status  Show current state (default)\n"
        "  Flag files: .llm-paused (kill switch), .force-cli (CLI-only mode)\n"
        "  `off` clears both flags."
    ),
    "growth": "Usage: hive growth [--json]\n  How keephive compounds over time: 30-day trends, week-over-week deltas, impact.\n  --json  Machine-readable output",
}

# Map aliases to canonical names for help lookup
_CANONICAL: dict[str, str] = {
    "s": "status",
    "r": "remember",
    "rc": "recall",
    "v": "verify",
    "rf": "reflect",
    "l": "log",
    "e": "edit",
    "n": "note",
    "d": "note",
    "nc": "note",
    "dc": "note",
    "m": "mem",
    "td": "todo",
    "to": "todo",
    "t": "todo",
    "su": "standup",
    "k": "knowledge",
    "ke": "knowledge",
    "p": "knowledge",
    "pe": "knowledge",
    "sk": "skill",
    "a": "audit",
    "g": "gc",
    "dr": "doctor",
    "st": "stats",
    "go": "session",
    "sesh": "session",
    "up": "update",
    "draft": "note",
    "rl": "rule",
    "rn": "run",
    "ws": "serve",
    "pf": "profile",
    "daemon": "daemon",
    "improve": "improve",
    "flow": "flow",
    "ck": "checkup",
    "checkup": "checkup",
    "cfg": "config",
    "config": "config",
    "wander": "wander",
    "wr": "wander",
    "w": "wander",
    "ib": "inbox",
    "inbox": "inbox",
    "im": "improve",
    "dm": "daemon",
    "fw": "flow",
    "mr": "mem",
    "rr": "rule",
    "pv": "privacy",
    "gr": "growth",
    "growth": "growth",
}

# Command families: (display_label, description, shorthand, tracked_aliases)
# Order = priority for Discover section (core daily-use first, plumbing last).
_CMD_FAMILIES: list[tuple[str, str, str, set[str]]] = [
    ("status", "Status at a glance", "s", {"s", "status"}),
    ("remember <text>", "Save to daily log", "r", {"r", "remember"}),
    ("recall <query>", "Search all memory tiers", "rc", {"rc", "recall"}),
    ("todo", "Add/complete TODOs", "t", {"t", "td", "to", "todo"}),
    ("verify", "Check stale facts", "v", {"v", "verify"}),
    ("log [date]", "View daily log", "l", {"l", "log"}),
    ("edit [target]", "Edit memory, rules, etc.", "e", {"e", "edit"}),
    ("note", "Multi-slot scratchpad", "n", {"n", "note", "d", "draft", "nc", "dc"}),
    ("knowledge [name]", "View/edit knowledge guides", "k", {"k", "ke", "knowledge"}),
    ("prompt [name]", "Prompt templates", "p", {"p", "prompt", "pe"}),
    ("reflect", "Find patterns in logs", "rf", {"rf", "reflect"}),
    ("audit [-v]", "Quality analysis", "a", {"a", "audit"}),
    ("flow [--skip-verify]", "Guided maintenance run", "fw", {"flow", "fw"}),
    ("checkup [--json]", "Production health check", "ck", {"checkup", "ck"}),
    ("daemon [run|status|enable|disable]", "KingBee background daemon", "dm", {"daemon", "dm"}),
    ("wander [list|seed|run]", "Agent free-thinking log", "w", {"wander", "wr", "w"}),
    ("inbox [--days N]", "KingBee output + review queue", "ib", {"inbox", "ib"}),
    ('run "<task>" [--max-time]', "Autonomous iteration loop", "rn", {"run", "rn"}),
    ("improve [review]", "Review self-improvement proposals", "im", {"improve", "im"}),
    ("go [mode]", "Launch session", "go", {"go", "sesh", "session"}),
    ("growth [--json]", "How keephive compounds", "gr", {"gr", "growth"}),
    ("stats [-p path]", "Usage + pipeline health", "st", {"st", "stats"}),
    ("mem [rm] <text>", "Add/remove working memory", "m", {"m", "mem", "mr"}),
    ("serve [port]", "Live web dashboard", "ws", {"ws", "serve"}),
    ("doctor", "Check setup + find duplicates", "dr", {"dr", "doctor"}),
    ("standup", "Generate standup summary", "su", {"su", "standup"}),
    ("gc", "Archive old logs", "g", {"g", "gc"}),
    ("rule [learn|review]", "Add/remove/learn rules", "rl", {"rule", "rl", "rr"}),
    ("set [key] [val]", "View/change settings", "", {"set"}),
    ("config llm-backend", "Manage LLM backend selection", "", {"config", "cfg"}),
    ("skill", "Manage skill plugins", "sk", {"sk", "skill"}),
    ("update", "Upgrade keephive in-place", "up", {"up", "update"}),
    ("ps", "Active sessions + git state", "", {"ps"}),
    ("setup", "Initial setup", "", {"setup"}),
    ("sound-test", "Play notification sound", "", {"sound-test"}),
    ("ui [install|clr]", "UI feedback queue", "", {"ui", "ui-install", "ui-clear"}),
    ("profile [cmd]", "Manage data profiles", "pf", {"pf", "profile"}),
    ("seed [--days N]", "Seed demo data", "", {"seed"}),
    ("export [path]", "Export data as tar.gz", "", {"export"}),
    ("import <path>", "Import data archive", "", {"import"}),
    (
        "privacy [on|off|cli]",
        "Pause/resume LLM calls or restrict to claude -p",
        "pv",
        {"privacy", "pv"},
    ),
]


def _command_usage(days: int = 7) -> tuple[dict[int, int], set[int]]:
    """Aggregate command usage by family index.

    Returns (recent_counts, all_time_indices):
      recent_counts: {family_idx: total_invocations_in_last_N_days}
      all_time_indices: set of family indices ever used
    """
    try:
        from datetime import timedelta

        from keephive.clock import get_today
        from keephive.storage import read_stats

        data = read_stats()
        if not data.get("days"):
            return {}, set()

        cutoff = (get_today() - timedelta(days=days)).isoformat()
        recent: dict[int, int] = {}
        all_time: set[int] = set()

        for day_str, day_data in data["days"].items():
            cmds = day_data.get("commands", {})
            if not cmds:
                continue
            is_recent = day_str >= cutoff

            for idx, (_, _, _, aliases) in enumerate(_CMD_FAMILIES):
                count = 0
                for alias in aliases:
                    count += cmds.get(alias, 0)
                # note.N slots (note.0 through note.9) count toward the note family
                if aliases & {"n", "note", "d", "draft", "nc", "dc"}:
                    for k, v in cmds.items():
                        if k.startswith("note."):
                            count += v
                if count > 0:
                    all_time.add(idx)
                    if is_recent:
                        recent[idx] = recent.get(idx, 0) + count

        return recent, all_time
    except Exception:
        return {}, set()


def _print_cmd_line(label: str, desc: str, shorthand: str) -> None:
    """Print a single command line with consistent column formatting."""
    print(f"    {label:<18s}{desc:<34s}{shorthand}")


def _help_grouped(show_all: bool = False) -> None:
    """Print help text."""
    print(f"""keephive v{__version__}
Preserves what your agent discovers. Checks if it's still true.

Usage: hive <command> [args]

  Capture & Search                                 Shorthand
    remember <text>   Save to daily log                r
    recall <query>    Search all memory tiers           rc
    todo <text>       Add a TODO                        t
    todo done <pat>   Mark complete                     td
    verify            Check stale facts                 v
    prompt [name]     Prompt templates                  p

  Workflows
    status            Status at a glance                s
    reflect           Find patterns in logs             rf
    audit [-v]        Quality analysis                  a
    flow              Guided maintenance run             fw
    wander [list|run] Agent free-thinking log           w
    inbox [--days N]  KingBee output + review queue     ib
    run "<task>"      Autonomous iteration loop         rn
    go [mode]         Launch session                    go
    growth [--json]   How keephive compounds             gr
    stats [-p path]   Usage + pipeline health           st
    log [date]        View daily log                    l

  Manage
    edit [target]     Edit memory, rules, etc.          e
    knowledge [name]  View/edit knowledge guides        k
    note              Multi-slot scratchpad              n
    mem [rm] <text>   Add/remove working memory         m
    rule [learn|review] Add/remove/learn rules           rl
    serve [port]      Live web dashboard""")

    if show_all:
        print("""
  Plumbing
    daemon [cmd]      KingBee background daemon         dm
    checkup [--json]  Production health check           ck
    improve [review]  Review self-improvement proposals  im
    todo repeat       Manage recurring tasks
    set [key] [val]   View/change settings
    sound-test        Play notification sound
    gc                Archive old logs
    doctor            Check setup + find duplicates     dr
    standup           Generate standup summary           su
    ps                Active sessions + git state
    skill             Manage skill plugins               sk
    update            Upgrade keephive in-place          up
    setup             Initial setup
    ui [install|clr]  UI feedback queue (bookmarklet)""")

    print("""
  Run 'hive help <cmd>' for details.""")
    if not show_all:
        print("  Run 'hive help --all' for all commands.")


def _help(show_all: bool = False) -> None:
    """Adaptive help: Recent + Discover when stats exist, grouped otherwise."""
    if show_all:
        _help_grouped(show_all=True)
        return

    recent, all_time = _command_usage(7)

    # No usage data at all: fall back to grouped layout (new user)
    if not all_time:
        _help_grouped()
        return

    # Build sections
    recent_items = sorted(recent.items(), key=lambda kv: -kv[1])
    discover_indices = [i for i in range(len(_CMD_FAMILIES)) if i not in all_time]
    discover_cap = 6

    # Edge case: both sections would be empty (used everything, none recent)
    if not recent_items and not discover_indices:
        _help_grouped()
        return

    print(f"keephive v{__version__}")
    print("Preserves what your agent discovers. Checks if it's still true.")
    print()
    print("Usage: hive <command> [args]")

    if recent_items:
        print()
        print(f"  {'Recent':<52s}Shorthand")
        for idx, _ in recent_items:
            label, desc, shorthand, _ = _CMD_FAMILIES[idx]
            _print_cmd_line(label, desc, shorthand)

    if discover_indices:
        print()
        print("  Discover")
        shown = discover_indices[:discover_cap]
        for idx in shown:
            label, desc, shorthand, _ = _CMD_FAMILIES[idx]
            _print_cmd_line(label, desc, shorthand)
        overflow = len(discover_indices) - discover_cap
        if overflow > 0:
            print(f"    ... and {overflow} more (hive help --all)")

    print()
    print("  Run 'hive help <cmd>' for details.")
    print("  Run 'hive help --all' for all commands.")


# Dispatch table: command -> (handler_module, handler_function)
# Lazy imports to keep startup fast.
COMMANDS: dict[str, tuple[str, str]] = {
    "s": ("keephive.commands.status", "cmd_status"),
    "status": ("keephive.commands.status", "cmd_status"),
    "r": ("keephive.commands.remember", "cmd_remember"),
    "remember": ("keephive.commands.remember", "cmd_remember"),
    "rc": ("keephive.commands.remember", "cmd_recall"),
    "recall": ("keephive.commands.remember", "cmd_recall"),
    "v": ("keephive.commands.verify", "cmd_verify"),
    "verify": ("keephive.commands.verify", "cmd_verify"),
    "rf": ("keephive.commands.reflect", "cmd_reflect"),
    "reflect": ("keephive.commands.reflect", "cmd_reflect"),
    "l": ("keephive.commands.log", "cmd_log"),
    "log": ("keephive.commands.log", "cmd_log"),
    "e": ("keephive.commands.edit", "cmd_edit"),
    "edit": ("keephive.commands.edit", "cmd_edit"),
    "n": ("keephive.commands.note", "cmd_note"),
    "note": ("keephive.commands.note", "cmd_note"),
    "nc": ("keephive.commands.note", "cmd_note_copy"),
    "d": ("keephive.commands.note", "cmd_note"),
    "draft": ("keephive.commands.note", "cmd_note"),
    "dc": ("keephive.commands.note", "cmd_note_copy"),
    "m": ("keephive.commands.memory", "cmd_mem"),
    "mem": ("keephive.commands.memory", "cmd_mem"),
    "mr": ("keephive.commands.memory", "cmd_mem_review"),
    "rule": ("keephive.commands.memory", "cmd_rule"),
    "rl": ("keephive.commands.memory", "cmd_rule_learn"),
    "rr": ("keephive.commands.memory", "cmd_rule_review"),
    "to": ("keephive.commands.todo", "cmd_todo"),
    "td": ("keephive.commands.todo", "cmd_td"),
    "todo": ("keephive.commands.todo", "cmd_todo"),
    "t": ("keephive.commands.todo", "cmd_t"),
    "su": ("keephive.commands.standup", "cmd_standup"),
    "standup": ("keephive.commands.standup", "cmd_standup"),
    "k": ("keephive.commands.knowledge", "cmd_knowledge"),
    "knowledge": ("keephive.commands.knowledge", "cmd_knowledge"),
    "ke": ("keephive.commands.knowledge", "cmd_knowledge_edit"),
    "p": ("keephive.commands.knowledge", "cmd_prompt"),
    "prompt": ("keephive.commands.knowledge", "cmd_prompt"),
    "pe": ("keephive.commands.knowledge", "cmd_prompt_edit"),
    "sk": ("keephive.commands.skill", "cmd_skill"),
    "skill": ("keephive.commands.skill", "cmd_skill"),
    "session": ("keephive.commands.session", "cmd_session"),
    "sesh": ("keephive.commands.session", "cmd_session"),
    "go": ("keephive.commands.session", "cmd_session"),
    "a": ("keephive.commands.audit", "cmd_audit"),
    "audit": ("keephive.commands.audit", "cmd_audit"),
    "g": ("keephive.commands.gc", "cmd_gc"),
    "gc": ("keephive.commands.gc", "cmd_gc"),
    "dr": ("keephive.commands.doctor", "cmd_doctor"),
    "doctor": ("keephive.commands.doctor", "cmd_doctor"),
    "setup": ("keephive.commands.setup", "cmd_setup"),
    "st": ("keephive.commands.stats", "cmd_stats"),
    "stats": ("keephive.commands.stats", "cmd_stats"),
    "update": ("keephive.commands.update", "cmd_update"),
    "up": ("keephive.commands.update", "cmd_update"),
    "ps": ("keephive.commands.ps", "cmd_ps"),
    "config": ("keephive.commands.config", "cmd_config"),
    "cfg": ("keephive.commands.config", "cmd_config"),
    "set": ("keephive.commands.settings", "cmd_set"),
    "sound-test": ("keephive.commands.settings", "cmd_sound_test"),
    "serve": ("keephive.commands.serve", "cmd_serve"),
    "ws": ("keephive.commands.serve", "cmd_serve"),
    "ui": ("keephive.commands.ui", "cmd_ui"),
    "ui-install": ("keephive.commands.ui", "cmd_ui_install"),
    "ui-clear": ("keephive.commands.ui", "cmd_ui_clear"),
    "profile": ("keephive.commands.profile", "cmd_profile"),
    "pf": ("keephive.commands.profile", "cmd_profile"),
    "seed": ("keephive.commands.seed", "cmd_seed"),
    "export": ("keephive.commands.transfer", "cmd_export"),
    "import": ("keephive.commands.transfer", "cmd_import"),
    "hook-precompact": ("keephive.hooks.precompact", "hook_precompact"),
    "hook-sessionstart": ("keephive.hooks.sessionstart", "hook_sessionstart"),
    "hook-posttooluse": ("keephive.hooks.posttooluse", "hook_posttooluse"),
    "hook-userpromptsubmit": ("keephive.hooks.userpromptsubmit", "hook_userpromptsubmit"),
    "hook-stop": ("keephive.hooks.stop", "hook_stop"),
    "hook-sessionend": ("keephive.hooks.sessionend", "hook_sessionend"),
    "hook-taskcompleted": ("keephive.hooks.taskcompleted", "hook_taskcompleted"),
    "hook-subagent-stop": ("keephive.hooks.subagent_stop", "hook_subagent_stop"),
    "hook-notification": ("keephive.hooks.notification", "hook_notification"),
    "daemon": ("keephive.commands.daemon", "cmd_daemon"),
    "dm": ("keephive.commands.daemon", "cmd_daemon"),
    "daemon-loop": ("keephive.commands.daemon", "cmd_daemon_loop"),
    "improve": ("keephive.commands.improve", "cmd_improve"),
    "im": ("keephive.commands.improve", "cmd_improve"),
    "flow": ("keephive.commands.flow", "cmd_flow"),
    "fw": ("keephive.commands.flow", "cmd_flow"),
    "checkup": ("keephive.commands.checkup", "cmd_checkup"),
    "ck": ("keephive.commands.checkup", "cmd_checkup"),
    "telemetry": ("keephive.commands.telemetry", "cmd_telemetry"),
    "wander": ("keephive.commands.wander", "cmd_wander"),
    "wr": ("keephive.commands.wander", "cmd_wander"),
    "w": ("keephive.commands.wander", "cmd_wander"),
    "inbox": ("keephive.commands.inbox", "cmd_inbox"),
    "ib": ("keephive.commands.inbox", "cmd_inbox"),
    "run": ("keephive.commands.loop", "cmd_loop"),
    "rn": ("keephive.commands.loop", "cmd_loop"),
    "loop-extract": ("keephive.commands.loop", "cmd_loop_extract"),
    "privacy": ("keephive.commands.privacy", "cmd_privacy"),
    "pv": ("keephive.commands.privacy", "cmd_privacy"),
    "growth": ("keephive.commands.growth", "cmd_growth"),
    "gr": ("keephive.commands.growth", "cmd_growth"),
}


def main(args: list[str] | None = None) -> None:
    if args is None:
        args = sys.argv[1:]

    if not args:
        # Default: show status
        args = ["s"]

    cmd = args[0]

    if cmd in ("h", "-h", "--help", "help"):
        show_all = "--all" in args[1:] if len(args) > 1 else False
        # Per-command help: hive help <cmd>
        if len(args) > 1 and not args[1].startswith("-"):
            canonical = _CANONICAL.get(args[1], args[1])
            if canonical in HELP:
                print(HELP[canonical])
                return
        _help(show_all=show_all)
        return

    if cmd in ("--version", "-v"):
        print(f"keephive v{__version__}")
        return

    if cmd == "mcp-serve":
        from keephive.mcp_server import main as mcp_main

        mcp_main()
        return

    # Dot notation for note slots: n.3, d.5, n.0, n.3c, d.5c
    m = re.match(r"^[nd]\.(\d)(c?)$", cmd)
    if m:
        digit = int(m.group(1))
        copy_flag = m.group(2)
        slot = 10 if digit == 0 else digit

        # Track usage
        try:
            import os

            from keephive.storage import _detect_source, track_event

            track_event("commands", f"note.{digit}", project=os.getcwd(), source=_detect_source())
        except Exception:
            pass

        import importlib

        mod = importlib.import_module("keephive.commands.note")
        try:
            if copy_flag:
                mod.cmd_note_slot(slot, ["copy"])
            else:
                mod.cmd_note_slot(slot, args[1:])
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            sys.exit(130)
        return

    # Bare digit: hive 4 → hive n.4, hive 4 todo → hive n.4 todo
    if re.match(r"^[0-9]$", cmd):
        digit = int(cmd)
        slot = 10 if digit == 0 else digit

        # Track usage
        try:
            import os

            from keephive.storage import _detect_source, track_event

            track_event("commands", f"note.{digit}", project=os.getcwd(), source=_detect_source())
        except Exception:
            pass

        import importlib

        mod = importlib.import_module("keephive.commands.note")
        try:
            mod.cmd_note_slot(slot, args[1:])
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            sys.exit(130)
        return

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print("Run 'hive help' for usage")
        sys.exit(1)

    # Per-command help
    if any(a in ("--help", "-h") for a in args[1:]):
        canonical = _CANONICAL.get(cmd, cmd)
        if canonical in HELP:
            print(HELP[canonical])
        else:
            _help()
        return

    module_path, func_name = COMMANDS[cmd]

    # Track usage (silent on error, never blocks)
    if not cmd.startswith("hook-"):
        try:
            import os

            from keephive.storage import _detect_source, track_event

            # Canonicalize so aliases (v/verify, rf/reflect) map to one stats key
            tracked_cmd = _CANONICAL.get(cmd, cmd)
            track_event("commands", tracked_cmd, project=os.getcwd(), source=_detect_source())
        except Exception:
            pass

    # Lazy import
    import importlib

    mod = importlib.import_module(module_path)
    handler = getattr(mod, func_name)
    try:
        handler(args[1:])
    except KeyboardInterrupt:
        sys.stdout.write("\ncancelled\n")
        sys.exit(130)
