"""hive improve — review and apply KingBee's self-improvement proposals."""

from __future__ import annotations

import json

from keephive.output import console
from keephive.storage import (
    append_improvement_history,
    daemon_config_file,
    guides_dir,
    hive_dir,
    is_auto_improve_trusted,
    read_daemon_config,
    read_pending_improvements,
    set_auto_improve_trusted,
    write_pending_improvements,
)

_STALE_DAYS = 30


def cmd_improve(args: list[str]) -> None:
    """hive improve [review|list|clear-stale|trust on|trust off]"""
    sub = args[0] if args else "review"
    if sub == "review":
        _improve_review()
    elif sub == "list":
        _improve_list()
    elif sub == "clear-stale":
        _improve_clear_stale()
    elif sub == "trust":
        flag = args[1] if len(args) > 1 else ""
        if flag == "on":
            set_auto_improve_trusted(True)
            console.print(
                "  [ok]✓[/ok] Auto-apply enabled for trusted improvements (skill + rule types)."
            )
            console.print("  KingBee will apply low-risk proposals without manual review.")
        elif flag == "off":
            set_auto_improve_trusted(False)
            console.print("  [dim]Auto-apply disabled. All proposals go to manual review.[/dim]")
        else:
            state = "on" if is_auto_improve_trusted() else "off"
            console.print(f"  Auto-apply trusted: [bold]{state}[/bold]")
            console.print(
                "  Toggle with [dim]hive improve trust on[/dim] / [dim]hive improve trust off[/dim]"
            )
    else:
        console.print(f"[err]Unknown subcommand: {sub}[/err]")
        console.print("Usage: hive improve [review|list|clear-stale|trust on|trust off]")


def _age_str(proposed_at: str | None) -> str:
    """Return human-readable age string for a queue item."""
    if not proposed_at:
        return ""
    try:
        from datetime import datetime

        delta = datetime.now() - datetime.fromisoformat(proposed_at)
        days = delta.days
        if days == 0:
            return "today"
        if days == 1:
            return "1 day ago"
        return f"{days} days ago"
    except ValueError:
        return ""


def _is_stale(item: dict) -> bool:
    proposed_at = item.get("proposed_at")
    if not proposed_at:
        return False
    try:
        from datetime import datetime

        return (datetime.now() - datetime.fromisoformat(proposed_at)).days >= _STALE_DAYS
    except ValueError:
        return False


def _improve_list() -> None:
    items = read_pending_improvements()
    if not items:
        console.print("[dim]No pending improvements. KingBee is still learning.[/dim]")
        return
    console.print(f"\n  🐝 [bold]Pending Improvements[/bold] ({len(items)})\n")
    for i, item in enumerate(items, 1):
        t = item.get("type", "?")
        name = item.get("name", item.get("rule", "")[:50])
        rationale = item.get("rationale", "")[:80]
        age = _age_str(item.get("proposed_at"))
        stale_tag = " [dim][stale][/dim]" if _is_stale(item) else ""
        auto_tag = " [dim][auto][/dim]" if item.get("trusted") else ""
        console.print(
            f"  [{i}] [bold]{t.upper()}[/bold]: {name}  [dim]{age}[/dim]{stale_tag}{auto_tag}"
        )
        console.print(f"      {rationale}")
    console.print()


def _auto_apply_trusted(items: list[dict]) -> tuple[list[dict], int]:
    """Auto-apply trusted skill/rule items if auto-trust is enabled.

    Returns (remaining_items, applied_count). Trusted items that succeed are
    removed from the list; failures stay for manual review.
    """
    if not is_auto_improve_trusted():
        return items, 0

    _SAFE_TYPES = {"skill", "rule"}
    remaining = []
    applied = 0
    for item in items:
        if item.get("trusted") and item.get("type") in _SAFE_TYPES:
            try:
                _apply_improvement(item)
                _log_auto_applied(item)
                applied += 1
            except Exception:
                remaining.append(item)  # failure → keep for manual review
        else:
            remaining.append(item)
    return remaining, applied


def _log_auto_applied(item: dict) -> None:
    """Append [AUTO-APPLIED] entry to daemon.log for audit trail."""
    from keephive.clock import get_now

    item_type = item.get("type", "?")
    name = item.get("name", item.get("rule", "")[:50])
    ts = get_now().strftime("%H:%M")
    try:
        from keephive.storage import hive_dir

        daemon_log = hive_dir() / "daemon.log"
        with daemon_log.open("a") as f:
            f.write(f"[{ts}] [AUTO-APPLIED {item_type}: {name}]\n")
    except Exception:
        pass


def _run_auto_apply() -> int:
    """Read queue, auto-apply trusted items, write back remainder. Returns applied count."""
    items = read_pending_improvements()
    remaining, applied = _auto_apply_trusted(items)
    if applied:
        write_pending_improvements(remaining)
    return applied


def _improve_clear_stale() -> None:
    """Remove all items older than STALE_DAYS from the queue."""
    items = read_pending_improvements()
    fresh = [i for i in items if not _is_stale(i)]
    removed = len(items) - len(fresh)
    if removed == 0:
        console.print("[dim]No stale improvements to remove.[/dim]")
        return
    write_pending_improvements(fresh)
    console.print(f"  [ok]✓[/ok] Removed {removed} stale item(s). {len(fresh)} remaining.")


def _improve_review() -> None:
    items = read_pending_improvements()
    # Auto-apply trusted items (skill/rule) before the interactive loop
    items, auto_count = _auto_apply_trusted(items)
    if auto_count:
        write_pending_improvements(items)
        console.print(f"  [dim]Auto-applied {auto_count} trusted improvement(s).[/dim]")
    if not items:
        console.print("[dim]No pending improvements. KingBee is still learning.[/dim]")
        return

    stale_count = sum(1 for i in items if _is_stale(i))
    stale_note = (
        f"  · [dim]{stale_count} stale (>30d) — 'hive improve clear-stale' to bulk-remove[/dim]"
        if stale_count
        else ""
    )
    console.print(f"\n  🐝 [bold]KingBee Improvements[/bold] — {len(items)} pending{stale_note}\n")

    remaining = []
    dismissed = []
    for idx, item in enumerate(items, 1):
        item_type = item.get("type", "?")
        name = item.get("name", "")
        rationale = item.get("rationale", "")
        age = _age_str(item.get("proposed_at"))
        stale_tag = " [dim][stale][/dim]" if _is_stale(item) else ""

        console.print(
            f"  [{idx}/{len(items)}] [bold]{item_type.upper()}[/bold]"
            + (f": {name}" if name else "")
            + f"  [dim]{age}[/dim]{stale_tag}"
        )
        console.print(f"  Rationale: {rationale}")

        if item_type == "skill":
            content_preview = item.get("content", "")[:120].replace("\n", " ")
            console.print(f"  Content: {content_preview}...")
        elif item_type == "task":
            console.print(f"  Config: {json.dumps(item.get('config', {}))}")
        elif item_type == "rule":
            console.print(f"  Rule: {item.get('rule', '')}")
        elif item_type == "edit":
            action = item.get("action", "edit")
            target = item.get("name", "")
            target_type = item.get("target_type", "")
            merge_with = item.get("merge_with")
            if action == "prune":
                console.print(f"  ⚠️  PRUNE {target_type}: [bold]{target}[/bold] — will be deleted")
            elif action == "merge":
                console.print(
                    f"  MERGE: [bold]{target}[/bold] + [bold]{merge_with}[/bold]"
                    f" → unified {target_type}"
                )
                changes_preview = item.get("changes", "")[:100].replace("\n", " ")
                console.print(f"  Result: {changes_preview}...")
            else:
                changes_preview = item.get("changes", "")[:120].replace("\n", " ")
                console.print(f"  Edit {target_type} [bold]{target}[/bold]: {changes_preview}...")

        from keephive.output import prompt_review_item

        action, edited = prompt_review_item("Accept improvement?", "Edit")

        if action == "accept":
            _apply_improvement(item, edited)
        elif action == "defer":
            remaining.append(item)
        else:
            # skip / dismiss: record so KingBee doesn't re-propose this
            dismissed.append(item)

    write_pending_improvements(remaining)
    if dismissed:
        from keephive.storage import append_dismissed_improvements

        append_dismissed_improvements(dismissed)
    if not remaining:
        console.print("\n  ✓ Queue cleared.\n")
    else:
        console.print(f"\n  {len(remaining)} improvement(s) deferred.\n")


def _apply_skill_edit(skill_path, target: str, changes: str) -> bool:
    """Apply a proposed edit to a skill guide via LLM using full guide context.

    Returns True on success, False on failure (caller opens $EDITOR as fallback).
    """
    from keephive.claude import ClaudePipeError, run_claude_pipe
    from keephive.models import GuideApplyResponse

    existing = skill_path.read_text()
    prompt = f"""You are updating a knowledge guide.

EXISTING GUIDE ({target}):
{existing}

PROPOSED CHANGE:
{changes}

Apply the proposed change to produce the complete updated guide.
Preserve all existing content that is still accurate. Write clean Markdown only.
No preamble, no commentary — output the guide directly."""

    try:
        result = run_claude_pipe(prompt, GuideApplyResponse, model="haiku")
        if result and result.content.strip():
            skill_path.write_text(result.content)
            return True
        return False
    except ClaudePipeError:
        return False


def _record_applied(item: dict) -> None:
    """Record an applied improvement for effectiveness tracking."""
    from keephive.clock import get_now

    entry = {
        "type": item.get("type", "?"),
        "name": (item.get("name") or item.get("rule", ""))[:80],
        "applied_at": get_now().isoformat(),
        "rationale": (item.get("rationale") or "")[:120],
    }
    try:
        append_improvement_history(entry)
    except Exception:
        pass  # never crash the accept flow


def _apply_improvement(item: dict, edited_text: str | None = None) -> None:
    item_type = item.get("type")

    if item_type == "skill":
        name = item.get("name", "").strip()
        content = edited_text or item.get("content", "")
        if not name or not content:
            console.print("[warn]Skipping malformed skill proposal[/warn]")
            return
        gd = guides_dir()
        gd.mkdir(parents=True, exist_ok=True)
        skill_path = gd / f"{name}.md"
        skill_path.write_text(content)
        console.print(f"  [ok]✓[/ok] Installed → {skill_path}")
        from keephive.commands.skill import _skill_sync

        _skill_sync()

    elif item_type == "task":
        name = item.get("name", "").strip()
        config = item.get("config", {})
        if not name:
            console.print("[warn]Skipping malformed task proposal[/warn]")
            return
        daemon_cfg = read_daemon_config()
        daemon_cfg.setdefault("tasks", {})[name] = config
        cfg_path = daemon_config_file()
        cfg_path.write_text(json.dumps(daemon_cfg, indent=2))
        status = "enabled" if config.get("enabled") else "disabled"
        console.print(f"  [ok]✓[/ok] Added task '{name}' to daemon.json ({status})")
        if not config.get("enabled"):
            console.print("  Enable with [dim]hive daemon edit[/dim]")

    elif item_type == "rule":
        rule_text = edited_text or item.get("rule", "")
        if not rule_text:
            return
        # Queue to .pending-rules.md for review via hive rule review
        pending_rules = hive_dir() / ".pending-rules.md"
        with pending_rules.open("a") as f:
            f.write(f"- {rule_text} [auto:proposed-by-kingbee]\n")
        console.print("  [ok]✓[/ok] Rule queued → run [dim]hive rule review[/dim] to apply")

    elif item_type == "edit":
        action = item.get("action", "edit")
        target_type = item.get("target_type", "")
        target = item.get("name", "").strip()
        changes = edited_text or item.get("changes", "")

        if not target:
            console.print("[warn]Skipping malformed edit proposal[/warn]")
            return

        if action == "prune":
            if target_type == "skill":
                skill_path = guides_dir() / f"{target}.md"
                if skill_path.exists():
                    skill_path.unlink()
                    console.print(f"  [ok]✓[/ok] Pruned skill: {target}")
                else:
                    console.print(f"  [dim]Skill not found: {target}[/dim]")
            elif target_type == "task":
                daemon_cfg = read_daemon_config()
                daemon_cfg.get("tasks", {}).pop(target, None)
                daemon_config_file().write_text(json.dumps(daemon_cfg, indent=2))
                console.print(f"  [ok]✓[/ok] Removed task: {target}")
            elif target_type == "rule":
                pending_rules = hive_dir() / ".pending-rules.md"
                with pending_rules.open("a") as f:
                    f.write(f"- REMOVE: {target} [auto:kingbee-prune]\n")
                console.print("  [ok]✓[/ok] Rule removal queued → run [dim]hive rule review[/dim]")

        elif action in ("edit", "merge"):
            if target_type == "skill":
                gd = guides_dir()
                gd.mkdir(parents=True, exist_ok=True)
                skill_path = gd / f"{target}.md"

                if action == "edit" and skill_path.exists():
                    # Apply-time LLM: merge proposed changes into full guide
                    success = _apply_skill_edit(skill_path, target, changes)
                    if not success:
                        import os
                        import subprocess

                        console.print(
                            f"  [warn]LLM apply failed for {target} — opening $EDITOR as fallback[/warn]"
                        )
                        editor = os.environ.get("EDITOR", "nano")
                        subprocess.run([editor, str(skill_path)])
                else:
                    # merge or new file: full replacement is correct
                    skill_path.write_text(changes)

                merge_note = (
                    f" (merged with {item.get('merge_with')})" if item.get("merge_with") else ""
                )
                console.print(f"  [ok]✓[/ok] Updated skill: {target}{merge_note}")
                if item.get("merge_with"):
                    merged_path = gd / f"{item['merge_with']}.md"
                    if merged_path.exists():
                        merged_path.unlink()
                        console.print(f"  [ok]✓[/ok] Removed merged-in skill: {item['merge_with']}")
                from keephive.commands.skill import _skill_sync

                _skill_sync()
            elif target_type == "task":
                daemon_cfg = read_daemon_config()
                try:
                    new_config = json.loads(changes) if changes else {}
                    daemon_cfg.setdefault("tasks", {})[target] = new_config
                    daemon_config_file().write_text(json.dumps(daemon_cfg, indent=2))
                    console.print(f"  [ok]✓[/ok] Updated task config: {target}")
                except (json.JSONDecodeError, ValueError):
                    console.print(
                        f"  [warn]Could not parse task config changes for {target}[/warn]"
                    )

    # Record in improvement history for effectiveness tracking
    _record_applied(item)
