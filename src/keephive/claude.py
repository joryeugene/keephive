"""LLM integration with Pydantic validation.

Two-tier routing:
  Tier 1 (default): claude -p subprocess. Uses Claude Code subscription. Works from terminal.
  Tier 2 (opt-in):  Anthropic API direct. Set ANTHROPIC_API_KEY. Works everywhere.

Inside Claude Code without an API key, LLM calls fail fast with guidance
instead of hanging for 120s on a blocked subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TypeVar, Type

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Model name mapping for the Anthropic API
_API_MODELS: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-5-20250514",
}


class ClaudePipeError(Exception):
    """Raised when an LLM call fails or returns unparseable output."""
    pass


def build_claude_env() -> dict[str, str]:
    """Build environment dict for claude -p subprocess.

    Strips the CLAUDECODE variable which blocks nested sessions.
    """
    blocked = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}
    return {k: v for k, v in os.environ.items() if k not in blocked}


def build_claude_command(
    prompt: str,
    schema_json: str,
    model: str = "haiku",
    tools: list[str] | None = None,
    max_turns: int | None = None,
) -> list[str]:
    """Build the claude -p command list.

    Args:
        prompt: The prompt text.
        schema_json: JSON string of the response schema.
        model: Claude model to use.
        tools: Optional list of built-in tools to enable.
        max_turns: Optional limit on investigation depth.

    Returns:
        Complete command list ready for subprocess.run.
    """
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", schema_json,
        "--model", model,
        "--no-session-persistence",
    ]

    if tools:
        cmd.extend(["--tools", ",".join(tools)])
        cmd.extend(["--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config"])
    if max_turns:
        cmd.extend(["--max-turns", str(max_turns)])

    cmd.append(prompt)
    return cmd


def parse_claude_response(raw_stdout: str, response_model: Type[T]) -> T:
    """Parse raw claude -p stdout into a validated Pydantic model.

    Handles: JSON extraction, array-vs-object format,
    nested result.content, Pydantic validation.

    Args:
        raw_stdout: The raw stdout string from claude -p.
        response_model: Pydantic model class to validate against.

    Returns:
        Validated Pydantic model instance.

    Raises:
        ClaudePipeError: If parsing or validation fails.
    """
    if not raw_stdout.strip():
        msg = "claude -p returned empty output"
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)

    try:
        raw = json.loads(raw_stdout)
    except json.JSONDecodeError as e:
        raise ClaudePipeError(
            f"claude -p returned invalid JSON: {e}\n"
            f"First 500 chars: {raw_stdout[:500]}"
        )

    # Handle array format: claude -p --output-format json returns a JSON array
    # containing system init messages, tool listings, AND the result. We must
    # find the element with "type": "result" specifically.
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

    # Extract structured_output if present (constrained decoding format)
    if isinstance(raw, dict):
        structured = raw.get("structured_output")
        if structured is not None:
            raw = structured
        elif raw.get("type") == "result":
            raw = _extract_from_result_text(raw, raw_stdout, response_model)

    # Validate with Pydantic
    try:
        return response_model.model_validate(raw)
    except ValidationError as e:
        raise ClaudePipeError(
            f"Response validation failed: {e}\n"
            f"Raw data: {json.dumps(raw)[:500]}"
        )


def run_claude_pipe(
    prompt: str,
    response_model: Type[T],
    model: str = "haiku",
    stdin_text: str | None = None,
    tools: list[str] | None = None,
    max_turns: int | None = None,
    timeout: int = 120,
    verbose: bool = False,
) -> T:
    """Run an LLM call with structured output, validated by Pydantic.

    Routing:
      1. ANTHROPIC_API_KEY set -> direct API call (works everywhere)
      2. Inside Claude Code without API key -> fail fast with guidance
      3. Otherwise -> claude -p subprocess (terminal only)

    When tools are requested (verify, reflect analyze), the API path
    cannot provide Claude Code built-in tools. Those calls require
    the subprocess path (from terminal) or fail fast inside Claude Code.

    Args:
        prompt: The prompt text to send.
        response_model: Pydantic model class to validate the response.
        model: Claude model shorthand (default: haiku).
        stdin_text: Optional text to include in the prompt context.
        tools: Optional list of Claude Code built-in tools (subprocess only).
        max_turns: Optional limit on investigation depth (subprocess only).
        timeout: Timeout in seconds (default 120).
        verbose: If True, print debug info.

    Returns:
        Validated Pydantic model instance.

    Raises:
        ClaudePipeError: If the LLM call fails or returns invalid data.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    inside_cc = bool(os.environ.get("CLAUDECODE"))

    # Tier 2: Direct API (works everywhere, including inside Claude Code)
    if api_key and not tools:
        return _run_via_api(
            prompt, response_model, model, stdin_text, timeout, verbose,
        )

    # Tools requested with API key: still need subprocess for tool access.
    # Inside CC that means we can't run it.
    if inside_cc and not api_key:
        raise ClaudePipeError(
            "LLM features need claude -p, which can't run inside Claude Code. "
            "Set ANTHROPIC_API_KEY for LLM features here, or run from a terminal."
        )

    if inside_cc and api_key and tools:
        raise ClaudePipeError(
            f"This command needs Claude Code tools ({', '.join(tools)}) "
            "which require a terminal. Run from a terminal: hive v"
        )

    # Tier 1: subprocess (terminal, uses Claude Code subscription)
    return _run_via_subprocess(
        prompt, response_model, model, stdin_text, tools, max_turns,
        timeout, verbose,
    )


def _run_via_api(
    prompt: str,
    response_model: Type[T],
    model: str,
    stdin_text: str | None,
    timeout: int,
    verbose: bool,
) -> T:
    """Direct Anthropic API call with structured output via Pydantic.

    Uses client.messages.create() with response parsing.
    """
    try:
        import anthropic
    except ImportError:
        raise ClaudePipeError(
            "anthropic package not installed. "
            "Run: keephive setup"
        )

    api_model = _API_MODELS.get(model, model)

    # Build the message content
    content = prompt
    if stdin_text:
        content = f"{prompt}\n\n---\n\n{stdin_text}"

    if verbose:
        print(f"[verbose] API model: {api_model}", file=sys.stderr)
        print(f"[verbose] prompt length: {len(content)}", file=sys.stderr)

    client = anthropic.Anthropic(timeout=float(timeout))

    # Build the tool schema from the Pydantic model
    schema = response_model.model_json_schema()
    # Remove the $defs key and title if present for cleaner tool input
    tool_name = "structured_output"
    tool_description = f"Return the structured {response_model.__name__} response"

    try:
        response = client.messages.create(
            model=api_model,
            max_tokens=4096,
            tools=[{
                "name": tool_name,
                "description": tool_description,
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APITimeoutError:
        msg = f"Anthropic API timed out after {timeout}s."
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)
    except anthropic.APIError as e:
        msg = f"Anthropic API error: {e}"
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)

    if verbose:
        print(f"[verbose] stop_reason: {response.stop_reason}", file=sys.stderr)
        print(f"[verbose] usage: {response.usage}", file=sys.stderr)

    # Extract the tool use result
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            try:
                return response_model.model_validate(block.input)
            except ValidationError as e:
                raise ClaudePipeError(
                    f"API response validation failed: {e}\n"
                    f"Raw input: {json.dumps(block.input)[:500]}"
                )

    raise ClaudePipeError(
        f"API response contained no tool_use block. "
        f"Stop reason: {response.stop_reason}"
    )


def _run_via_subprocess(
    prompt: str,
    response_model: Type[T],
    model: str,
    stdin_text: str | None,
    tools: list[str] | None,
    max_turns: int | None,
    timeout: int,
    verbose: bool,
) -> T:
    """Run claude -p as subprocess. Original Tier 1 path."""
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
        raise ClaudePipeError(msg)

    if verbose:
        print(f"[verbose] returncode: {result.returncode}", file=sys.stderr)
        print(f"[verbose] stderr: {result.stderr[:300]}", file=sys.stderr)
        print(f"[verbose] stdout (first 500): {result.stdout[:500]}", file=sys.stderr)

    if result.returncode != 0:
        msg = f"claude -p exited with code {result.returncode}: {result.stderr[:500]}"
        print(f"[keephive] {msg}", file=sys.stderr)
        raise ClaudePipeError(msg)

    return parse_claude_response(result.stdout, response_model)


def _extract_from_result_text(
    result_elem: dict,
    full_stdout: str,
    response_model: Type[BaseModel] | None = None,
) -> dict:
    """Extract structured data from assistant text when structured_output is missing.

    This handles cases like error_max_turns where the model produced
    the answer as text in an assistant message but didn't get to emit
    structured_output.

    Uses the response model's field names to search for JSON objects,
    falling back to "verdicts" if no model is provided.
    """
    import re as _re

    field_names = (
        list(response_model.model_fields.keys())
        if response_model
        else ["verdicts"]
    )

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
