#!/usr/bin/env python3
"""Record an explicit, evidence-bound manufacturing process confirmation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from artifact_store import create_run, finish_run, relative
from design_policy import MANUFACTURING_REGISTRY_ARTIFACT, write_policy
from engine_runtime import activate
from release_inputs import adopted_routing_board, load_json, sha256
from stage_policy import require_operation

activate()

from pcb_template_engine.manufacturing import evaluate_pre_export  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/manufacturing-profile.json"


def confirmed_profile(
    profile: dict[str, object], process_id: str, outcome: str, evidence_ref: str,
    *, replace: bool = False,
) -> dict[str, object]:
    processes = profile.get("special_processes")
    if not isinstance(processes, dict) or process_id not in processes:
        raise ValueError(f"Unknown configured special process: {process_id}")
    process = processes[process_id]
    if not isinstance(process, dict):
        raise ValueError("Special-process configuration must be an object")
    allowed = process.get("allowed_confirmed_outcomes")
    if not isinstance(allowed, list) or outcome not in allowed:
        raise ValueError("Outcome is not in allowed_confirmed_outcomes")
    if process.get("status") == "confirmed" and not replace:
        raise ValueError("Process is already confirmed; explicit replacement is required")
    updated = json.loads(json.dumps(profile))
    updated_process = updated["special_processes"][process_id]
    updated_process["status"] = "confirmed"
    updated_process["selected_outcome"] = outcome
    updated_process["evidence"] = evidence_ref
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--board", type=Path)
    parser.add_argument("--note")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    require_operation("export")
    board = args.board.resolve() if args.board else adopted_routing_board()
    adopted = adopted_routing_board()
    if board != adopted or sha256(board) != sha256(adopted):
        raise SystemExit("Special-process confirmation accepts only the adopted routing artifact")
    evidence = args.evidence.resolve()
    if not evidence.is_file():
        raise SystemExit(f"Confirmation evidence not found: {evidence}")
    profile = load_json(PROFILE)
    try:
        confirmed_profile(profile or {}, args.process, args.outcome, "pending", replace=args.replace)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    run = create_run("release", "special-process-confirmation", args.run_id)
    receipt_path = run.evidence / "special-process-confirmation.json"
    updated = confirmed_profile(
        profile or {}, args.process, args.outcome, relative(receipt_path), replace=args.replace,
    )
    rendered = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    profile_after_sha = hashlib.sha256(rendered.encode("utf-8")).hexdigest().upper()
    receipt = {
        "schema": 1, "kind": "special_process_confirmation", "success": True,
        "process": args.process, "selected_outcome": args.outcome,
        "board": relative(board), "board_sha256": sha256(board),
        "evidence": relative(evidence), "evidence_sha256": sha256(evidence),
        "note": args.note,
        "profile": relative(PROFILE), "profile_before_sha256": sha256(PROFILE),
        "profile_after_sha256": profile_after_sha,
        "limits": "This records an external manufacturing/CAM confirmation. It does not replace PCB DFM, SMT DFM, Gerber gates, or release approval.",
    }
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary = PROFILE.with_suffix(".json.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    if sha256(temporary) != profile_after_sha:
        raise SystemExit("Manufacturing profile staging hash mismatch")
    temporary.replace(PROFILE)
    policy = write_policy()
    registry = load_json(MANUFACTURING_REGISTRY_ARTIFACT)
    gate = evaluate_pre_export(registry) if registry is not None else {"success": False, "pending": [args.process]}
    finish_run(
        run, True,
        artifacts={"confirmation": relative(receipt_path)},
        metrics={
            "board_sha256": receipt["board_sha256"], "process": args.process,
            "policy_sha256": policy["policy_sha256"], "remaining_pending": gate.get("pending", []),
        },
    )
    print(json.dumps({
        "success": True, "process_confirmed": True, "process": args.process,
        "selected_outcome": args.outcome, "confirmation": relative(receipt_path),
        "policy_sha256": policy["policy_sha256"], "remaining_pending": gate.get("pending", []),
        "next_command": f'py -3.14 scripts/pcbctl.py verify readiness --board "{relative(board)}"',
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
