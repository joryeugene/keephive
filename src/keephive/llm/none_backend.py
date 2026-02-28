"""Fallback backend when no LLM provider is available."""

from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from keephive.llm import Backend
from keephive.llm.exceptions import BackendNotAvailable

T = TypeVar("T", bound=BaseModel)


def _call_structured(
    prompt: str,
    response_model: Type[T],
    model: str,
    stdin_text: str | None,
    tools: list[str] | None,
    max_turns: int | None,
    timeout: int,
    verbose: bool,
    **_kwargs,
) -> T:
    raise BackendNotAvailable(
        "No LLM backend available. "
        "Configure a backend via HIVE_LLM_BACKEND, hive config llm-backend, or install the claude CLI."
    )


def _detect() -> tuple[bool, str]:
    return True, "Fallback backend"


backend = Backend(
    name="none",
    priority=99,
    supports_structured=False,
    supports_tools=False,
    supports_streaming=False,
    call_structured=_call_structured,
    detect=_detect,
    describe=lambda: "Fallback backend that always errors with configuration guidance.",
)
