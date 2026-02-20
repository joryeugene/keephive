"""Interactive session launcher.

Opens an interactive claude session with full keephive context
pre-loaded and a purpose-specific opening prompt.
"""

from __future__ import annotations

import os
import shutil
import sys
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

    session_prompt = _build_session_prompt(mode, prompt, context, piped_content)

    try:
        from keephive.storage import track_event

        track_event("commands", f"session.{mode}", project=cwd, source="terminal")
    except Exception:
        pass

    flag_desc = " ".join(claude_flags) + " " if claude_flags else ""
    print(f"Launching {mode} session... {flag_desc}")

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    # Build claude argv: flags go before the prompt positional arg
    claude_argv = ["claude"] + claude_flags + [session_prompt]
    os.execvpe("claude", claude_argv, env)
