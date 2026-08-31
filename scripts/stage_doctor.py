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
from release_inputs import DEFAULT_READINESS, fresh_release_readiness


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


def physical_navigation(
    physical_candidate_exists: bool, required_inputs: list[str]
) -> tuple[str, str | None, list[str]]:
    if not physical_candidate_exists:
        return (
            "create_kicad_physical_candidate",
            "py -3.14 scripts/pcbctl.py physical-start",
            required_inputs,
        )
    if "bounded_critical_probe_plan" in required_inputs:
        return "continue_physical_critical_probes", None, required_inputs
    if any(value.startswith("bounded_") and value.endswith("_corridor_strategy") for value in required_inputs):
        return "continue_physical_corridor_review", None, required_inputs
    return "continue_kicad_placement", None, required_inputs or ["bounded_placement_plan"]


def resolve_recorded_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def routing_checkpoint_navigation(
    stage: dict[str, object], routing_board: str, board_hash: str | None,
) -> tuple[str, str | None, list[str]]:
    """Use compact receipts to stop routing once connectivity is complete."""
    records = stage.get("candidate_receipts", [])
    if not isinstance(records, list) or not board_hash:
        return "route_next_functional_block", f'py -3.14 scripts/pcbctl.py route check --input "{routing_board}"', []
    audit_path: Path | None = None
    unconnected: int | None = None
    for value in reversed(records):
        path = resolve_recorded_path(value)
        if path is None or not path.is_file():
            continue
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if receipt.get("kind") != "routing_candidate" or receipt.get("candidate_sha256") != board_hash:
            continue
        audit_path = resolve_recorded_path(receipt.get("audit"))
        if audit_path is None or not audit_path.is_file():
            continue
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        counts = audit.get("candidate_counts")
        if (
            audit.get("success") is True and audit.get("baseline_pass") is True
            and audit.get("candidate_sha256") == board_hash and isinstance(counts, dict)
        ):
            unconnected = int(counts.get("unconnected_items", -1))
            break
    if unconnected != 0 or audit_path is None:
        return "route_next_functional_block", f'py -3.14 scripts/pcbctl.py route check --input "{routing_board}"', []
    visual_path: Path | None = None
    for value in reversed(records):
        path = resolve_recorded_path(value)
        if path is None or not path.is_file():
            continue
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            receipt.get("kind") == "pcb_visual_review" and receipt.get("review_pass") is True
            and receipt.get("tier") == "checkpoint" and receipt.get("board_sha256") == board_hash
        ):
            visual_path = path
            break
    if visual_path is None:
        return "review_routing_complete_candidate", None, ["checkpoint_visual_review"]
    command = (
        f'py -3.14 scripts/pcbctl.py route checkpoint-finalize --board "{routing_board}" '
        f'--audit "{audit_path}" --visual-review "{visual_path}"'
    )
    return "finalize_routing_checkpoint", command, []


def cached_release_readiness(board: str) -> dict[str, object] | None:
    path = resolve_recorded_path(board)
    return fresh_release_readiness(path, DEFAULT_READINESS) if path is not None else None


def release_navigation(stage: dict[str, object], board: str) -> tuple[str, str | None, list[str]]:
    readiness = cached_release_readiness(board)
    if readiness is None:
        return (
            "refresh_release_readiness",
            f'py -3.14 scripts/pcbctl.py verify readiness --board "{board}"',
            [],
        )
    required = [str(item) for item in list(readiness.get("required_inputs") or [])[:8]]
    return (
        str(readiness.get("next_action") or "refresh_release_readiness"),
        str(readiness["next_command"]) if readiness.get("next_command") else None,
        required,
    )


def receipt() -> dict[str, object]:
    stage = current_stage()
    checklist = json.loads((ROOT / "config/stage-checklist.json").read_text(encoding="utf-8"))
    execution = json.loads((ROOT / "config/execution-policy.json").read_text(encoding="utf-8"))
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    configured_board = str((flow.get("project") or {}).get("board", ""))
    configured_board_path = (ROOT / configured_board).resolve() if configured_board else None
    physical_candidate_exists = bool(
        configured_board_path
        and configured_board_path.is_file()
        and configured_board_path.with_suffix(".physical-origin.json").is_file()
    )
    rules = checklist.get("stages", {}).get(stage["stage"], {})
    number = rules.get("number")
    label = f"{int(number):02d}-{stage['stage']}" if isinstance(number, int) else str(stage["stage"])
    blockers: list[str] = []
    handoff_issues = handoff_integrity_issues(stage)
    blockers.extend(handoff_issues)
    handoff_board = stage.get("board")
    routing_board = (
        str(handoff_board)
        if isinstance(handoff_board, str)
        and handoff_board
        and not any(issue.startswith("handoff_board_") for issue in handoff_issues)
        else configured_board
    )
    routing_board_path = resolve_recorded_path(routing_board)
    routing_board_hash = (
        hashlib.sha256(routing_board_path.read_bytes()).hexdigest().upper()
        if routing_board_path is not None and routing_board_path.is_file() else None
    )
    next_command: str | None
    next_action: str | None = None
    required_inputs: list[str] = []
    if stage.get("status") == "blocked_external_dependency":
        blockers.extend(list(stage.get("open_items", []))[:8])
        next_command = None
        handoff_required_inputs = stage.get("required_inputs", [])
        if isinstance(handoff_required_inputs, list):
            required_inputs = [str(item) for item in handoff_required_inputs[:8]]
        if stage.get("stage") == "electrical" and "interactive_atopile_authentication" in required_inputs:
            next_action = "authenticate_atopile"
    elif stage.get("status") == "complete" and not stage.get("next_actions") and not handoff_issues:
        next_command = None
    elif stage["stage"] == "requirements":
        requirements = json.loads((ROOT / "config/requirements.json").read_text(encoding="utf-8"))
        blockers.extend(problems(requirements)[:8])
        next_action = "validate_requirements"
        next_command = "py -3.14 scripts/validate_requirements.py" if blockers else "py -3.14 scripts/validate_requirements.py --require-reviewed"
    else:
        actions: dict[str, tuple[str, str | None, list[str]]] = {
            "electrical": ("plan_build_or_freeze_electrical", "py -3.14 scripts/pcbctl.py build --plan --backend atopile", []),
            "physical": physical_navigation(physical_candidate_exists, []),
            "routing": routing_checkpoint_navigation(stage, routing_board, routing_board_hash),
            "release": release_navigation(stage, routing_board),
        }
        selected_action = actions.get(stage["stage"])
        if selected_action:
            next_action, next_command, required_inputs = selected_action
        else:
            next_command = None
        handoff_required_inputs = stage.get("required_inputs", [])
        if isinstance(handoff_required_inputs, list) and handoff_required_inputs:
            required_inputs = [str(item) for item in handoff_required_inputs[:8]]
            if stage["stage"] == "electrical" and "interactive_atopile_authentication" in required_inputs:
                next_action = "authenticate_atopile"
                next_command = None
            elif stage["stage"] == "physical":
                next_action, next_command, required_inputs = physical_navigation(
                    physical_candidate_exists, required_inputs
                )
        if stage["stage"] in {"physical", "routing", "release"}:
            policy = compile_policy()
            capability = {"physical": "physical", "routing": "candidate_routing", "release": "release"}[stage["stage"]]
            if not policy["capabilities"][capability]:
                blockers.extend(list(policy["capability_issues"][capability])[:8])
        if stage["stage"] == "release":
            readiness = cached_release_readiness(routing_board)
            if readiness is None:
                blockers.append("release_readiness_missing_or_stale")
            else:
                blockers.extend([
                    str(item.get("code")) for item in list(readiness.get("blockers") or [])
                    if isinstance(item, dict) and item.get("code")
                ][:8])
    blockers = blockers[:8]
    allowed_commands = {
        "requirements": ["validate_requirements", "create_stage_handoff"],
        "electrical": ["pcbctl build", "pcbctl freeze"],
        "physical": ["pcbctl physical-start", "pcbctl place", "pcbctl verify"],
        "routing": ["pcbctl route", "pcbctl verify"],
        "release": ["pcbctl verify readiness", "compile_design_policy", "check_final_electrical_parity", "check_manufacturing_gates", "pcbctl release prepare-dfm", "render_kicad_visual_bundle", "record_pcb_visual_review", "record_physical_review", "run_pcb_pipeline --export", "create_release_snapshot"],
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
