#!/usr/bin/env python3
"""Record a SHA-bound review of declared physical PCB requirements."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from artifact_store import create_run, finish_run, relative
from design_policy import DEFAULT_OUTPUT, require_policy
from pcb_policy import configured_board
from release_inputs import register_release_input
from stage_policy import current_stage, require_operation


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def resolve_evidence(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def load_evidence(value: str, label: str) -> tuple[Path, dict[str, object]]:
    path = resolve_evidence(value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} evidence is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{label} evidence must be a JSON object")
    return path, payload


def placement_evidence_matches(payload: dict[str, object], board_hash: str) -> bool:
    return bool(
        payload.get("kind") == "kicad_placement_semantics"
        and payload.get("success") is True
        and payload.get("candidate_sha256") == board_hash
    )


def routing_evidence_matches(payload: dict[str, object], board_hash: str) -> bool:
    bound_hash = payload.get("board_sha256", payload.get("candidate_sha256"))
    return bool(
        bound_hash == board_hash
        and (isinstance(payload.get("routed_net_metrics"), list) or payload.get("success") is True)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, default=configured_board(ROOT))
    parser.add_argument("--policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--placement-evidence", action="append", default=[])
    parser.add_argument("--critical-net-evidence", action="append", default=[])
    parser.add_argument("--return-path-evidence", action="append", default=[])
    parser.add_argument("--visual-review", type=Path)
    args = parser.parse_args()
    require_operation("record_review")
    run = None
    if args.output is None:
        selected_stage = str(current_stage().get("stage"))
        artifact_stage = selected_stage if selected_stage in {"physical", "release"} else "physical"
        run = create_run(artifact_stage, "physical-review", args.run_id)
        output = run.evidence / "physical-review.json"
    else:
        output = args.output.resolve()
    # Placement and return-path review happens before fab-specific routing
    # geometry is approved.  Requiring the release capability here made it
    # impossible to retain legitimate physical-stage evidence, even though
    # the stage policy explicitly permits physical work.  Routing and release
    # remain separately guarded by their stricter capabilities.
    policy = require_policy("physical", args.policy.resolve())
    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    visual_review_pass = False
    visual_review_path = None
    if args.visual_review:
        visual_review_path = args.visual_review.resolve()
        try:
            visual_review = json.loads(visual_review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Visual review receipt is unavailable or invalid: {visual_review_path}: {exc}") from exc
        visual_review_pass = (
            visual_review.get("kind") == "pcb_visual_review"
            and visual_review.get("review_pass") is True
            and visual_review.get("board_sha256") == sha256(board)
        )
    evidence = {
        "placement": [item.strip() for item in args.placement_evidence if item.strip()],
        "critical_nets": [item.strip() for item in args.critical_net_evidence if item.strip()],
        "return_paths": [item.strip() for item in args.return_path_evidence if item.strip()],
        "visual": [relative(visual_review_path)] if visual_review_path else [],
    }
    board_hash = sha256(board)
    evidence_hashes: dict[str, list[str]] = {name: [] for name in evidence}
    placement_valid = False
    for value in evidence["placement"]:
        path, payload = load_evidence(value, "Placement")
        evidence_hashes["placement"].append(sha256(path))
        placement_valid = placement_valid or placement_evidence_matches(payload, board_hash)
    audit_valid: dict[str, bool] = {"critical_nets": False, "return_paths": False}
    for category in audit_valid:
        for value in evidence[category]:
            path, payload = load_evidence(value, category)
            evidence_hashes[category].append(sha256(path))
            audit_valid[category] = audit_valid[category] or routing_evidence_matches(payload, board_hash)
    if visual_review_path:
        evidence_hashes["visual"].append(sha256(visual_review_path))
    required = ["placement", "return_paths", "visual"]
    if policy.get("requirements", {}).get("critical_nets"):
        required.append("critical_nets")
    missing = [name for name in required if not evidence[name] or (name == "visual" and not visual_review_pass)]
    if "placement" in required and not placement_valid and "placement" not in missing:
        missing.append("placement")
    for category, valid in audit_valid.items():
        if category in required and not valid and category not in missing:
            missing.append(category)
    receipt = {
        "schema": 1,
        "kind": "physical_review",
        "success": not missing,
        "review_pass": not missing,
        "board": relative(board),
        "board_sha256": board_hash,
        "policy_sha256": policy["policy_sha256"],
        "required_categories": required,
        "missing_categories": missing,
        "evidence": evidence,
        "evidence_sha256": evidence_hashes,
        "visual_review_pass": visual_review_pass,
        "visual_review_sha256": sha256(visual_review_path) if visual_review_path else None,
        "note": "Evidence is SHA-bound reviewer evidence, not an automatic physical-quality score.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_release_input("physical_review", output, receipt)
    if run:
        finish_run(
            run, bool(receipt["review_pass"]),
            artifacts={"physical_review": relative(output)},
            metrics={"board_sha256": receipt["board_sha256"], "missing_categories": missing},
        )
    print(json.dumps({"success": receipt["review_pass"], "output": relative(output), "missing_categories": missing, "board_sha256": receipt["board_sha256"]}, separators=(",", ":")))
    return 0 if receipt["review_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
