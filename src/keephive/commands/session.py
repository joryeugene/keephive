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
        "Use `hive todo done \"<pattern>\"` to close completed items. "
        "Use `hive r \"TODO: <text>\"` to rewrite unclear ones. "
        "Let's triage them one by one."
    ),
    "verify": (
        "Review the stale facts shown in the keephive context above. "
        "For each stale fact: check against the codebase using Read/Grep tools, "
        "then use `hive r \"FACT: <corrected>\"` to update or "
        "`hive mem rm \"<old fact>\"` to remove outdated ones. "
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
        "Use `hive r \"INSIGHT: <pattern>\"` to capture findings. "
        "Use `hive ke <name>` to draft a knowledge guide if warranted."
    ),
}


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


def _build_session_prompt(mode: str, prompt: str, context: str) -> str:
    """Combine context and mode prompt into the session opening."""
    parts = [
        "# keephive session context\n",
        context,
        "\n---\n",
        f"# Session mode: {mode}\n",
        prompt,
    ]
    return "\n".join(parts)


def cmd_session(args: list[str]) -> None:
    """Launch an interactive claude session with keephive context."""
    # Verify claude is available
    if not shutil.which("claude"):
        print("Error: 'claude' CLI not found in PATH.")
        print("Install Claude Code: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    cwd = os.getcwd()
    project_name = Path(cwd).name

    # Reuse sessionstart's context builder
    from keephive.hooks.sessionstart import build_context

    context = build_context(cwd, project_name)

    # Resolve mode and prompt
    mode, prompt = _resolve_mode(args)

    # Build the full session prompt
    session_prompt = _build_session_prompt(mode, prompt, context)

    # Track usage
    try:
        from keephive.storage import track_event

        track_event("commands", f"session.{mode}", project=cwd, source="terminal")
    except Exception:
        pass

    print(f"Launching {mode} session...")

    # Strip CLAUDECODE env var so nested session isn't blocked
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    # Replace process with interactive claude
    # Positional arg = first message in interactive mode (not -p which is pipe/non-interactive)
    os.execvpe("claude", ["claude", session_prompt], env)
