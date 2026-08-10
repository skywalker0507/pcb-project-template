#!/usr/bin/env python3
"""Emit the single compact navigation receipt needed at session start."""

from __future__ import annotations

import json
from pathlib import Path

from stage_doctor import receipt as stage_receipt
from stage_policy import ROOT


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def unique_paths(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value not in result:
            result.append(value)
    return result


def build_receipt() -> dict[str, object]:
    navigation = stage_receipt()
    stack = load(ROOT / "config/board-stack.json")
    stage_paths = unique_paths(list(navigation.get("stage_files") or []))
    read_if_needed = unique_paths([
        *[value for value in stage_paths if (ROOT / value).is_file()],
        "config/pcb-acceptance.json",
        "config/board-stack.json",
        "config/tool-entrypoints.json",
    ])
    artifact_locations = [value for value in stage_paths if not (ROOT / value).is_file()]
    return {
        "schema": 2,
        "stage": {
            "name": navigation.get("stage"),
            "number": navigation.get("stage_number"),
            "label": navigation.get("stage_label"),
            "status": navigation.get("status"),
            "objective": navigation.get("objective"),
        },
        "work": {
            "blockers": navigation.get("blockers", []),
            "open_items": navigation.get("open_items", []),
            "next_actions": navigation.get("stage_next_actions", []),
            "continue_required": navigation.get("continue_required", False),
        },
        "gates": {
            "required": navigation.get("required_evidence", []),
            "forbidden": navigation.get("forbidden", []),
        },
        "active_board_stack": stack.get("active"),
        "next_command": navigation.get("next_command"),
        "command_help": "python scripts/pcbctl.py --help",
        "context": {
            "default_read": ["AGENTS.md", "this session receipt"],
            "read_now": [],
            "read_if_needed": read_if_needed,
            "artifact_locations": artifact_locations,
            "never_load_complete": ["*.kicad_pcb", "*.kicad_sch", "*.dsn", "*.ses"],
        },
        "final_gate": navigation.get("final_gate"),
        "instruction": "If next_command is present, run it unless the user named a more specific operation; load on-demand files only when needed.",
    }


def main() -> int:
    print(json.dumps(build_receipt(), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
