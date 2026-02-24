"""Fallback backend that records unmet LLM requests."""

from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from keephive.llm import Backend
from keephive.llm.exceptions import ClaudePipeError
from keephive.llm.pending import queue_request

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
    queue_request(
        prompt,
        model=model,
        tools=tools,
        stdin_text=stdin_text,
        max_turns=max_turns,
    )
    raise ClaudePipeError(
        "No LLM backend available. Request queued in ~/.keephive/pending/llm.jsonl. "
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
