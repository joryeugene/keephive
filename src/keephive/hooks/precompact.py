"""PreCompact hook handler.

Called by Claude Code when a conversation is about to be compacted.
Reads JSON from stdin, extracts transcript excerpts (Layer 1),
then optionally summarizes with claude -p (Layer 2).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from keephive.storage import (
    append_to_daily,
    backup_and_write,
    capture_budget,
    daily_file,
    ensure_daily,
    hive_dir,
    memory_file,
    read_memory,
    safe_read_text,
)


def hook_precompact(args: list[str]) -> None:
    """Main entry point for PreCompact hook."""
    ensure_daily()
    raw = sys.stdin.read()

    # Diagnostic breadcrumb
    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        input_data = {}

    trigger = input_data.get("trigger", "unknown") or "unknown"
    cwd = input_data.get("cwd", "")
    debug_log = hive_dir() / ".hook-debug.log"
    with open(debug_log, "a") as f:
        f.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] hook-precompact called (trigger={trigger})\n"
        )

    # Track usage
    try:
        from keephive.storage import track_event

        track_event("hooks", "precompact", project=cwd, source="hook")
    except Exception:
        pass

    # Find transcript
    transcript = _find_transcript(input_data)

    # Layer 1: Deterministic transcript extraction
    excerpts = ""
    if transcript and Path(transcript).is_file():
        excerpts = _extract_excerpts(transcript, capture_budget())

        if excerpts:
            # Dedup by content hash
            excerpt_hash = hashlib.md5(excerpts.encode()).hexdigest()
            df = daily_file()
            skip = False
            if df.exists() and f"<!-- excerpt-hash:{excerpt_hash} -->" in safe_read_text(df):
                skip = True

            if not skip:
                append_to_daily(f"\n<!-- excerpt-hash:{excerpt_hash} -->")
                append_to_daily("### Session Excerpts (auto-captured)")
                append_to_daily(excerpts)

    # Layer 2: LLM summary (skip if HIVE_SKIP_LLM=1 or no excerpts)
    if excerpts and not os.environ.get("HIVE_SKIP_LLM"):
        _llm_summary(excerpts)


def _find_transcript(input_data: dict) -> str | None:
    """Find the transcript JSONL file from hook input."""
    transcript = input_data.get("transcript_path") or ""

    if transcript and Path(transcript).is_file():
        return transcript

    # Fallback: derive from session_id + cwd
    session_id = input_data.get("session_id") or ""
    cwd = input_data.get("cwd") or ""

    if session_id and cwd:
        encoded_cwd = cwd.replace("/", "-")
        candidate = Path.home() / ".claude" / "projects" / encoded_cwd / f"{session_id}.jsonl"
        if candidate.is_file():
            return str(candidate)

    # Second fallback: search projects dir
    if session_id:
        projects = Path.home() / ".claude" / "projects"
        if projects.exists():
            for found in projects.rglob(f"{session_id}.jsonl"):
                return str(found)

    return None


def _redact_secrets(text: str) -> str:
    """Redact API keys, tokens, and secrets from text using gitleaks-style patterns."""
    # Pattern 1: key=value or key: value style secrets (16+ char values)
    text = re.sub(
        r"(key|token|secret|password|api.?key)[:\s=]*['\"]?([a-zA-Z0-9_\-./+]{16,})",
        lambda m: f"{m.group(1)}: [REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    # Pattern 2: Prefixed secrets like moltbook_sk_..., stripe_key_..., etc.
    text = re.sub(
        r"\b\w+_(sk|key|token|secret)_[a-zA-Z0-9_\-]{10,}",
        "[REDACTED]",
        text,
    )
    # Pattern 3: Bearer tokens
    text = re.sub(
        r"Bearer [a-zA-Z0-9_\-.]+",
        "Bearer [REDACTED]",
        text,
    )
    return text


def _extract_excerpts(transcript_path: str, budget: int) -> str:
    """Layer 1: Deterministic transcript extraction."""
    NOISE_RE = re.compile(
        r"^(Let me|Now let me|I'll |Looking at|Reading |Good,? I|First,? |"
        r"Next,? |Now I |Starting with|Here's what|I can see|I notice|"
        r"I'm going to|Sure,? |OK,? |Okay,? |Alright,? |"
        r"The (test|file|output|error|code|result|log|build|module) |"
        r"This (confirms|shows|means|indicates|suggests|is) |"
        r"Based on |According to |It looks like |It seems |"
        r"That (makes sense|looks|worked|confirms|is)|"
        r"After (reading|looking|checking|reviewing|running))",
        re.IGNORECASE,
    )
    USER_NOISE_RE = re.compile(r"^(This session is being continued|\[Request interrupted)")

    user_msgs: list[str] = []
    asst_msgs: list[str] = []

    with open(transcript_path) as f:
        for line in f:
            try:
                obj = json.loads(line)
                msg_type = obj.get("type")

                if msg_type == "user":
                    text = _extract_user_text(obj)
                    if text and len(text) >= 10:
                        if len(text) > 500 and USER_NOISE_RE.match(text):
                            continue
                        user_msgs.append(text)

                elif msg_type == "assistant":
                    content = obj.get("message", {})
                    if isinstance(content, dict):
                        parts = content.get("content", [])
                        if isinstance(parts, list):
                            for part in parts:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    text = part.get("text", "").strip()
                                    if len(text) > 80 and not NOISE_RE.match(text):
                                        asst_msgs.append(text)
                        elif isinstance(parts, str) and len(parts) > 80:
                            if not NOISE_RE.match(parts):
                                asst_msgs.append(parts)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    if not user_msgs and not asst_msgs:
        return ""

    # Budget: 50% user / 50% assistant
    user_budget = budget // 2
    asst_budget = budget - user_budget

    user_selected = _select_within_budget(user_msgs, user_budget)
    asst_selected = _select_within_budget(asst_msgs, asst_budget)

    lines = []
    for msg in user_selected:
        clean = " ".join(msg.split())[:500]
        lines.append(f"- [USER] {clean}")
    for msg in asst_selected:
        clean = " ".join(msg.split())[:500]
        lines.append(f"- {clean}")

    return _redact_secrets("\n".join(lines))


def _extract_user_text(obj: dict) -> str | None:
    """Extract text from a user message object."""
    content = obj.get("message", {})
    if isinstance(content, dict):
        text_parts = content.get("content", "")
        if isinstance(text_parts, str):
            return text_parts.strip()
        elif isinstance(text_parts, list):
            return " ".join(
                p.get("text", "").strip()
                for p in text_parts
                if isinstance(p, dict) and p.get("type") == "text"
            ).strip()
    elif isinstance(content, str):
        return content.strip()
    return None


def _select_within_budget(messages: list[str], budget: int) -> list[str]:
    """Select messages within character budget, prioritizing recent."""
    if not messages:
        return []
    total = sum(len(m) for m in messages)
    if total <= budget:
        return messages

    selected = []
    chars = 0
    for m in reversed(messages):
        if chars + len(m) > budget:
            remaining = budget - chars
            if remaining > 50:
                selected.append(m[:remaining])
            break
        selected.append(m)
        chars += len(m)
    selected.reverse()
    return selected


def _normalize_for_dedup(text: str) -> str:
    """Normalize text for duplicate comparison.

    Strips URLs, long tokens (API keys etc.), lowercases, collapses whitespace.
    """
    s = text.lower()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[a-zA-Z0-9_\-]{20,}", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_duplicate_insight(daily_path: Path, text: str) -> bool:
    """Check if a similar insight already exists in today's log."""
    if not daily_path.exists():
        return False
    from difflib import SequenceMatcher

    norm_text = _normalize_for_dedup(text)
    existing = safe_read_text(daily_path)
    for line in existing.splitlines():
        m = re.match(r"^- \[\d{2}:\d{2}:\d{2}\]\s*\w+:\s*(.*)", line)
        if m:
            norm_existing = _normalize_for_dedup(m.group(1))
            # Short-circuit: if first 50 normalized chars match, it's a duplicate
            if len(norm_text) >= 50 and len(norm_existing) >= 50:
                if norm_text[:50] == norm_existing[:50]:
                    return True
            elif norm_text == norm_existing:
                return True
            # Fall through to fuzzy match
            if SequenceMatcher(None, norm_text, norm_existing).ratio() > 0.7:
                return True
    return False


# Category descriptions from the LLM prompt that get parroted back verbatim.
_PARROTED_DESCRIPTIONS = frozenset(
    {
        "unfinished work or follow-up items",
        "choices made about architecture, tools, approach",
        "something learned that was previously unknown",
        "something that was wrong and got fixed",
        "non-obvious observations or patterns",
    }
)

_MIN_INSIGHT_LENGTH = 15


def _is_garbage_insight(text: str) -> bool:
    """Reject insights that are too short or parroted category descriptions."""
    stripped = text.strip().rstrip(".")
    if len(stripped) < _MIN_INSIGHT_LENGTH:
        return True
    if stripped.lower() in _PARROTED_DESCRIPTIONS:
        return True
    return False


def _llm_summary(excerpts: str, pipe_fn=None) -> None:
    """Layer 2: LLM summary via claude -p."""
    try:
        from keephive.claude import run_claude_pipe
        from keephive.models import PreCompactResponse

        fn = pipe_fn or run_claude_pipe

        # Load full memory.md for context (never truncated)
        memory_content = read_memory()
        memory_block = ""
        if memory_content:
            memory_block = f"""

You also have the current working memory.

<working_memory>
{memory_content}
</working_memory>

In addition to classifying insights, identify memory updates in the memory_updates array:
- CORRECT (action: "correct"): if an insight contradicts a fact in working memory. Include the "replaces" field with the exact text of the line being replaced.
- ADD (action: "add"): if an insight is a genuinely important, lasting fact worth remembering across sessions.

Memory update rules:
- Only FACT and DECISION insights qualify for "add"
- Only CORRECTION insights qualify for "correct"
- Skip if already in working memory (even if worded differently)
- Max 3 updates per response
- Be highly selective: only facts useful in future sessions"""

        prompt = f"""You are analyzing excerpts from a Claude Code conversation that is about to be compacted.
Extract the IMPORTANT content, not everything, just what's worth remembering.

Lines prefixed [USER] are the human speaking. Prioritize their decisions,
complaints, observations. Skip Claude process narration.

Categories:
- DECISION: choices made about architecture, tools, approach
- FACT: something learned that was previously unknown
- CORRECTION: something that was wrong and got fixed
- TODO: unfinished work or follow-up items
- INSIGHT: non-obvious observations or patterns

Prioritize [USER] lines over assistant narration. User decisions, corrections, and complaints always outrank assistant observations.
If the user corrected Claude, that is always a CORRECTION category.

IMPORTANT: NEVER include API keys, tokens, secrets, passwords, or credentials in your classified insights. Redact or omit them entirely.

Extract 2-7 insights. If nothing is worth remembering, return an empty insights array.

Optionally include rule_suggestions (max 2, empty list if none): short imperative behavioral rules like "Always use uv for Python package management" only if a consistent behavioral preference appears 3+ times in this transcript and is NOT a one-off decision. Leave empty if nothing qualifies.{memory_block}"""

        response = fn(prompt, PreCompactResponse, stdin_text=excerpts)

        if response.insights:
            df = daily_file()
            written = 0
            for insight in response.insights:
                desc = _redact_secrets(insight.description)
                if _is_garbage_insight(desc):
                    continue
                if not _is_duplicate_insight(df, desc):
                    ts = datetime.now().strftime("%H:%M:%S")
                    append_to_daily(f"- [{ts}] {insight.category.value}: {desc}")
                    written += 1

            if written:
                debug_log = hive_dir() / ".hook-debug.log"
                with open(debug_log, "a") as f:
                    f.write(
                        f"[{datetime.now().isoformat(timespec='seconds')}] "
                        f"Layer 2: wrote {written} insight(s) to daily log\n"
                    )

        # Apply memory updates (auto-promote / auto-correct)
        if response.memory_updates:
            _apply_memory_updates(response.memory_updates)

        if response.rule_suggestions:
            _queue_rule_suggestions(response.rule_suggestions)

    except Exception as e:
        debug_log = hive_dir() / ".hook-debug.log"
        with open(debug_log, "a") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] Layer 2 failed: {e}\n")


def _apply_memory_updates(updates: list) -> None:
    """Apply memory updates from LLM response to memory.md.

    - ADD: append to ## Auto-Captured section
    - CORRECT: find-and-replace the old fact in-place

    Logs each change to the daily log. Max 3 per compaction.
    """
    from keephive.models import MemoryAction

    mem_path = memory_file()
    content = safe_read_text(mem_path) if mem_path.exists() else ""
    today_str = datetime.now().strftime("%Y-%m-%d")
    applied = 0

    for update in updates[:3]:  # Hard cap: 3 per compaction
        if update.action == MemoryAction.CORRECT and update.replaces:
            # Find and replace the old fact
            new_content = _correct_in_memory(content, update.replaces, update.text, today_str)
            if new_content != content:
                content = new_content
                ts = datetime.now().strftime("%H:%M:%S")
                append_to_daily(f'- [{ts}] AUTO-CORRECTED: "{update.replaces}" -> "{update.text}"')
                applied += 1

        elif update.action == MemoryAction.ADD:
            # Check for duplicates against memory content
            if not _is_duplicate_in_memory(content, update.text):
                content = _add_to_auto_captured(content, update.text, today_str)
                ts = datetime.now().strftime("%H:%M:%S")
                append_to_daily(f"- [{ts}] AUTO-PROMOTED: {update.text}")
                applied += 1

    if applied > 0:
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        backup_and_write(mem_path, content)
        debug_log = hive_dir() / ".hook-debug.log"
        with open(debug_log, "a") as f:
            f.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"Memory auto-updated: {applied} change(s)\n"
            )


def _pending_rules_path() -> Path:
    return hive_dir() / ".pending-rules.md"


def _queue_rule_suggestions(suggestions: list[str]) -> None:
    """Queue LLM-suggested rules for human review in .pending-rules.md."""
    if not suggestions:
        return
    path = _pending_rules_path()
    existing = path.read_text() if path.exists() else ""
    new_lines = []
    for rule in suggestions[:2]:
        rule = rule.strip()
        if rule and rule.lower() not in existing.lower():
            new_lines.append(f"- {rule}\n")
    if new_lines:
        with path.open("a") as f:
            f.writelines(new_lines)


def _correct_in_memory(content: str, old_text: str, new_text: str, today_str: str) -> str:
    """Find and replace a contradicted fact in memory.md."""
    old_lower = old_text.lower().strip()
    lines = content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip("- ").strip()
        # Remove any existing verified tag for comparison
        stripped_no_tag = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", stripped)
        if old_lower in stripped_no_tag.lower():
            lines[i] = f"- {new_text} [verified:{today_str}]"
            return "\n".join(lines)
    return content  # No match found, don't change


def _add_to_auto_captured(content: str, text: str, today_str: str) -> str:
    """Add a fact to the ## Auto-Captured section of memory.md.

    Creates the section if it doesn't exist.
    """
    new_line = f"- {text} [verified:{today_str}]"
    marker = "## Auto-Captured"

    if marker in content:
        # Insert after the marker header
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == marker:
                # Find the end of existing auto-captured entries
                insert_at = i + 1
                while insert_at < len(lines) and (
                    lines[insert_at].startswith("- ") or lines[insert_at].strip() == ""
                ):
                    insert_at += 1
                # Insert before the next section or at end of auto-captured
                lines.insert(insert_at, new_line)
                return "\n".join(lines)

    # Section doesn't exist: append at end
    stripped = content.rstrip()
    return f"{stripped}\n\n{marker}\n{new_line}\n"


def _is_duplicate_in_memory(content: str, text: str) -> bool:
    """Check if a similar fact already exists in memory.md."""
    from difflib import SequenceMatcher

    text_lower = text.lower()
    for line in content.splitlines():
        if not line.startswith("- "):
            continue
        # Strip verified tag and bullet for comparison
        stripped = re.sub(r"\s*\[verified:\d{4}-\d{2}-\d{2}\]", "", line[2:]).strip()
        if SequenceMatcher(None, text_lower, stripped.lower()).ratio() > 0.7:
            return True
    return False
