"""hive privacy — LLM API call controls.

Two privacy modes, one reset command:
  on    Block ALL LLM calls (full kill switch).
  cli   Route all LLM calls through claude -p only; ignore direct API keys.
  off   Full reset — clears both .llm-paused and .force-cli.

File I/O commands (remember, recall, todo, serve) continue normally in both modes.

Usage:
    hive privacy          Show current state
    hive privacy on       Block all LLM calls
    hive privacy cli      Route through claude -p only (ignore API keys)
    hive privacy off      Full reset — unblock LLM calls and allow all backends
    hive privacy status   Alias for bare hive privacy
"""

from __future__ import annotations

from keephive.output import console
from keephive.storage import hive_dir, is_force_cli, is_llm_paused, set_force_cli, set_llm_paused


def cmd_privacy(args: list[str]) -> None:
    """hive privacy [on|off|cli|status]"""
    sub = args[0].lower() if args else "status"

    if sub == "on":
        set_llm_paused(True)
        _print_state()
    elif sub in ("cli", "cli-only"):
        set_force_cli(True)
        _print_state()
    elif sub == "off":
        # Full reset: clear both kill switch and CLI-only flag
        set_llm_paused(False)
        set_force_cli(False)
        _print_state()
    elif sub in ("status", ""):
        _print_state()
    else:
        console.print(f"[err]Unknown subcommand: {sub}[/err]")
        console.print("  Usage: hive privacy [on|off|cli|status]")


def _print_state() -> None:
    paused = is_llm_paused()
    force_cli = is_force_cli()
    paused_flag = hive_dir() / ".llm-paused"
    cli_flag = hive_dir() / ".force-cli"

    if paused:
        console.print()
        console.print("[bold yellow]🔒 LLM privacy mode: ON[/bold yellow]")
        console.print(
            "   All LLM calls (hive a, hive v, precompact, daemon tasks) are blocked."
        )
        console.print(
            "   File I/O commands (remember, recall, todo, serve) continue normally."
        )
        console.print("   Run [bold]hive privacy off[/bold] to resume.")
        console.print(f"   Flag: [dim]{paused_flag}[/dim]")
        if force_cli:
            console.print()
            console.print("   [bold cyan]🔐 CLI-only mode also active[/bold cyan]")
            console.print(f"   Flag: [dim]{cli_flag}[/dim]")
        console.print()
    elif force_cli:
        console.print()
        console.print("[bold cyan]🔐 CLI-only mode: ACTIVE[/bold cyan]")
        console.print(
            "   Direct API backends (Anthropic API, Gemini, OpenAI) blocked."
        )
        console.print(
            "   All LLM calls route through claude -p (your Pro/Max subscription)."
        )
        console.print("   Run [bold]hive privacy off[/bold] to allow API backends.")
        console.print(f"   Flag: [dim]{cli_flag}[/dim]")
        console.print()
    else:
        console.print()
        console.print("[bold green]🔓 LLM privacy mode: OFF[/bold green]")
        console.print("   LLM calls are enabled. All backends allowed.")
        console.print(
            "   Run [bold]hive privacy on[/bold] to block all calls, "
            "or [bold]hive privacy cli[/bold] to restrict to claude -p only."
        )
        console.print()
