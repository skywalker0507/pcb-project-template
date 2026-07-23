#!/usr/bin/env python3
"""Return one bounded next action from CURRENT_STAGE and its checklist."""

from __future__ import annotations

import json
from pathlib import Path

from stage_policy import ROOT, current_stage
from validate_requirements import problems
from design_policy import compile_policy


def main() -> int:
    stage = current_stage()
    checklist = json.loads((ROOT / "config/stage-checklist.json").read_text(encoding="utf-8"))
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
            "electrical": "python scripts/check_pcb_environment.py --tier standard",
            "physical": "python scripts/compile_design_policy.py",
            "routing": "python scripts/krt_flow.py check --input elec/layout/default/default.kicad_pcb",
            "release": "python scripts/run_pcb_pipeline.py --export",
        }
        next_command = commands.get(stage["stage"])
        if stage["stage"] in {"physical", "routing", "release"}:
            policy = compile_policy()
            capability = {"physical": "physical", "routing": "candidate_routing", "release": "release"}[stage["stage"]]
            if not policy["capabilities"][capability]:
                blockers = list(policy["capability_issues"][capability])[:8]
    allowed_commands = {
        "requirements": ["validate_requirements", "create_stage_handoff"],
        "electrical": ["check_pcb_environment", "build_atopile_isolated", "create_stage_handoff"],
        "physical": ["compile_design_policy", "apply_kicad_rules", "run_pcb_pipeline", "record_physical_review", "create_stage_handoff"],
        "routing": ["compile_design_policy", "apply_kicad_rules", "krt_flow check", "krt_flow route", "run_freerouting_headless", "record_physical_review"],
        "release": ["compile_design_policy", "record_physical_review", "run_pcb_pipeline --export", "create_release_snapshot"],
    }.get(stage["stage"], [])
    print(json.dumps({
        "success": not blockers, "stage": stage["stage"], "stage_number": number, "stage_label": label, "status": stage.get("status"),
        "blockers": blockers, "required_evidence": rules.get("required_evidence", []),
        "forbidden": rules.get("forbidden", []), "next_command": next_command,
        "allowed_commands": allowed_commands,
        "stage_files": rules.get("files", []),
    }, separators=(",", ":")))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
