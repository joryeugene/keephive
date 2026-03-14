"""hive config: manage advanced configuration (LLM backends, etc.)."""

from __future__ import annotations

from textwrap import indent

from keephive.llm import available_backends, get_backend_state
from keephive.llm.exceptions import BackendNotAvailable
from keephive.output import console
from keephive.settings import get_setting, set_setting


def _format_backend_line(name: str, available: bool, reason: str, selected: bool) -> str:
    status = "[ok]✓[/ok]" if available else "[warn]×[/warn]"
    sel = "[bold] (active)[/bold]" if selected else ""
    info = f" - {reason}" if reason else ""
    return f"  {status} {name}{sel}{info}"


def _show_overview() -> None:
    """Display current backend state and availability."""
    current = get_setting("llm_backend") or "auto"
    state = get_backend_state()
    console.print("[bold]LLM backend configuration[/bold]")
    console.print(f"  Preferred setting: [info]{current}[/info]")
    if state:
        console.print(
            f"  Last selection: [ok]{state.get('selected', '?')}[/ok] "
            f"(source: {state.get('source', '?')}, reason: {state.get('reason', '')})"
        )
    console.print()
    console.print("  [dim]Available backends:[/dim]")
    for backend in available_backends():
        available, info = backend.detect()
        selected = state.get("selected") == backend.name if state else False
        console.print(_format_backend_line(backend.name, available, info, selected))
    console.print()
    console.print("  Commands:")
    console.print("    hive config llm-backend list")
    console.print("    hive config llm-backend set <name>")
    console.print("    hive config llm-backend auto  (reset to auto-detect)")


def _list_backends() -> None:
    """Print detailed backend list with descriptions."""
    state = get_backend_state()
    console.print("[bold]Registered LLM backends[/bold]")
    for backend in available_backends():
        available, info = backend.detect()
        selected = state.get("selected") == backend.name if state else False
        line = _format_backend_line(backend.name, available, info, selected)
        console.print(line)
        if backend.describe:
            console.print(indent(backend.describe() or "", "      "))
    console.print()


def _set_backend(value: str) -> None:
    """Persist backend preference."""
    if value.lower() in {"auto", "default", ""}:
        set_setting("llm_backend", "")
        console.print("  LLM backend preference cleared (auto-detect).")
        return

    names = {backend.name for backend in available_backends()}
    if value not in names:
        console.print(f"[err]Unknown backend:[/err] {value}")
        console.print(f"  Available: {', '.join(sorted(names))}")
        return

    # Validate availability now to surface configuration mistakes early.
    for backend in available_backends():
        if backend.name == value:
            available, reason = backend.detect()
            if not available:
                raise BackendNotAvailable(f"{value} unavailable: {reason}")
            break

    set_setting("llm_backend", value)
    console.print(f"  LLM backend preference set to [bold]{value}[/bold].")


def cmd_config(args: list[str]) -> None:
    """Entrypoint for `hive config`."""
    if not args:
        _show_overview()
        return

    key = args[0]
    if key != "llm-backend":
        known = ", ".join(k for k in ("llm-backend",))
        console.print(f"[err]Unknown config key:[/err] {key}")
        console.print(f"  Supported keys: {known}")
        return

    if len(args) == 1:
        _show_overview()
        return

    sub = args[1]
    if sub in {"list", "--list"}:
        _list_backends()
    elif sub == "set":
        if len(args) < 3:
            console.print("[err]Missing backend name for set[/err]")
            return
        try:
            _set_backend(args[2])
        except BackendNotAvailable as exc:
            console.print(f"[err]{exc}[/err]")
    elif sub in {"auto", "default"}:
        _set_backend("auto")
    else:
        try:
            _set_backend(sub)
        except BackendNotAvailable as exc:
            console.print(f"[err]{exc}[/err]")
