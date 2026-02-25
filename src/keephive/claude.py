"""Compatibility shim for legacy claude-specific imports.

The historical `keephive.claude` module exported `run_claude_pipe` and a handful of
helper functions. The new backend-agnostic LLM layer lives under
:mod:`keephive.llm`. This module simply re-exports the public surface so existing
imports keep working.
"""

from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from keephive.llm import call_structured
from keephive.llm.anthropic_api import _call_structured as _run_via_api
from keephive.llm.anthropic_cli import (
    _call_structured as _run_via_subprocess,
)
from keephive.llm.anthropic_cli import (
    build_claude_command,
    build_claude_env,
    parse_claude_response,
)
from keephive.llm.exceptions import ClaudePipeError

T = TypeVar("T", bound=BaseModel)


def run_claude_pipe(
    prompt: str,
    response_model: Type[T],
    model: str = "haiku",
    stdin_text: str | None = None,
    tools: list[str] | None = None,
    max_turns: int | None = None,
    timeout: int = 120,
    verbose: bool = False,
    backend_override: str | None = None,
    allowed_dirs: list[str] | None = None,
    restrict_mcp: bool = True,
    dangerously_skip_permissions: bool = False,
) -> T:
    """Proxy to the backend-agnostic LLM router.

    Args mirror the historical implementation so tests and callers continue to
    work unchanged.
    """

    return call_structured(
        prompt,
        response_model,
        model=model,
        stdin_text=stdin_text,
        tools=tools,
        max_turns=max_turns,
        timeout=timeout,
        verbose=verbose,
        backend_override=backend_override,
        allowed_dirs=allowed_dirs,
        restrict_mcp=restrict_mcp,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )


__all__ = [
    "ClaudePipeError",
    "build_claude_command",
    "build_claude_env",
    "parse_claude_response",
    "_run_via_api",
    "_run_via_subprocess",
    "run_claude_pipe",
]
