"""Anthropic CLI backend (claude -p)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from keephive.llm import Backend
from keephive.llm.exceptions import BackendTimeoutError, ClaudePipeError

T = TypeVar("T", bound=BaseModel)
_BLOCKED_ENV_VARS = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}


def build_claude_env() -> dict[str, str]:
    """Build environment dict for claude -p subprocess."""
    return {k: v for k, v in os.environ.items() if k not in _BLOCKED_ENV_VARS}


def build_claude_command(
    prompt: str,
    schema_json: str,
    model: str = "haiku",
    tools: list[str] | None = None,
    max_turns: int | None = None,
) -> list[str]:
    """Build the claude -p command list."""
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
        "--model",
        model,
        "--no-session-persistence",
    ]

    if tools:
        cmd.extend(["--tools", ",".join(tools)])
        cmd.extend(["--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config"])
    if max_turns:
        cmd.extend(["--max-turns", str(max_turns)])

    cmd.append(prompt)
    return cmd


def _extract_from_result_text(
    result_elem: dict,
    full_stdout: str,
    response_model: Type[BaseModel] | None = None,
) -> dict:
    """Extract structured data from assistant text when structured_output is missing."""
    import re as _re

    field_names = list(response_model.model_fields.keys()) if response_model else ["verdicts"]

    try:
        elements = json.loads(full_stdout)
    except (json.JSONDecodeError, TypeError):
        return result_elem

    for elem in reversed(elements):
        if not isinstance(elem, dict) or elem.get("type") != "assistant":
            continue
        for block in elem.get("content", []):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text", "")
            for field in field_names:
                pattern = r'\{[\s\S]*"' + _re.escape(field) + r'"[\s\S]*\}'
                match = _re.search(pattern, text)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        continue

    return result_elem


def parse_claude_response(raw_stdout: str, response_model: Type[T]) -> T:
    """Parse raw claude -p stdout into a validated Pydantic model."""
    if not raw_stdout.strip():
        msg = "claude -p returned empty output"
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)

    try:
        raw = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        raise ClaudePipeError(
            f"claude -p returned invalid JSON: {exc}\nFirst 500 chars: {raw_stdout[:500]}"
        )

    if isinstance(raw, list):
        if not raw:
            raise ClaudePipeError("claude -p returned empty array")
        result_elem = None
        for elem in raw:
            if isinstance(elem, dict) and elem.get("type") == "result":
                result_elem = elem
                break
        if result_elem is None:
            types = [e.get("type", "?") for e in raw if isinstance(e, dict)]
            raise ClaudePipeError(
                f"claude -p returned no result element (found types: {types})\n"
                f"Raw response (first 500 chars): {json.dumps(raw)[:500]}"
            )
        raw = result_elem

    if isinstance(raw, dict):
        structured = raw.get("structured_output")
        if structured is not None:
            raw = structured
        elif raw.get("type") == "result":
            raw = _extract_from_result_text(raw, raw_stdout, response_model)

    if isinstance(raw, dict) and raw.get("type") == "result":
        subtype = raw.get("subtype", "")
        if subtype.startswith("error_"):
            label = subtype.replace("_", " ")
            raise ClaudePipeError(
                f"claude -p ended with {label}. "
                "The model used all allowed turns without producing structured output. "
                "Try: fewer facts per run, or increase --max-turns."
            )

    try:
        return response_model.model_validate(raw)
    except ValidationError as exc:
        raise ClaudePipeError(
            f"Response validation failed: {exc}\nRaw data: {json.dumps(raw)[:500]}"
        )


def _call_structured(
    prompt: str,
    response_model: Type[T],
    model: str,
    stdin_text: str | None,
    tools: list[str] | None,
    max_turns: int | None,
    timeout: int,
    verbose: bool,
) -> T:
    """Execute claude -p subprocess and parse output."""
    schema = json.dumps(response_model.model_json_schema())
    env = build_claude_env()
    cmd = build_claude_command(prompt, schema, model, tools, max_turns)

    if verbose:
        print(f"[verbose] cmd: {' '.join(cmd)}", file=sys.stderr)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            input=stdin_text,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        msg = f"claude -p timed out after {timeout}s. Try increasing timeout or simplifying the prompt."
        print(f"[keephive] {msg}", file=sys.stderr)
        raise BackendTimeoutError(msg)

    if verbose:
        print(f"[verbose] returncode: {result.returncode}", file=sys.stderr)
        print(f"[verbose] stderr: {result.stderr[:300]}", file=sys.stderr)
        print(f"[verbose] stdout (first 500): {result.stdout[:500]}", file=sys.stderr)

    if result.returncode != 0:
        msg = f"claude -p exited with code {result.returncode}: {result.stderr[:500]}"
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)

    return parse_claude_response(result.stdout, response_model)


def _detect() -> tuple[bool, str]:
    """Check if claude CLI is available."""
    if shutil.which("claude"):
        return True, "claude CLI available"
    return False, "claude CLI not found on PATH"


backend = Backend(
    name="anthropic_cli",
    priority=10,
    supports_structured=True,
    supports_tools=True,
    supports_streaming=False,
    call_structured=_call_structured,
    detect=_detect,
    describe=lambda: "Uses local claude CLI (no additional API cost).",
)
