"""hive swarm: parse a markdown task list and spawn an agent team.

hive swarm <tasks-file> [--dry-run]
hive sw <tasks-file> [--dry-run]
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from keephive.output import console

HELP_TEXT = """Usage: hive swarm <tasks-file> [--dry-run]

  Parse a markdown task list and spawn an agent team.

  Task format:
    - [tag] Description → file/hint
    - [tag] Another task
    - Untagged task (gets worker-N)
    - [ ] GitHub-style checkbox (gets worker-N)
    - [x] Checked item (skipped — already done)

  Options:
    --dry-run   Print the prompt without launching

  Examples:
    hive swarm todo.md
    hive sw tasks.md --dry-run
"""


def cmd_swarm(args: list[str]) -> None:
    if not args or args[0] in ("-h", "--help", "help"):
        print(HELP_TEXT)
        return

    dry_run = "--dry-run" in args
    file_args = [a for a in args if not a.startswith("-")]

    if not file_args:
        console.print("[warn]Error:[/warn] No task file specified.")
        console.print("Usage: hive swarm <tasks-file> [--dry-run]")
        return

    task_file = Path(file_args[0]).expanduser().resolve()
    if not task_file.exists():
        console.print(f"[warn]Error:[/warn] File not found: {task_file}")
        return

    tasks = _parse_tasks(task_file)
    if not tasks:
        console.print(f"[warn]No tasks found in {task_file.name}[/warn]")
        return

    tags = sorted({t["tag"] for t in tasks})
    team_name = f"swarm-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    if not dry_run:
        console.print(
            f"[bold]Swarm:[/bold] {len(tasks)} task(s), {len(tags)} agent(s): {', '.join(tags)}"
        )

    prompt = _build_prompt(tasks, team_name)
    _launch(prompt, dry_run)


def _parse_tasks(file_path: Path) -> list[dict]:
    """Parse markdown task list into structured dicts.

    Supports:
      - [tag] Description → hint    (tagged task)
      - [tag] Description            (tagged task, no hint)
      - Description                  (bare task → worker-N)
      - [ ] Description              (GitHub checkbox, unchecked → worker-N)
      - [x] Description              (GitHub checkbox, checked → skipped)
      - [X] Description              (GitHub checkbox, checked → skipped)

    Checkbox lines are matched before tag lines so a space-only bracket
    content ("[ ]") is never mistaken for an empty tag.
    """
    tasks = []
    worker_n = 1
    checkbox_re = re.compile(r"^\s*-\s+\[([ xX])\]\s+(.+?)(?:\s+→\s+(.+))?$")
    tag_re = re.compile(r"^\s*-\s+\[([^\]]+)\]\s+(.+?)(?:\s+→\s+(.+))?$")
    bare_re = re.compile(r"^\s*-\s+(.+?)(?:\s+→\s+(.+))?$")

    for line in file_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        m = checkbox_re.match(line)
        if m:
            marker = m.group(1)
            if marker in ("x", "X"):
                continue  # already done, skip
            description = m.group(2).strip()
            hint = m.group(3).strip() if m.group(3) else ""
            tag = f"worker-{worker_n}"
            worker_n += 1
            tasks.append({"tag": tag, "description": description, "hint": hint})
            continue

        m = tag_re.match(line)
        if m:
            tag = m.group(1).strip()
            description = m.group(2).strip()
            hint = m.group(3).strip() if m.group(3) else ""
            tasks.append({"tag": tag, "description": description, "hint": hint})
            continue

        m = bare_re.match(line)
        if m:
            description = m.group(1).strip()
            hint = m.group(2).strip() if m.group(2) else ""
            tag = f"worker-{worker_n}"
            worker_n += 1
            tasks.append({"tag": tag, "description": description, "hint": hint})

    return tasks


def _build_prompt(tasks: list[dict], team_name: str) -> str:
    """Build the structured Claude prompt for team creation."""
    tags = sorted({t["tag"] for t in tasks})
    lines = []
    for i, task in enumerate(tasks, 1):
        entry = f"{i}. [{task['tag']}] {task['description']}"
        if task["hint"]:
            entry += f" → {task['hint']}"
        lines.append(entry)

    task_block = "\n".join(lines)
    agent_list = ", ".join(tags)

    return (
        f'Create an agent team called "{team_name}". Use in-process mode.\n\n'
        f"Tasks:\n{task_block}\n\n"
        f"Spawn one teammate per unique tag ({agent_list}).\n"
        "Assign each teammate their tagged tasks.\n"
        "Use Delegate mode (you coordinate only, do not implement).\n"
        "Report back when all tasks are complete."
    )


def _launch(prompt: str, dry_run: bool) -> None:
    """Launch the swarm: print for dry-run, new tmux window or clipboard otherwise."""
    from keephive.storage import hive_dir

    if dry_run:
        print(prompt)
        return

    prompt_file = hive_dir() / "swarm-prompt.md"
    prompt_file.write_text(prompt)

    if os.environ.get("TMUX"):
        # Write a launcher script to avoid inline quoting issues with long prompts
        launcher = hive_dir() / "swarm-launch.sh"
        launcher.write_text(f"#!/bin/bash\nexec claude -p \"$(cat '{prompt_file}')\"\n")
        launcher.chmod(0o755)
        result = subprocess.run(
            ["tmux", "new-window", str(launcher)],
            capture_output=True,
        )
        if result.returncode == 0:
            console.print("[ok]Swarm launched in new tmux window[/ok]")
        else:
            err = result.stderr.decode().strip()
            console.print(f"[warn]tmux launch failed:[/warn] {err}")
            console.print(f"Run manually: claude -p \"$(cat '{prompt_file}')\"")
    else:
        print(prompt)
        try:
            subprocess.run(["pbcopy"], input=prompt.encode(), timeout=5, check=True)
            console.print("[ok]Prompt copied to clipboard — paste into Claude[/ok]")
        except Exception:
            console.print("[dim]Clipboard copy unavailable (pbcopy not found)[/dim]")

    console.print(f"[dim]Prompt saved: {prompt_file}[/dim]")
