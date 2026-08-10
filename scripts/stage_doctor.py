#!/usr/bin/env python3
"""Return bounded stage navigation; the next command is not a turn boundary."""

from __future__ import annotations

import json
from pathlib import Path

from stage_policy import ROOT, current_stage
from validate_requirements import problems
from design_policy import compile_policy


def main() -> int:
    stage = current_stage()
    checklist = json.loads((ROOT / "config/stage-checklist.json").read_text(encoding="utf-8"))
    execution = json.loads((ROOT / "config/execution-policy.json").read_text(encoding="utf-8"))
    rules = checklist.get("stages", {}).get(stage["stage"], {})
    number = rules.get("number")
    label = f"{int(number):02d}-{stage['stage']}" if isinstance(number, int) else str(stage["stage"])
    blockers: list[str] = []
    next_command: str | None
    if stage.get("status") == "blocked_external_dependency":
        blockers = list(stage.get("open_items", []))[:8]
        next_command = None
    elif stage["stage"] == "requirements":
        requirements = json.loads((ROOT / "config/requirements.json").read_text(encoding="utf-8"))
        blockers = problems(requirements)[:8]
        next_command = "python scripts/validate_requirements.py" if blockers else "python scripts/validate_requirements.py --require-reviewed"
    else:
        commands = {
            "parts": "python scripts/validate_requirements.py --require-reviewed",
            "electrical": "python scripts/build_electrical.py --backend atopile",
            "electrical_freeze": "python scripts/create_electrical_freeze.py --help",
            "physical": "python scripts/check_easyeda_import_parity.py --help",
            "routing": "python scripts/krt_flow.py check --input elec/layout/default/default.kicad_pcb",
            "release": "python scripts/run_pcb_pipeline.py --export",
            "prototype": "python scripts/artifact_store.py inventory",
        }
        next_command = commands.get(stage["stage"])
        if stage["stage"] in {"physical", "routing", "release"}:
            policy = compile_policy()
            capability = {"physical": "physical", "routing": "candidate_routing", "release": "release"}[stage["stage"]]
            if not policy["capabilities"][capability]:
                blockers = list(policy["capability_issues"][capability])[:8]
    allowed_commands = {
        "requirements": ["validate_requirements", "create_stage_handoff"],
        "parts": ["validate_requirements", "create_stage_handoff"],
        "electrical": ["check_pcb_environment", "build_electrical", "create_stage_handoff"],
        "electrical_freeze": ["build_electrical --check", "validate_electrical_contract", "review_electrical_candidate", "create_electrical_freeze", "create_stage_handoff"],
        "physical": ["check_easyeda_import_parity", "compile_design_policy", "apply_kicad_rules", "run_pcb_pipeline", "record_physical_review", "create_stage_handoff"],
        "routing": ["compile_design_policy", "apply_kicad_rules", "krt_flow check", "krt_flow route", "run_freerouting_headless", "record_physical_review"],
        "release": ["compile_design_policy", "record_physical_review", "run_pcb_pipeline --export", "create_release_snapshot"],
        "prototype": ["record_prototype_measurement", "create_stage_handoff"],
    }.get(stage["stage"], [])
    print(json.dumps({
        "success": not blockers, "stage": stage["stage"], "stage_number": number, "stage_label": label, "status": stage.get("status"),
        "blockers": blockers, "required_evidence": rules.get("required_evidence", []),
        "forbidden": rules.get("forbidden", []), "next_command": next_command,
        "stage_next_actions": list(stage.get("next_actions") or [])[:8],
        "turn_boundary": False,
        "continue_required": stage.get("status") in (execution.get("continuation") or {}).get("statuses", []),
        "final_gate": execution.get("gate_command"),
        "allowed_commands": allowed_commands,
        "stage_files": rules.get("files", []),
    }, separators=(",", ":")))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
