#!/usr/bin/env python3
"""Validate and record a structured visual review bound to a PCB visual bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from artifact_store import create_run, finish_run, relative
from release_inputs import register_release_input
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {"layout", "critical_routing", "return_paths", "silkscreen", "manufacturability"}
SEVERITIES = {"critical", "high", "medium", "low", "note"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def validate_assessment(value: dict[str, object]) -> None:
    if value.get("schema") != 1 or value.get("kind") != "pcb_visual_assessment":
        raise SystemExit("Assessment must be schema-1 pcb_visual_assessment")
    if value.get("verdict") not in {"pass", "revise", "reject"}:
        raise SystemExit("Assessment verdict must be pass, revise, or reject")
    ratings = value.get("ratings")
    if not isinstance(ratings, dict) or set(ratings) != CATEGORIES:
        raise SystemExit("Assessment ratings must contain exactly the five reviewed visual categories")
    if any(not isinstance(score, int) or not 1 <= score <= 5 for score in ratings.values()):
        raise SystemExit("Every visual rating must be an integer from 1 to 5")
    findings = value.get("findings")
    if not isinstance(findings, list) or len(findings) > 8:
        raise SystemExit("Assessment findings must be a list with at most eight items")
    for item in findings:
        if not isinstance(item, dict) or item.get("category") not in CATEGORIES or item.get("severity") not in SEVERITIES:
            raise SystemExit("Every finding requires a reviewed category and severity")
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("observation", "action")):
            raise SystemExit("Every finding requires non-empty observation and action text")
    if value.get("verdict") == "pass" and any(item["severity"] in {"critical", "high"} for item in findings):
        raise SystemExit("A passing visual assessment may not contain critical or high findings")


def validate_local_assessment(value: dict[str, object]) -> None:
    if value.get("schema") != 1 or value.get("kind") != "local_pcb_visual_assessment":
        raise SystemExit("Local assessment must be schema-1 local_pcb_visual_assessment")
    if value.get("verdict") not in {"accept", "awkward", "unsafe"}:
        raise SystemExit("Local assessment verdict must be accept, awkward, or unsafe")
    findings = value.get("findings", [])
    if not isinstance(findings, list) or len(findings) > 4 or any(not isinstance(item, str) or not item.strip() for item in findings):
        raise SystemExit("Local findings must contain at most four non-empty strings")


def validate_semantic_review(value: dict[str, object], board_hash: str, verdict: str) -> bool:
    if value.get("kind") != "kicad_placement_semantics" or value.get("candidate_sha256") != board_hash:
        raise SystemExit("Placement semantic review does not match the reviewed board SHA-256")
    passed = value.get("success") is True
    if verdict == "pass" and not passed:
        raise SystemExit("A passing visual review requires passing placement semantics")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--placement-semantics", type=Path)
    parser.add_argument("--placement-state", choices=("unchanged", "moved"), default="unchanged")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "routing", "release"}:
        raise SystemExit("PCB visual reviews are allowed only during physical, routing, or release stages")
    board = args.board.resolve()
    bundle_path = args.bundle.resolve()
    assessment_path = args.assessment.resolve()
    semantic_path = args.placement_semantics.resolve() if args.placement_semantics else None
    bundle = load_json(bundle_path, "Visual bundle")
    assessment = load_json(assessment_path, "Visual assessment")
    tier = str(bundle.get("tier", "release"))
    if tier == "local":
        validate_local_assessment(assessment)
    else:
        validate_assessment(assessment)
    board_hash = sha256(board)
    semantic_required = tier == "release" or (tier == "checkpoint" and args.placement_state == "moved")
    if semantic_required and (ROOT / "config" / "placement-semantics.json").is_file() and semantic_path is None:
        raise SystemExit(f"A {tier} review with placement state {args.placement_state} requires SHA-bound placement semantics")
    semantic = load_json(semantic_path, "Placement semantic review") if semantic_path else None
    semantic_pass = validate_semantic_review(semantic, board_hash, str(assessment["verdict"])) if semantic else False
    if bundle.get("kind") != "pcb_visual_bundle" or bundle.get("success") is not True or bundle.get("board_sha256") != board_hash:
        raise SystemExit("Visual bundle does not match the reviewed board SHA-256")
    images = bundle.get("images")
    expected_images = {
        "local": {"local_copper"},
        "checkpoint": {"front_copper", "back_copper"},
        "release": {"top_3d", "bottom_3d", "front_copper", "back_copper"},
    }.get(tier)
    if not isinstance(images, dict) or expected_images is None or set(images) != expected_images:
        raise SystemExit("Visual bundle is missing required views")
    for entry in images.values():
        path = Path(str(entry["path"]))
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        if not path.is_file() or sha256(path) != entry.get("sha256"):
            raise SystemExit("Visual bundle image is missing or has changed")
    run = None
    if args.output:
        output = args.output.resolve()
    else:
        run = create_run(stage, "pcb-visual-review", args.run_id)
        output = run.evidence / "visual-review.json"
    verdict = str(assessment["verdict"])
    review_pass = verdict in {"pass", "accept"}
    receipt = {
        "schema": 1, "kind": "pcb_visual_review", "success": review_pass, "review_pass": review_pass,
        "tier": tier, "placement_state": args.placement_state,
        "verdict": verdict, "board_sha256": board_hash,
        "bundle": relative(bundle_path), "bundle_sha256": sha256(bundle_path),
        "assessment": relative(assessment_path), "assessment_sha256": sha256(assessment_path),
        "placement_semantics": relative(semantic_path) if semantic_path else None,
        "placement_semantics_sha256": sha256(semantic_path) if semantic_path else None,
        "placement_semantics_pass": semantic_pass,
        "ratings": assessment.get("ratings"), "summary": str(assessment.get("summary", "")),
        "findings": assessment["findings"],
        "limits": "Visual review is mandatory complementary evidence; it does not replace deterministic electrical or manufacturing gates.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if tier == "release":
        register_release_input("release_visual_review", output, receipt)
    if run:
        finish_run(run, True, artifacts={"visual_review": relative(output), "bundle": relative(bundle_path), "assessment": relative(assessment_path)}, metrics={"verdict": verdict, "finding_count": len(assessment["findings"])})
    print(json.dumps({"success": True, "review_pass": receipt["review_pass"], "tier": tier, "verdict": verdict, "board_sha256": board_hash, "output": relative(output)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
