"""Anthropic API backend."""

from __future__ import annotations

import json
import os
import sys
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from keephive.llm import Backend
from keephive.llm.exceptions import ClaudePipeError

T = TypeVar("T", bound=BaseModel)

_API_MODELS: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
}


def _call_structured(
    prompt: str,
    response_model: Type[T],
    model: str,
    stdin_text: str | None,
    _tools: list[str] | None,
    _max_turns: int | None,
    timeout: int,
    verbose: bool,
    **_kwargs,
) -> T:
    """Invoke the Anthropic Messages API with structured output."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ClaudePipeError(f"anthropic package not installed. Run: keephive setup\n({exc})")

    api_model = _API_MODELS.get(model, model)
    content = prompt
    if stdin_text:
        content = f"{prompt}\n\n---\n\n{stdin_text}"

    if verbose:
        print(f"[verbose] API model: {api_model}", file=sys.stderr)
        print(f"[verbose] prompt length: {len(content)}", file=sys.stderr)

    client = anthropic.Anthropic(timeout=float(timeout))
    schema = response_model.model_json_schema()
    tool_name = "structured_output"
    tool_description = f"Return the structured {response_model.__name__} response"

    try:
        response = client.messages.create(
            model=api_model,
            max_tokens=4096,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APITimeoutError:
        msg = f"Anthropic API timed out after {timeout}s."
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)
    except anthropic.APIError as exc:
        msg = f"Anthropic API error: {exc}"
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)

    if verbose:
        print(f"[verbose] stop_reason: {response.stop_reason}", file=sys.stderr)
        print(f"[verbose] usage: {response.usage}", file=sys.stderr)

    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            try:
                return response_model.model_validate(block.input)
            except ValidationError as exc:
                raise ClaudePipeError(
                    f"API response validation failed: {exc}\n"
                    f"Raw input: {json.dumps(block.input)[:500]}"
                )

    raise ClaudePipeError(
        f"API response contained no tool_use block. Stop reason: {response.stop_reason}"
    )


def _detect() -> tuple[bool, str]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, "ANTHROPIC_API_KEY present"
    return False, "ANTHROPIC_API_KEY not set"


backend = Backend(
    name="anthropic_api",
    priority=20,
    supports_structured=True,
    supports_tools=True,
    supports_streaming=True,
    call_structured=_call_structured,
    detect=_detect,
    describe=lambda: "Anthropic Messages API (requires ANTHROPIC_API_KEY).",
)
