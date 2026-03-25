#!/usr/bin/env python3
"""CLI bridge between any AI agent and the deep-research Python engine.

Usage:
    python agent_cli.py init  "<query>" --work-dir <path>
    python agent_cli.py next  --work-dir <path>
    python agent_cli.py complete <trigger> --work-dir <path> [--data '<json>'] [--data-file <path>]

All commands output a JSON action descriptor to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure the package is importable regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.engine import Engine


def cmd_init(args):
    engine = Engine(args.work_dir)
    action = engine.init(args.query)
    print(json.dumps(action, ensure_ascii=False, indent=2))


def cmd_next(args):
    engine = Engine(args.work_dir)
    action = engine.next_action()
    print(json.dumps(action, ensure_ascii=False, indent=2))


def cmd_complete(args):
    data = None
    if args.data_file:
        with open(args.data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif args.data:
        data = json.loads(args.data)

    engine = Engine(args.work_dir)
    action = engine.complete(args.trigger, data)
    print(json.dumps(action, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Deep Research Python Engine — agent CLI bridge",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- init ---
    p_init = sub.add_parser("init", help="Initialize a new research session")
    p_init.add_argument("query", help="The user's research query")
    p_init.add_argument("--work-dir", required=True, help="Working directory for this session")
    p_init.set_defaults(func=cmd_init)

    # --- next ---
    p_next = sub.add_parser("next", help="Return the current pending action (idempotent)")
    p_next.add_argument("--work-dir", required=True, help="Working directory for this session")
    p_next.set_defaults(func=cmd_next)

    # --- complete ---
    p_complete = sub.add_parser("complete", help="Report completion and trigger state transition")
    p_complete.add_argument("trigger", help="Transition trigger name")
    p_complete.add_argument("--work-dir", required=True, help="Working directory for this session")
    p_complete.add_argument("--data", default=None, help="JSON string with completion data")
    p_complete.add_argument("--data-file", default=None, help="Path to JSON file with completion data (preferred for complex payloads)")
    p_complete.set_defaults(func=cmd_complete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
