"""Interactive session launcher.

Opens an interactive claude session with full keephive context
pre-loaded and a purpose-specific opening prompt.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

# Built-in mode prompts. Each gets the full context prepended.
_MODE_PROMPTS: dict[str, str] = {
    "default": (
        "You have keephive context loaded above. "
        "You can use keephive CLI commands (hive r, hive rc, hive todo, etc.) "
        "or MCP tools (hive_remember, hive_recall, etc.) throughout this session. "
        "What would you like to work on?"
    ),
    "todo": (
        "Here are your open TODOs from keephive context above. "
        "Go through each one. For each: decide if it's actionable, stale, or done. "
        'Use `hive todo done "<pattern>"` to close completed items. '
        'Use `hive r "TODO: <text>"` to rewrite unclear ones. '
        "Let's triage them one by one."
    ),
    "verify": (
        "Review the stale facts shown in the keephive context above. "
        "For each stale fact: check against the codebase using Read/Grep tools, "
        'then use `hive r "FACT: <corrected>"` to update or '
        '`hive mem rm "<old fact>"` to remove outdated ones. '
        "Let's verify them one at a time."
    ),
    "learn": (
        "Quiz me on what I've learned recently. "
        "Pick facts and decisions from the working memory and recent entries above. "
        "Ask me to explain them from memory before showing the answer. "
        "Track which ones I struggle with. "
        "This is active recall practice for durable learning."
    ),
    "reflect": (
        "Review the daily log entries from the past week shown above. "
        "What patterns do you see? What keeps recurring? "
        "What insights should become a permanent rule or guide? "
        'Use `hive r "INSIGHT: <pattern>"` to capture findings. '
        "Use `hive ke <name>` to draft a knowledge guide if warranted."
    ),
    "kb": "",  # Populated dynamically in cmd_session — reads KB queue + SOUL.md
}


def _parse_args(args: list[str]) -> tuple[list[str], list[str]]:
    """Split claude pass-through flags from session mode/prompt args.

    Recognized flags:
      -c / --continue          → pass -c to claude
      -r / --resume [id]       → pass --resume [id] to claude

    Returns (claude_flags, remaining_args).
    """
    claude_flags: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-c", "--continue"):
            claude_flags.append("--continue")
            i += 1
        elif arg in ("-r", "--resume"):
            claude_flags.append("--resume")
            i += 1
            # Optional session ID: next arg that doesn't start with "-"
            # and comes before any remaining positional args
            if i < len(args) and not args[i].startswith("-"):
                # If there are still more args after this one, treat it as an ID.
                # If it's the last arg, it's the prompt, not an ID.
                if i + 1 < len(args):
                    claude_flags.append(args[i])
                    i += 1
                # else: fall through, last arg goes to remaining as the prompt
        else:
            remaining.append(arg)
            i += 1
    return claude_flags, remaining


def _read_stdin_if_piped() -> str | None:
    """Read stdin if it's a pipe. Returns content or None.

    After reading, redirects fd 0 to /dev/tty so the child claude
    process gets an interactive terminal.
    """
    if sys.stdin.isatty():
        return None
    try:
        content = sys.stdin.read()
        # Reattach stdin to the controlling terminal so claude is interactive
        tty_fd = os.open("/dev/tty", os.O_RDONLY)
        os.dup2(tty_fd, 0)
        os.close(tty_fd)
        return content.strip() or None
    except OSError:
        return None


def _resolve_mode(args: list[str]) -> tuple[str, str]:
    """Resolve session mode from args.

    Returns (mode_name, prompt_text).
    Falls back to checking knowledge/prompts/ for custom prompt templates.
    """
    if not args:
        return "default", _MODE_PROMPTS["default"]

    mode = args[0].lower()

    # Built-in mode
    if mode in _MODE_PROMPTS:
        return mode, _MODE_PROMPTS[mode]

    # Custom prompt from knowledge/prompts/
    from keephive.storage import prompts_dir

    pd = prompts_dir()
    if pd.exists():
        # Exact match first
        exact = pd / f"{mode}.md"
        if exact.exists():
            return mode, exact.read_text().strip()

        # Prefix match
        for p in sorted(pd.glob("*.md")):
            if p.stem.lower().startswith(mode):
                return p.stem, p.read_text().strip()

    # If nothing matched, treat args as a freeform prompt
    return "custom", " ".join(args)


def _build_session_prompt(mode: str, prompt: str, context: str, piped: str | None = None) -> str:
    """Combine context and mode prompt into the session opening."""
    parts = [
        "# keephive session context\n",
        context,
        "\n---\n",
        f"# Session mode: {mode}\n",
        prompt,
    ]
    if piped:
        parts += [
            "\n\n---\n",
            "# Piped input\n",
            "```\n",
            piped,
            "\n```",
        ]
    return "\n".join(parts)


def cmd_session(args: list[str]) -> None:
    """Launch an interactive claude session with keephive context."""
    if not shutil.which("claude"):
        print("Error: 'claude' CLI not found in PATH.")
        print("Install Claude Code: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    # Read piped stdin before anything touches it
    piped_content = _read_stdin_if_piped()

    # Split flags from mode/prompt args
    claude_flags, remaining = _parse_args(args)

    cwd = os.getcwd()
    project_name = Path(cwd).name

    from keephive.hooks.sessionstart import build_context

    context = build_context(cwd, project_name)

    mode, prompt = _resolve_mode(remaining)

    # KB mode: build prompt dynamically from live queue + SOUL.md
    if mode == "kb":
        try:
            from keephive.storage import kb_queue_depth, read_kb_queue, read_soul

            kb_messages = read_kb_queue()
            pending = [m["text"] for m in kb_messages if m["status"] == "pending"]
            if pending:
                kb_queue_summary = "\n".join(f"- {msg}" for msg in pending)
            else:
                kb_queue_summary = "(no pending messages)"

            soul_text = read_soul() or ""
            # Extract a short summary — first 400 chars of SOUL.md
            soul_summary = soul_text[:400].strip() if soul_text else "(SOUL.md not yet written)"
            depth = kb_queue_depth()

            prompt = (
                "You are KingBee, keephive's agent identity. This is a direct conversation.\n\n"
                f"Pending direct messages ({depth} unprocessed):\n"
                f"{kb_queue_summary}\n\n"
                f"Current SOUL.md summary:\n{soul_summary}\n\n"
                "Start by acknowledging any pending messages, then be present for whatever the "
                "user wants to discuss. If the user gives a behavioral directive (style, tone, "
                "workflow), note it clearly and offer to write it as a rule with "
                "hive_remember('RULE: ...') before the session ends."
            )
        except Exception:
            prompt = (
                "You are KingBee, keephive's agent identity. "
                "This is a direct conversation about your behavior and identity."
            )

    session_prompt = _build_session_prompt(mode, prompt, context, piped_content)

    try:
        from keephive.storage import track_event

        track_event("commands", f"session.{mode}", project=cwd, source="terminal")
    except Exception:
        pass

    flag_desc = " ".join(claude_flags) + " " if claude_flags else ""
    print(f"Launching {mode} session... {flag_desc}")

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["HIVE_SESSION_LAUNCHED"] = "1"  # env var fallback for older hook versions

    # Write file-based signal so the SessionStart hook can detect a hive go session
    # (env var guard doesn't work because Claude Code daemon inherits its own env)
    try:
        from keephive.storage import hive_dir as _hive_dir

        (_hive_dir() / ".session-launched").write_text(str(int(time.time())))
    except Exception:
        pass

    # Build claude argv: flags go before the prompt positional arg
    claude_argv = ["claude"] + claude_flags + [session_prompt]
    os.execvpe("claude", claude_argv, env)
