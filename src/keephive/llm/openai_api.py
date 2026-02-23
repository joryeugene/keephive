"""OpenAI Responses API backend."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from keephive.llm import Backend
from keephive.llm.exceptions import ClaudePipeError

T = TypeVar("T", bound=BaseModel)

_ENDPOINT = "https://api.openai.com/v1/responses"
_MODEL_MAP = {
    "haiku": "gpt-4.1-mini",
    "sonnet": "gpt-4.1",
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
) -> T:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ClaudePipeError("OPENAI_API_KEY not set.")

    model_id = _MODEL_MAP.get(model, model or "gpt-4.1-mini")
    text = prompt
    if stdin_text:
        text = f"{prompt}\n\n---\n\n{stdin_text}"

    schema = response_model.model_json_schema()
    payload = {
        "model": model_id,
        "input": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": schema,
            },
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise ClaudePipeError(f"OpenAI API error {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise ClaudePipeError(f"OpenAI API request failed: {exc}")

    if verbose:
        print(f"[verbose] openai raw: {raw[:500]}", flush=True)

    try:
        parsed = json.loads(raw)
        output = parsed.get("output", [])
        first = output[0]["content"][0]
        if first["type"] == "output_text":
            structured = json.loads(first["text"])
        elif first["type"] == "json_schema":
            structured = first["json"]
        else:
            raise KeyError("Unsupported content type")
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise ClaudePipeError(f"OpenAI response malformed: {exc}")

    try:
        return response_model.model_validate(structured)
    except ValidationError as exc:
        raise ClaudePipeError(
            f"OpenAI response validation failed: {exc}\n"
            f"Raw data: {json.dumps(structured)[:500]}"
        )


def _detect() -> tuple[bool, str]:
    if os.environ.get("OPENAI_API_KEY"):
        return True, "OPENAI_API_KEY present"
    return False, "OPENAI_API_KEY not set"


backend = Backend(
    name="openai_api",
    priority=40,
    supports_structured=True,
    supports_tools=False,
    supports_streaming=True,
    call_structured=_call_structured,
    detect=_detect,
    describe=lambda: "OpenAI Responses API (requires OPENAI_API_KEY).",
)
