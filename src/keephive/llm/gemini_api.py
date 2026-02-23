"""Gemini API backend."""

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

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_MODEL_MAP = {
    "haiku": "gemini-3-flash",
    "sonnet": "gemini-3.1-pro",
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
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ClaudePipeError("GEMINI_API_KEY not set.")

    model_id = _MODEL_MAP.get(model, model or "gemini-1.5-flash")
    url = f"{_BASE_URL}/{model_id}:generateContent?key={api_key}"

    text = prompt
    if stdin_text:
        text = f"{prompt}\n\n---\n\n{stdin_text}"

    payload = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"responseMimeType": "application/json"},
        "responseSchema": response_model.model_json_schema(),
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise ClaudePipeError(f"Gemini API error {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise ClaudePipeError(f"Gemini API request failed: {exc}")

    if verbose:
        print(f"[verbose] gemini raw: {raw[:500]}", flush=True)

    try:
        parsed = json.loads(raw)
        candidates = parsed.get("candidates", [])
        first = candidates[0]["content"][0]
        if first.get("text"):
            structured = json.loads(first["text"])
        elif first.get("functionCall", {}).get("args"):
            structured = first["functionCall"]["args"]
        else:
            raise KeyError("No text or functionCall args in response.")
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise ClaudePipeError(f"Gemini response malformed: {exc}")

    try:
        return response_model.model_validate(structured)
    except ValidationError as exc:
        raise ClaudePipeError(
            f"Gemini response validation failed: {exc}\nRaw data: {json.dumps(structured)[:500]}"
        )


def _detect() -> tuple[bool, str]:
    if os.environ.get("GEMINI_API_KEY"):
        return True, "GEMINI_API_KEY present"
    return False, "GEMINI_API_KEY not set"


backend = Backend(
    name="gemini_api",
    priority=30,
    supports_structured=True,
    supports_tools=False,
    supports_streaming=True,
    call_structured=_call_structured,
    detect=_detect,
    describe=lambda: "Google Gemini API (requires GEMINI_API_KEY).",
)
