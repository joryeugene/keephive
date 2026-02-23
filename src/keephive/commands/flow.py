"""hive flow — guided maintenance flow. Drain all queues in one pass."""

from __future__ import annotations

from keephive.output import console


def cmd_flow(args: list[str]) -> None:
    """hive flow [--skip-verify]

    Runs all maintenance queues in sequence:
      Stage 1: Triage       — show pending queue counts (instant)
      Stage 2: Fact Review  — hive mem review
      Stage 3: Rule Review  — hive rule review
      Stage 4: Improvements — hive improve review
      Stage 5: Verify       — hive v (skippable with --skip-verify)
    """
    skip_verify = "--skip-verify" in args

    _triage()

    # Stage 2: Pending facts
    from keephive.commands.memory import _count_pending_facts, cmd_mem_review

    if _count_pending_facts() > 0:
        console.print("\n[bold]● Stage 2/5: Fact Review[/bold]")
        cmd_mem_review([])
    else:
        console.print("[dim]● Stage 2/5: Fact Review — queue empty[/dim]")

    # Stage 3: Pending rules
    from keephive.commands.memory import _count_pending_rules, cmd_rule

    if _count_pending_rules() > 0:
        console.print("\n[bold]● Stage 3/5: Rule Review[/bold]")
        cmd_rule(["review"])
    else:
        console.print("[dim]● Stage 3/5: Rule Review — queue empty[/dim]")

    # Stage 4: Pending improvements
    from keephive.storage import read_pending_improvements

    improvements = read_pending_improvements()
    if improvements:
        console.print(
            f"\n[bold]● Stage 4/5: Improvement Review ({len(improvements)} pending)[/bold]"
        )
        from keephive.commands.improve import cmd_improve

        cmd_improve(["review"])
    else:
        console.print("[dim]● Stage 4/5: Improvement Review — queue empty[/dim]")

    # Stage 5: Verify (optional)
    if not skip_verify:
        console.print("\n[bold]● Stage 5/5: Verify Stale Facts[/bold]")
        from keephive.commands.verify import cmd_verify

        cmd_verify([])
    else:
        console.print("[dim]● Stage 5/5: Verify — skipped (--skip-verify)[/dim]")

    console.print("\n  ✓ [bold]Flow complete.[/bold]\n")


def _triage() -> None:
    """Stage 1: Show all pending queue counts."""
    from keephive.commands.memory import _count_pending_facts, _count_pending_rules
    from keephive.storage import read_pending_improvements

    pf_count = _count_pending_facts()
    pr_count = _count_pending_rules()
    pi_count = len(read_pending_improvements())

    console.print("\n  🐝 [bold]hive flow[/bold] — maintenance run\n")
    console.print(f"  Pending facts:        {pf_count:>3d}  [dim](hive mem review)[/dim]")
    console.print(f"  Pending rules:        {pr_count:>3d}  [dim](hive rule review)[/dim]")
    console.print(f"  Pending improvements: {pi_count:>3d}  [dim](hive improve review)[/dim]")
    console.print()

    if pf_count == 0 and pr_count == 0 and pi_count == 0:
        console.print("  [dim]All queues empty.[/dim]")
    console.print()
