"""hive off / hive on: disable or re-enable all keephive activity."""

from __future__ import annotations

from keephive.output import console
from keephive.storage import is_disabled, set_disabled


def cmd_off(args: list[str]) -> None:
    force = "--force" in args

    if is_disabled():
        console.print("[dim]keephive is already off.[/dim]")
        return

    # Warn if active loops exist
    from keephive.storage import hive_dir

    loop_files = list(hive_dir().glob(".loop-*.json"))
    if loop_files and not force:
        console.print(
            f"[bold yellow]Warning:[/bold yellow] {len(loop_files)} active loop(s) running."
        )
        console.print(
            "  Their stop hooks will go silent, breaking iteration."
        )
        console.print("  Run [bold]hive off --force[/bold] to proceed, or cancel loops first.")
        return

    # Stop daemon if running
    try:
        from keephive.commands.daemon import _is_running, _stop

        if _is_running():
            _stop()
    except Exception:
        pass

    set_disabled(True)
    console.print("[bold red]keephive is OFF[/bold red]")
    console.print("[dim]All hooks, MCP tools, and daemon tasks are disabled.[/dim]")
    console.print("[dim]CLI commands still work. Run [bold]hive on[/bold] to re-enable.[/dim]")


def cmd_on(args: list[str]) -> None:
    if not is_disabled():
        console.print("[dim]keephive is already on.[/dim]")
        return

    set_disabled(False)
    console.print("[bold green]keephive is ON[/bold green]")
    console.print("[dim]All hooks and MCP tools are active again.[/dim]")
    console.print("[dim]Daemon does not auto-restart. Run [bold]hive daemon start[/bold] if needed.[/dim]")
