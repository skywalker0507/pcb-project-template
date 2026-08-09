#!/usr/bin/env python3
"""Emit a compact, read-first session receipt without opening the whole PCB."""

from __future__ import annotations

import json
from pathlib import Path

from stage_policy import ROOT, current_stage
from validate_requirements import problems
from artifact_store import config as artifact_config, stage_directory


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    stage = current_stage()
    checklist = load(ROOT / "config/stage-checklist.json")
    acceptance = load(ROOT / "config/pcb-acceptance.json")
    stack = load(ROOT / "config/board-stack.json")
    execution = load(ROOT / "config/execution-policy.json")
    requirements = load(ROOT / "config/requirements.json")
    artifacts = artifact_config()
    tool_policy = load(ROOT / "config/ai-tool-policy.json")
    entrypoint_policy = load(ROOT / "config/tool-entrypoints.json")
    stage_rules = checklist.get("stages", {}).get(stage.get("stage"), {})
    number = stage_rules.get("number")
    label = f"{int(number):02d}-{stage.get('stage')}" if isinstance(number, int) else stage.get("stage")
    result = {
        "schema": 1,
        "stage": stage.get("stage"),
        "stage_number": number,
        "stage_label": label,
        "status": stage.get("status"),
        "objective": stage.get("objective"),
        "open_items": stage.get("open_items", [])[:8],
        "next_actions": stage.get("next_actions", [])[:8],
        "forbidden": stage_rules.get("forbidden", []),
        "required_evidence": stage_rules.get("required_evidence", []),
        "stage_files": stage_rules.get("files", []),
        "board_stack": stack.get("active"),
        "release_requires": (acceptance.get("release") or {}).get("requires", []),
        "requirements_issues": problems(requirements)[:8],
        "execution": {
            "continue_statuses": (execution.get("continuation") or {}).get("statuses", []),
            "candidate_result_is_turn_boundary": (execution.get("continuation") or {}).get("candidate_result_is_turn_boundary"),
            "candidate_rejection_is_blocker": (execution.get("continuation") or {}).get("candidate_rejection_is_blocker"),
            "handoff_is_turn_boundary": (execution.get("continuation") or {}).get("handoff_is_turn_boundary"),
            "final_gate": execution.get("gate_command"),
        },
        "artifacts": {
            "stage_root": str(stage_directory(str(stage.get("stage")), artifacts)),
            "lanes": artifacts.get("lanes", {}),
            "context_budget": artifacts.get("context_budget", {}),
        },
        "allowed_ai_tools": (tool_policy.get("stages") or {}).get(stage.get("stage"), []),
        "tool_entrypoints": {
            name: (entrypoint_policy.get("entrypoints") or {}).get(name)
            for name in (entrypoint_policy.get("stage_capabilities") or {}).get(stage.get("stage"), [])
        },
        "read_only_default": True,
        "instruction": "Do not read a full .kicad_pcb by default; use compact receipts or query_pcb_context.py slices.",
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
