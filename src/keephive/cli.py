"""CLI dispatch for keephive. Flat command table, no framework."""

from __future__ import annotations

import re
import sys

from keephive import __version__


def _help() -> None:
    """Print help text."""
    print(f"""keephive v{__version__}  -  a knowledge sidecar for Claude Code

Usage: hive <command> [args]

Memory
  s, status            Status overview
  r, remember <text>   Save insight to daily log
  rc, recall <query>   Search all memory tiers
  m, mem [rm] <text>   Add/remove working memory facts
  rule [rm] <text>     Add/remove behavioral rules
  l, log [date]        View daily log (today, yesterday, N, YYYY-MM-DD)

Todo
  todo, td             List open TODOs + due recurring
  t <text>             Quick add TODO
  todo done <pat>      Complete a TODO or recurring task
  todo repeat <freq> <text>   Add recurring (daily/weekly/2d/12h)

Knowledge
  k [name]             List or view guides (prefix match)
  ke <name>            Edit/create a guide
  p [name]             List or view prompts
  pe <name>            Edit/create a prompt
  sk, skill            Manage skills

Notes
  n, note              Open scratchpad in $EDITOR
  n.3                  Switch to slot 3
  nc / n.3c            Copy to clipboard
  n list               Show all slots

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

Maintenance
  e, edit [target]     Edit file (memory/rules/claude/settings/note/today)
  g, gc                Archive old logs
  dr, doctor           Check setup + find duplicate TODOs
  setup                Initial setup

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
    "td": ("keephive.commands.todo", "cmd_todo"),
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
    "sess": ("keephive.commands.session", "cmd_session"),
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

    if cmd == "--version":
        print(f"keephive v{__version__}")
        return

    if cmd == "mcp-serve":
        from keephive.mcp_server import main as mcp_main
        mcp_main()
        return

    # Dot notation for note slots: n.3, d.5, n.0, n.3c, d.5c
    m = re.match(r'^[nd]\.(\d)(c?)$', cmd)
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
        if copy_flag:
            mod.cmd_note_slot(slot, ["copy"])
        else:
            mod.cmd_note_slot(slot, args[1:])
        return

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print("Run 'keephive help' for usage")
        sys.exit(1)

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
    handler(args[1:])
