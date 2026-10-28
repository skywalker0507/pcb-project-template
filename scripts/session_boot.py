#!/usr/bin/env python3
"""Emit the single compact navigation receipt needed at session start."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from stage_doctor import receipt as stage_receipt
from stage_policy import ROOT
from project_context import selection


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
            "blocker_count": navigation.get("blocker_count", len(navigation.get("blockers") or [])),
            "open_items": list(navigation.get("open_items", []))[:4],
            "open_item_count": navigation.get("open_item_count", len(navigation.get("open_items") or [])),
            "next_actions": list(navigation.get("stage_next_actions", []))[:3],
            "stage_in_progress": navigation.get("stage_in_progress", False),
        },
        "gates": {
            "required": navigation.get("required_evidence", []),
            "forbidden": navigation.get("forbidden", []),
        },
        "active_board_stack": stack.get("active"),
        "next_action": navigation.get("next_action"),
        "required_inputs": navigation.get("required_inputs", []),
        "next_command": navigation.get("next_command"),
        "command_help": "py -3.14 scripts/pcbctl.py --help",
        "context": {
            "default_read": ["AGENTS.md", "build/WORKSET.json"],
            "read_now": ["build/WORKSET.json"],
            "read_if_needed": read_if_needed,
            "artifact_locations": artifact_locations,
            "never_load_complete": ["*.kicad_pcb", "*.kicad_sch", "*.dsn", "*.ses"],
        },
        "final_gate": navigation.get("final_gate"),
        "continuous_gate_required": navigation.get("continuous_gate_required", False),
        "instruction": "Use the compact workset as the only default context. Load an on-demand file only after its related action or failure requires it.",
    }


def workset(receipt: dict[str, object]) -> dict[str, object]:
    stage = dict(receipt.get("stage") or {})
    work = dict(receipt.get("work") or {})
    release_stage = stage.get("name") == "release"
    blocker_count = work.get("blocker_count", len(work.get("blockers") or []))
    open_item_count = 0 if release_stage else work.get("open_item_count", len(work.get("open_items") or []))
    result = {
        "schema": 1,
        "stage": stage.get("name"),
        "status": stage.get("status"),
        "objective": stage.get("objective"),
        "active_board_stack": receipt.get("active_board_stack"),
        "blockers": list(work.get("blockers") or [])[:4],
        "blocker_count": blocker_count,
        "blockers_truncated": blocker_count > len(list(work.get("blockers") or [])[:4]),
        "open_items": [] if release_stage else list(work.get("open_items") or [])[:4],
        "open_item_count": open_item_count,
        "open_items_truncated": open_item_count > len(list(work.get("open_items") or [])[:4]),
        "next_actions": list(work.get("next_actions") or [])[:3],
        "next_command": receipt.get("next_command"),
        "required_inputs": list(receipt.get("required_inputs") or [])[:3],
        "details_if_needed": "build/07-release/tmp/release-readiness/current.json" if release_stage else None,
        "project_root": str(ROOT.resolve()),
    }
    checks_path = ROOT / 'build/03-electrical/project-checks/current.json'
    if checks_path.is_file():
        try:
            checks = load(checks_path)
            result['last_project_check_batch'] = {
                key: checks.get(key) for key in ('check_count', 'executed', 'reused', 'success')}
            result['last_project_check_batch'].update(freshness='not_checked;use verify project --plan',
                                                       details='build/03-electrical/project-checks/current.json')
        except (OSError, ValueError):
            result['last_project_check_batch'] = {'error': 'summary_unavailable'}
    return result


def stdout_index(receipt: dict[str, object]) -> dict[str, object]:
    stage = dict(receipt.get("stage") or {})
    work = dict(receipt.get("work") or {})
    return {
        "schema": 3,
        "project_root": str(ROOT.resolve()),
        "stage": stage.get("name"),
        "status": stage.get("status"),
        "blocker_count": work.get("blocker_count", len(work.get("blockers") or [])),
        "open_item_count": 0 if stage.get("name") == "release" else work.get("open_item_count", len(work.get("open_items") or [])),
        "next_action": receipt.get("next_action"),
        "next_command": receipt.get("next_command"),
        "read_now": ["build/WORKSET.json"],
        "instruction": "Read build/WORKSET.json once; do not reopen stage policy files unless a related failure requires them.",
    }


def main(argv: list[str] | None = None) -> int:
    try:
        project, args = selection(ROOT, list(sys.argv[1:] if argv is None else argv))
        if args:
            raise ValueError('usage: session_boot.py [--project PATH | --local]')
        if project != ROOT.resolve():
            completed = subprocess.run([sys.executable, str(project / 'scripts/session_boot.py'), '--local'],
                                       cwd=project, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if completed.returncode:
                print(completed.stderr[-2000:], file=sys.stderr)
                return completed.returncode
            index = json.loads(completed.stdout)
            remote = load(project / 'build/WORKSET.json')
            remote.update(project_root=str(project), workspace_root=str(ROOT.resolve()))
            target = ROOT / 'build/WORKSET.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(remote, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            index.update(project_root=str(project), workspace_root=str(ROOT.resolve()))
            print(json.dumps(index, ensure_ascii=False, separators=(',', ':')))
            return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'success': False, 'error': 'project_context_invalid', 'details': str(exc)}))
        return 2
    result = build_receipt()
    target = ROOT / "build/WORKSET.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(workset(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stdout_index(result), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
