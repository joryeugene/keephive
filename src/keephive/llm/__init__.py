"""Backend-agnostic LLM routing with capability awareness."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Type, TypeVar

from pydantic import BaseModel

from keephive.llm.exceptions import BackendNotAvailable, BackendTimeoutError, CapabilityError, ClaudePipeError
from keephive.settings import read_settings
from keephive.storage import hive_dir

T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class Backend:
    """Registry metadata for a single LLM backend."""

    name: str
    priority: int
    supports_structured: bool
    supports_tools: bool
    supports_streaming: bool
    call_structured: Callable[
        [str, Type[T], str, Optional[str], Optional[list[str]], Optional[int], int, bool], T
    ]
    detect: Callable[[], tuple[bool, str]]
    describe: Optional[Callable[[], str]] = None


_REGISTRY: Dict[str, Backend] = {}


def register_backend(backend: Backend) -> None:
    """Register a backend implementation."""
    _REGISTRY[backend.name] = backend


def available_backends() -> Iterable[Backend]:
    """Return all registered backends sorted by priority."""
    return sorted(_REGISTRY.values(), key=lambda b: (b.priority, b.name))


def _backend_state_path() -> Path:
    """Location for persisted backend state metadata."""
    return hive_dir().parent / ".backend-state.json"


def _write_state(meta: dict) -> None:
    """Persist backend selection metadata for doctor/status."""
    try:
        path = _backend_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = dict(meta)
        meta["timestamp"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - best effort logging
        import sys

        print(f"[keephive] Failed to write backend state: {exc}", file=sys.stderr)


def get_backend_state() -> dict:
    """Load last persisted backend state (best-effort)."""
    path = _backend_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _get_backend(name: str | None) -> Optional[Backend]:
    if not name:
        return None
    return _REGISTRY.get(name)


def _settings_backend() -> str | None:
    settings = read_settings()
    backend = settings.get("llm_backend") if isinstance(settings, dict) else None
    if isinstance(backend, str) and backend.strip():
        return backend.strip()
    return None


def _auto_select(require_tools: bool, attempted: set[str]) -> tuple[Backend, dict]:
    """Pick the highest-priority backend that matches capabilities."""
    reason = "auto"
    for backend in available_backends():
        if backend.name in attempted:
            continue
        if require_tools and not backend.supports_tools:
            continue
        available, info = backend.detect()
        if available:
            meta = {
                "selected": backend.name,
                "source": "auto",
                "reason": info or "available",
            }
            return backend, meta
        attempted.add(backend.name)
        reason = f"{backend.name}: {info or 'unavailable'}"

    # Fall back to a noop backend (must exist in registry)
    fallback = _REGISTRY.get("none")
    if not fallback:
        raise BackendNotAvailable("No LLM backend registered.")
    meta = {
        "selected": fallback.name,
        "source": "auto",
        "reason": reason,
    }
    return fallback, meta


def _resolve_backend(
    override: str | None = None,
    *,
    require_tools: bool = False,
    attempted: set[str] | None = None,
) -> tuple[Backend, dict]:
    """Resolve the backend to use along with selection metadata."""
    if attempted is None:
        attempted = set()

    def _validate(candidate: Backend, source: str) -> tuple[Backend, dict] | None:
        available, info = candidate.detect()
        if not available:
            attempted.add(candidate.name)
            if source == "cli":
                raise BackendNotAvailable(
                    f"{candidate.name} backend unavailable ({info or 'unknown reason'})"
                )
            return None
        if require_tools and not candidate.supports_tools:
            attempted.add(candidate.name)
            if source == "cli":
                raise CapabilityError(f"{candidate.name} backend does not support tool usage.")
            return None
        meta = {
            "selected": candidate.name,
            "source": source,
            "reason": info or "available",
        }
        return candidate, meta

    # 1. CLI override (explicit call)
    if override:
        candidate = _get_backend(override)
        if not candidate:
            raise BackendNotAvailable(f"Unknown LLM backend: {override!r}")
        validated = _validate(candidate, "cli")
        if validated:
            return validated

    # 2. Config file
    config_choice = _settings_backend()
    if config_choice:
        candidate = _get_backend(config_choice)
        if candidate:
            validated = _validate(candidate, "config")
            if validated:
                return validated

    # 3. Environment variable
    env_choice = os.environ.get("HIVE_LLM_BACKEND", "").strip()
    if env_choice:
        candidate = _get_backend(env_choice)
        if candidate:
            validated = _validate(candidate, "env")
            if validated:
                return validated

    # 4. Automatic detection
    return _auto_select(require_tools=require_tools, attempted=attempted)


def call_structured(
    prompt: str,
    response_model: Type[T],
    *,
    model: str = "haiku",
    stdin_text: str | None = None,
    tools: list[str] | None = None,
    max_turns: int | None = None,
    timeout: int = 120,
    verbose: bool = False,
    backend_override: str | None = None,
) -> T:
    """Execute a structured LLM call via the selected backend."""
    attempted: set[str] = set()

    while True:
        backend, meta = _resolve_backend(
            override=backend_override,
            require_tools=bool(tools),
            attempted=attempted,
        )

        meta.update(
            {
                "supports_structured": backend.supports_structured,
                "supports_tools": backend.supports_tools,
                "supports_streaming": backend.supports_streaming,
            }
        )

        if tools and not backend.supports_tools:
            # This should have been caught by _resolve_backend, but double-check.
            raise CapabilityError(
                f"{backend.name} backend does not support tool usage. "
                "Choose a backend with tool support."
            )

        try:
            result = backend.call_structured(
                prompt,
                response_model,
                model,
                stdin_text,
                tools,
                max_turns,
                timeout,
                verbose,
            )
            meta["status"] = "ok"
            _write_state(meta)
            return result
        except ClaudePipeError as exc:
            meta["status"] = "error"
            meta["error"] = str(exc)
            _write_state(meta)

            # Timeouts are non-retriable: a different backend would silently
            # change model behavior for the same prompt.
            if isinstance(exc, BackendTimeoutError):
                raise

            # If we are in auto-select mode (no explicit override or setting),
            # try the next best backend.
            is_auto = meta.get("source") == "auto"
            if is_auto and backend.name != "none":
                attempted.add(backend.name)
                if verbose:
                    import sys

                    print(
                        f"[keephive] Backend {backend.name} failed: {exc}. Retrying next...",
                        file=sys.stderr,
                    )
                continue

            raise


# -- Provider registrations -------------------------------------------------

from . import anthropic_api, anthropic_cli, gemini_api, none_backend, openai_api  # noqa: E402

register_backend(anthropic_cli.backend)
register_backend(anthropic_api.backend)
register_backend(gemini_api.backend)
register_backend(openai_api.backend)
register_backend(none_backend.backend)
