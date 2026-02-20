"""Analyze saved LLM E2E test outputs for quality.

Reads outputs from tests/e2e_outputs/, judges each with claude -p on 5 dimensions:
- accuracy: Is the output factually correct?
- specificity: Does it reference specific input data?
- actionability: Are suggestions concrete and executable?
- format_compliance: Does it follow the expected output structure?
- no_hallucination: Does it avoid inventing facts not in the input?

Each scored 1-5. Anything < 4 is flagged.

Usage:
    uv run python tests/analyze_e2e.py
    uv run python tests/analyze_e2e.py --verbose
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

E2E_DIR = Path(__file__).parent / "e2e_outputs"

JUDGE_PROMPT = """You are a quality judge for an AI agent's outputs.

Score this output on 5 dimensions (1-5 each):

1. accuracy: Is the output factually correct given the input?
2. specificity: Does it reference specific data from the input (not generic)?
3. actionability: Are any suggestions concrete and executable?
4. format_compliance: Does it follow expected structure (sections, labels, etc)?
5. no_hallucination: Does it avoid inventing facts not present in the input?

INPUT CONTEXT:
{metadata}

ACTUAL OUTPUT:
{output}

Respond with ONLY a JSON object:
{{
  "accuracy": <1-5>,
  "specificity": <1-5>,
  "actionability": <1-5>,
  "format_compliance": <1-5>,
  "no_hallucination": <1-5>,
  "issues": ["list of specific problems, empty if none"]
}}"""


def load_outputs() -> list[dict]:
    """Load all saved E2E outputs."""
    results = []
    if not E2E_DIR.exists():
        return results

    for cmd_dir in sorted(E2E_DIR.iterdir()):
        if not cmd_dir.is_dir():
            continue
        for f in sorted(cmd_dir.glob("*.json")):
            data = json.loads(f.read_text())
            data["_file"] = str(f.relative_to(E2E_DIR))
            results.append(data)
    return results


def judge_output(record: dict, verbose: bool = False) -> dict | None:
    """Judge a single output using claude -p."""
    import subprocess

    from keephive.claude import build_claude_env

    metadata = json.dumps(record.get("metadata", {}), indent=2)
    output_text = record.get("output", "")

    if not output_text.strip():
        return {"error": "empty output", "scores": {}}

    prompt = JUDGE_PROMPT.format(metadata=metadata, output=output_text[:3000])

    cmd = [
        "claude",
        "-p",
        "--output-format",
        "text",
        "--model",
        "haiku",
        "--no-session-persistence",
        prompt,
    ]

    env = build_claude_env()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "scores": {}}

    if result.returncode != 0:
        return {"error": f"exit {result.returncode}", "scores": {}}

    # Parse JSON from output
    raw = result.stdout.strip()
    # Handle claude -p array format
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            for elem in parsed:
                if isinstance(elem, dict) and elem.get("type") == "result":
                    text = ""
                    for block in elem.get("content", [elem]):
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                    raw = text
                    break
        else:
            raw = result.stdout.strip()
    except json.JSONDecodeError:
        pass

    # Extract JSON from text
    import re

    json_match = re.search(r'\{[^{}]*"accuracy"[^{}]*\}', raw, re.DOTALL)
    if not json_match:
        if verbose:
            print(f"  Could not parse judge response: {raw[:200]}")
        return {"error": "parse_failed", "scores": {}}

    try:
        scores = json.loads(json_match.group())
        return {"scores": scores, "error": None}
    except json.JSONDecodeError:
        return {"error": "json_parse_failed", "scores": {}}


def main():
    verbose = "--verbose" in sys.argv

    outputs = load_outputs()
    if not outputs:
        print("No E2E outputs found in tests/e2e_outputs/")
        print("Run: uv run pytest -m llm -v")
        sys.exit(1)

    print(f"Analyzing {len(outputs)} saved output(s)...\n")

    results = []
    critical_issues = []

    for record in outputs:
        label = record.get("_file", "unknown")
        print(f"  Judging: {label} ...", end=" ", flush=True)

        judgment = judge_output(record, verbose)
        if judgment is None:
            print("ERROR: unknown")
            continue
        if judgment.get("error"):
            print(f"ERROR: {judgment['error']}")
            continue

        scores = judgment["scores"]
        dims = ["accuracy", "specificity", "actionability", "format_compliance", "no_hallucination"]
        score_strs = []
        for d in dims:
            val = scores.get(d, 0)
            marker = " !" if val < 4 else ""
            score_strs.append(f"{d}={val}{marker}")
            if val < 4:
                critical_issues.append(f"{label}: {d}={val}")

        issues = scores.get("issues", [])
        print(" | ".join(score_strs))
        if issues and verbose:
            for issue in issues:
                print(f"    -> {issue}")

        results.append({"file": label, "scores": scores})

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Analyzed: {len(results)}/{len(outputs)}")

    if critical_issues:
        print("\nCritical issues (score < 4):")
        for issue in critical_issues:
            print(f"  - {issue}")
        print(f"\n{len(critical_issues)} issue(s) need attention.")
        sys.exit(1)
    else:
        print("All scores >= 4. Quality looks good.")
        sys.exit(0)


if __name__ == "__main__":
    main()
