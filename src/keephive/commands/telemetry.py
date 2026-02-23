"""hive telemetry: internal platform telemetry management."""

from __future__ import annotations

import argparse
import json
import sys

from keephive.telemetry import append_event


def cmd_telemetry(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="hive telemetry")
    subparsers = parser.add_subparsers(dest="subcommand")

    append_parser = subparsers.add_parser("append")
    append_parser.add_argument("--platform", required=True)
    append_parser.add_argument("--event", required=True)
    append_parser.add_argument("--source", default="hook")
    append_parser.add_argument("--stdin-payload", action="store_true")

    parsed = parser.parse_args(args)

    if parsed.subcommand == "append":
        payload = {}
        if parsed.stdin_payload:
            try:
                raw = sys.stdin.read()
                if raw.strip():
                    payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw}

        append_event(parsed.platform, parsed.event, payload, source=parsed.source)
