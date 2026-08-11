#!/usr/bin/env python3
"""Return bounded stage navigation; the next command is not a turn boundary."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from stage_policy import ROOT, current_stage
from validate_requirements import problems
from design_policy import compile_policy
from electrical_backend import read_config as read_electrical_config, selected_backend as selected_electrical_backend
from physical_backend import read_config as read_physical_config, selected_backend as selected_physical_backend
from create_stage_handoff import tree_hash


def handoff_integrity_issues(stage: dict[str, object]) -> list[str]:
    issues: list[str] = []
    active_electrical, electrical = selected_electrical_backend(read_electrical_config())
    active_physical, _ = selected_physical_backend(read_physical_config())
    if stage.get("electrical_backend") not in (None, active_electrical):
        issues.append("handoff_electrical_backend_does_not_match_active_backend")
    if stage.get("physical_backend") not in (None, active_physical):
        issues.append("handoff_physical_backend_does_not_match_active_backend")
    source_hash = stage.get("source_sha256")
    if isinstance(source_hash, str):
        current_source_hash = tree_hash([
            ROOT / "config/electrical-backend.json",
            ROOT / str(electrical["source_root"]),
        ])
        if source_hash != current_source_hash:
            issues.append("handoff_electrical_source_sha256_is_stale")
    elif stage.get("status") in {"complete", "completed"}:
        issues.append("handoff_electrical_source_sha256_is_missing")
    board_value = stage.get("board")
    board_hash = stage.get("board_sha256")
    if isinstance(board_value, str) and board_value:
        board = Path(board_value)
        board = board.resolve() if board.is_absolute() else (ROOT / board).resolve()
        if not board.is_file():
            issues.append("handoff_board_is_missing")
        elif not isinstance(board_hash, str) or hashlib.sha256(board.read_bytes()).hexdigest().upper() != board_hash:
            issues.append("handoff_board_sha256_is_stale")
    elif stage.get("status") in {"complete", "completed"}:
        issues.append("handoff_completion_board_is_missing")
    if stage.get("status") in {"complete", "completed"} and stage.get("completion_verified") is not True:
        issues.append("handoff_completion_lacks_passing_evidence")
    elif stage.get("status") in {"complete", "completed"}:
        basis = stage.get("completion_basis")
        if basis == "pipeline":
            pipeline = stage.get("pipeline")
            if not isinstance(pipeline, dict) or pipeline.get("success") is not True or pipeline.get("board_sha256") != board_hash:
                issues.append("handoff_completion_pipeline_is_invalid")
            elif stage.get("stage") == "release" and pipeline.get("release_pass") is not True:
                issues.append("handoff_release_pipeline_did_not_pass_all_release_gates")
        elif basis == "receipts":
            receipts = stage.get("completion_receipts")
            if not isinstance(receipts, list) or not receipts:
                issues.append("handoff_completion_receipts_are_missing")
            else:
                for item in receipts:
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                        issues.append("handoff_completion_receipt_record_is_invalid")
                        break
                    path = Path(item["path"])
                    path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest().upper() != item.get("sha256"):
                        issues.append("handoff_completion_receipt_is_missing_or_stale")
                        break
        else:
            issues.append("handoff_completion_basis_is_missing")
    return issues


def receipt() -> dict[str, object]:
    stage = current_stage()
    checklist = json.loads((ROOT / "config/stage-checklist.json").read_text(encoding="utf-8"))
    execution = json.loads((ROOT / "config/execution-policy.json").read_text(encoding="utf-8"))
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    configured_board = str((flow.get("project") or {}).get("board", ""))
    rules = checklist.get("stages", {}).get(stage["stage"], {})
    number = rules.get("number")
    label = f"{int(number):02d}-{stage['stage']}" if isinstance(number, int) else str(stage["stage"])
    blockers: list[str] = []
    handoff_issues = handoff_integrity_issues(stage)
    blockers.extend(handoff_issues)
    next_command: str | None
    next_action: str | None = None
    required_inputs: list[str] = []
    if stage.get("status") == "blocked_external_dependency":
        blockers.extend(list(stage.get("open_items", []))[:8])
        next_command = None
    elif stage.get("status") == "complete" and not stage.get("next_actions") and not handoff_issues:
        next_command = None
    elif stage["stage"] == "requirements":
        requirements = json.loads((ROOT / "config/requirements.json").read_text(encoding="utf-8"))
        blockers.extend(problems(requirements)[:8])
        next_action = "validate_requirements"
        next_command = "python scripts/validate_requirements.py" if blockers else "python scripts/validate_requirements.py --require-reviewed"
    else:
        actions: dict[str, tuple[str, str | None, list[str]]] = {
            "parts": ("validate_selected_parts", "python scripts/validate_part_provenance.py", []),
            "electrical": ("build_electrical_candidate", "python scripts/pcbctl.py build --backend atopile", []),
            "electrical_freeze": (
                "create_electrical_freeze", None,
                ["electrical_build_receipt", "electrical_contract_receipt", "candidate_review_receipt"],
            ),
            "physical": (
                "validate_easyeda_import_parity", None,
                ["frozen_source_manifest", "easyeda_import_manifest", "output_receipt"],
            ),
            "routing": ("check_routing_engine", f"python scripts/pcbctl.py route check --input {configured_board}", []),
            "release": ("run_guarded_release", "python scripts/pcbctl.py release", []),
            "prototype": ("inventory_prototype_evidence", "python scripts/artifact_store.py inventory", []),
        }
        selected_action = actions.get(stage["stage"])
        if selected_action:
            next_action, next_command, required_inputs = selected_action
        else:
            next_command = None
        if stage["stage"] in {"physical", "routing", "release"}:
            policy = compile_policy()
            capability = {"physical": "physical", "routing": "candidate_routing", "release": "release"}[stage["stage"]]
            if not policy["capabilities"][capability]:
                blockers.extend(list(policy["capability_issues"][capability])[:8])
    blockers = blockers[:8]
    allowed_commands = {
        "requirements": ["validate_requirements", "create_stage_handoff"],
        "parts": ["validate_part_provenance", "create_stage_handoff"],
        "electrical": ["check_pcb_environment", "build_electrical", "create_stage_handoff"],
        "electrical_freeze": ["build_electrical --check", "validate_electrical_contract", "review_electrical_candidate", "create_electrical_freeze", "create_stage_handoff"],
        "physical": ["check_easyeda_import_parity", "compile_design_policy", "apply_kicad_rules", "run_pcb_pipeline", "record_physical_review", "create_stage_handoff"],
        "routing": ["compile_design_policy", "apply_kicad_rules", "krt_flow check", "krt_flow route", "run_freerouting_headless", "record_physical_review"],
        "release": ["compile_design_policy", "check_final_electrical_parity", "check_manufacturing_gates", "record_physical_review", "run_pcb_pipeline --export", "create_release_snapshot"],
        "prototype": ["record_prototype_measurement", "create_stage_handoff"],
    }.get(stage["stage"], [])
    return {
        "success": not blockers, "stage": stage["stage"], "stage_number": number, "stage_label": label, "status": stage.get("status"),
        "objective": stage.get("objective"), "open_items": list(stage.get("open_items") or [])[:8],
        "blockers": blockers, "required_evidence": rules.get("required_evidence", []),
        "forbidden": rules.get("forbidden", []), "next_action": next_action,
        "required_inputs": required_inputs, "next_command": next_command,
        "stage_next_actions": list(stage.get("next_actions") or [])[:8],
        "turn_boundary": False,
        "stage_in_progress": stage.get("status") in (execution.get("continuation") or {}).get("statuses", []),
        "continuous_gate_required": False,
        "continuous_gate_requires_explicit_scope": (execution.get("activation") or {}).get("requires_explicit_whole_stage_scope", True),
        "final_gate": execution.get("gate_command"),
        "allowed_commands": allowed_commands,
        "stage_files": rules.get("files", []),
    }


def main() -> int:
    result = receipt()
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
