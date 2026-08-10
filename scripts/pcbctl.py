#!/usr/bin/env python3
"""Thin, stable command surface over the existing PCB workflow scripts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# This table only dispatches. Validation, artifacts, guards and release policy
# remain implemented by the mature scripts below.
COMMANDS: dict[str, tuple[str, ...]] = {
    "status": ("scripts/session_boot.py",),
    "build": ("scripts/build_electrical.py",),
    "freeze": ("scripts/create_electrical_freeze.py",),
    "route": ("scripts/krt_flow.py",),
    "verify": ("scripts/run_pcb_pipeline.py",),
    "release": ("scripts/run_pcb_pipeline.py", "--export"),
}

COMMAND_HELP = {
    "status": "current stage and next action",
    "build": "isolated electrical candidate",
    "freeze": "immutable electrical handoff",
    "route": "guarded KRT candidate flow",
    "verify": "PCB audit and independent DRC",
    "release": "guarded manufacturing export",
}


def usage() -> str:
    lines = ["usage: pcbctl.py <command> [arguments forwarded unchanged]", "commands:"]
    lines.extend(f"  {name:<8} {COMMAND_HELP[name]}" for name in COMMANDS)
    return "\n".join(lines)


def command_argv(command: str, arguments: list[str]) -> list[str]:
    target = COMMANDS.get(command)
    if target is None:
        raise ValueError(f"unknown command: {command}")
    return [sys.executable, *target, *arguments]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0
    command = args.pop(0)
    try:
        dispatched = command_argv(command, args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2
    return subprocess.run(dispatched, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
