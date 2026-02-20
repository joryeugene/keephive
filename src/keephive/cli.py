"""CLI dispatch for keephive. Flat command table, no framework."""

from __future__ import annotations

import re
import sys

from keephive import __version__

# Per-command help strings, keyed by canonical command name.
HELP: dict[str, str] = {
    "status": "Usage: hive s\n  Status overview (facts, TODOs, stale warnings)",
    "remember": "Usage: hive r <text>\n  Save insight to daily log\n  Prefix with FACT:/DECISION:/TODO:/INSIGHT:/CORRECTION: for categorization",
    "recall": "Usage: hive rc <query> [--deep] [--json]\n  Search all memory tiers\n  --deep  Expand search with AI when few results found",
    "verify": "Usage: hive v [--check] [--json] [--verbose]\n  Verify facts against codebase using LLM\n  --check  Quick stale count, exit code 1 if stale\n  --verbose  Show raw LLM output",
    "reflect": "Usage: hive rf [scan|analyze|apply|draft <topic>]\n  scan     Quick log scan (no AI)\n  analyze  Pattern detection with AI (~20s)\n  apply    Review and graduate analysis to memory\n  draft    Draft a knowledge guide from logs",
    "log": "Usage: hive l [date|summarize]\n  View daily log. Date: today, yesterday, N (days ago), YYYY-MM-DD\n  summarize  AI summary of today's entries (3-5 bullets)",
    "edit": "Usage: hive e [target]\n  Targets: memory, rules, claude, settings, local, today, note\n  No args: show available targets",
    "todo": "Usage: hive todo [done <pat>] [repeat [freq] [text]]\n  todo         List open TODOs\n  todo done X  Mark TODO matching X complete\n  todo repeat  List/add recurring tasks",
    "note": 'Usage: hive n [show|copy|clear|list|<slot>|<template>]\n  n          Open active slot in $EDITOR\n  n.3        Switch to slot 3, open editor (1-9, 0=10)\n  4          Open slot 4 in $EDITOR (bare-digit shorthand)\n  n show     Print content\n  n copy     Copy to clipboard\n  n clear    Archive and clear\n  n list     Show all slots\n  n <N> todo  Extract TODOs from slot N and add to daily log\n  4 "text"   Append text to slot 4 without opening editor',
    "knowledge": "Usage: hive k [name|edit <name>|rm <name>]\n  k           List all guides and prompts\n  k <name>    View guide (prefix match)\n  k edit X    Create/edit guide\n  k rm X      Remove guide",
    "audit": "Usage: hive a [-v] [--json]\n  Quality Pulse: 3-perspective LLM analysis + synthesis\n  -v      Show full perspective essays\n  --json  Machine-readable output",
    "doctor": "Usage: hive dr\n  Health check: hooks, MCP, deps, data integrity\n  Uses LLM for semantic TODO dedup (deterministic fallback if unavailable)",
    "gc": "Usage: hive gc [--dry-run]\n  Archive daily logs older than 30 days\n  --dry-run  Show what would be archived without doing it",
    "standup": "Usage: hive su\n  Generate standup summary from daily logs + GitHub PRs\n  Uses LLM for formatting. Copies to clipboard.",
    "stats": "Usage: hive st [-p <project>] [date]\n  Usage statistics. Date: today, N (days ago), YYYY-MM-DD",
    "mem": "Usage: hive m [rm] <text>\n  Add or remove working memory facts\n  hive m <text>      Add fact to memory.md\n  hive m rm <pat>    Remove line matching pattern",
    "rule": "Usage: hive rule [rm|review] <text>\n  Add or remove behavioral rules\n  hive rule <text>      Add rule\n  hive rule rm <pat>    Remove matching rule\n  hive rule review      Review pending rule suggestions from PreCompact hook",
    "session": "Usage: hive go [mode|prompt]\n  Modes: todo, verify, learn, reflect\n  Or load a custom prompt from knowledge/prompts/",
    "skill": "Usage: hive sk [publish <name>|unpublish <name>|sync|find <q>]\n  Manage skill plugins",
    "update": "Usage: hive up\n  Upgrade keephive to the latest version in-place",
    "setup": "Usage: hive setup\n  Initial setup: register MCP server + hooks in ~/.claude/",
    "ps": "Usage: hive ps\n  Local hive map: active claude sessions, recent project activity, git state",
    "serve": "Usage: hive serve [port] [--hot]\n  Live web dashboard at localhost:3847 (default)\n  Views: / /daily /dev /simple /stats /know /mem /notes\n  --hot  Watch source files, auto-restart on change",
    "ui": "Usage: hive ui [install|clear]\n  ui           Show pending UI feedback queue\n  ui-install   Print bookmarklet URL (drag to bookmarks bar)\n  ui-clear     Clear pending feedback",
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
    "ws": "serve",
}


def _help() -> None:
    """Print help text."""
    print(f"""keephive v{__version__}  -  a knowledge sidecar for Claude Code

Usage: hive <command> [args]

Daily
  s, status            Status overview
  r, remember <text>   Save insight to daily log
  rc, recall <query>   Search all memory tiers
  l, log [date]        View daily log (today, yesterday, N, YYYY-MM-DD)
  m, mem [rm] <text>   Add/remove working memory facts
  rule [rm] <text>     Add/remove behavioral rules
  rule review          Review queued rule suggestions

Todo
  to, todo             List open TODOs + due recurring
  t <text>             Quick add TODO
  td <pat>             Mark TODO done (also: t done <pat>, t d <pat>)
  todo done <pat>      Complete a TODO or recurring task
  todo repeat <freq> <text>   Add recurring (daily/weekly/2d/12h)
  e todo               Bulk-edit TODOs in $EDITOR

Knowledge
  k [name]             List or view guides (prefix match)
  ke <name>            Edit/create a guide
  p [name]             List or view prompts
  pe <name>            Edit/create a prompt
  sk, skill            Manage skills

Notes
  n, note              Open scratchpad in $EDITOR
  draft                (alias for n)
  4                    Open slot 4 in $EDITOR (bare-digit shorthand for n.4)
  n.3                  Switch to slot 3, open editor
  nc / n.3c            Copy to clipboard
  n list               Show all slots
  n <N> todo           Extract TODOs from slot N
  4 "text"             Append text to slot 4 without opening editor

Sessions (run 'hive s' first - signals tell you which to use)
  go, session          General session with full context
  session todo         Triage open TODOs          (when TODOs pile up)
  session verify       Fix stale facts            (when status shows stale)
  session learn        Active recall quiz          (to test retention)
  session reflect      Find patterns in logs       (after a week of work)
  session <prompt>     Load custom prompt from knowledge/prompts/

Analysis
  v, verify            Verify stale facts (claude -p)
  rf, reflect          Review daily logs (scan / analyze / apply / draft)
  a, audit [-v]        Quality pulse (score + actions)
  su, standup          Generate standup summary
  st, stats [-p path]  Usage statistics
  ps                   Active sessions, project activity, git state

Maintenance
  e, edit [target]     Edit file (memory/rules/claude/settings/local/note/today)
  g, gc                Archive old logs
  dr, doctor           Check setup + find duplicate TODOs
  up, update           Upgrade keephive in-place
  setup                Initial setup

Dashboard
  serve [port]         Live web dashboard (localhost:3847)
  ui [install|clear]   UI feedback queue (bookmarklet → Claude Code)

  h, help / --help     Show this help
  --version            Show version
""")


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
    "rule": ("keephive.commands.memory", "cmd_rule"),
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
    "serve": ("keephive.commands.serve", "cmd_serve"),
    "ws": ("keephive.commands.serve", "cmd_serve"),
    "ui": ("keephive.commands.ui", "cmd_ui"),
    "ui-install": ("keephive.commands.ui", "cmd_ui_install"),
    "ui-clear": ("keephive.commands.ui", "cmd_ui_clear"),
    "hook-precompact": ("keephive.hooks.precompact", "hook_precompact"),
    "hook-sessionstart": ("keephive.hooks.sessionstart", "hook_sessionstart"),
    "hook-posttooluse": ("keephive.hooks.posttooluse", "hook_posttooluse"),
    "hook-userpromptsubmit": ("keephive.hooks.userpromptsubmit", "hook_userpromptsubmit"),
}


def main(args: list[str] | None = None) -> None:
    if args is None:
        args = sys.argv[1:]

    if not args:
        # Default: show status
        args = ["s"]

    cmd = args[0]

    if cmd in ("h", "-h", "--help", "help"):
        _help()
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

            track_event("commands", cmd, project=os.getcwd(), source=_detect_source())
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
