#!/usr/bin/env python3
"""Compare instance-level functional placement relationships on two KiCad boards."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")


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


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def board_pads(path: Path) -> tuple[dict[str, dict[str, list[tuple[float, float]]]], dict[str, tuple[float, float, float]]]:
    import pcbnew  # type: ignore

    board = pcbnew.LoadBoard(str(path))
    pads: dict[str, dict[str, list[tuple[float, float]]]] = {}
    placements: dict[str, tuple[float, float, float]] = {}
    for footprint in board.GetFootprints():
        ref = footprint.GetReference()
        at = footprint.GetPosition()
        placements[ref] = (at.x / 1_000_000, at.y / 1_000_000, footprint.GetOrientationDegrees())
        ref_pads: dict[str, list[tuple[float, float]]] = {}
        for pad in footprint.Pads():
            pos = pad.GetPosition()
            ref_pads.setdefault(pad.GetNumber(), []).append((pos.x / 1_000_000, pos.y / 1_000_000))
        pads[ref] = ref_pads
    return pads, placements


def endpoint_positions(pads: dict[str, dict[str, list[tuple[float, float]]]], endpoint: object, relation_id: str) -> list[tuple[float, float]]:
    if not isinstance(endpoint, dict) or not isinstance(endpoint.get("ref"), str) or not isinstance(endpoint.get("pads"), list):
        raise SystemExit(f"Relation {relation_id} has an invalid endpoint")
    ref = endpoint["ref"]
    numbers = endpoint["pads"]
    if ref not in pads or not numbers or any(not isinstance(number, str) or not number for number in numbers):
        raise SystemExit(f"Relation {relation_id} references a missing component or pad list")
    positions = [position for number in numbers for position in pads[ref].get(number, [])]
    if not positions:
        raise SystemExit(f"Relation {relation_id} has no matching pads on {ref}")
    return positions


def measure_relation(pads: dict[str, dict[str, list[tuple[float, float]]]], relation: dict[str, object]) -> float:
    relation_id = str(relation.get("id", ""))
    left = endpoint_positions(pads, relation.get("from"), relation_id)
    right = endpoint_positions(pads, relation.get("to"), relation_id)
    distances = [[math.hypot(ax - bx, ay - by) for bx, by in right] for ax, ay in left]
    aggregate = relation.get("aggregate")
    if aggregate == "minimum":
        return min(value for row in distances for value in row)
    if aggregate == "maximum_nearest":
        return max(min(row) for row in distances)
    raise SystemExit(f"Relation {relation_id} has unsupported aggregate {aggregate!r}")


def validate_contract(contract: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], float]:
    if contract.get("schema") != 1 or contract.get("status") != "reviewed":
        raise SystemExit("Placement semantics must be a reviewed schema-1 contract")
    roles = contract.get("roles")
    relations = contract.get("relations")
    policy = contract.get("measurement_policy")
    if not isinstance(roles, list) or not roles or not isinstance(relations, list) or not relations or not isinstance(policy, dict):
        raise SystemExit("Placement semantics requires roles, relations and measurement_policy")
    typed_roles = [item for item in roles if isinstance(item, dict)]
    typed_relations = [item for item in relations if isinstance(item, dict)]
    if len(typed_roles) != len(roles) or len({item.get("ref") for item in typed_roles}) != len(typed_roles):
        raise SystemExit("Placement semantic roles require unique component references")
    if len(typed_relations) != len(relations) or len({item.get("id") for item in typed_relations}) != len(typed_relations):
        raise SystemExit("Placement semantic relations require unique identifiers")
    for item in typed_relations:
        if item.get("direction") not in {"non_increasing", "bounded_regression", "informational"} or not isinstance(item.get("source"), str) or not item["source"].strip():
            raise SystemExit("Every placement relation requires a direction and reviewed source")
        if item.get("direction") == "bounded_regression" and (
            not isinstance(item.get("max_regression_mm"), (int, float))
            or float(item["max_regression_mm"]) < 0
            or not isinstance(item.get("limit_source"), str)
        ):
            raise SystemExit("Bounded placement regressions require max_regression_mm and limit_source")
        if "max_distance_mm" in item and not isinstance(item.get("limit_source"), str):
            raise SystemExit("Absolute placement limits require limit_source")
    tolerance = float(policy.get("regression_tolerance_mm", 0.001))
    if not 0 <= tolerance <= 0.1:
        raise SystemExit("Placement regression tolerance must remain a small comparison tolerance")
    return typed_roles, typed_relations, tolerance


def placement_changed(before: tuple[float, float, float], after: tuple[float, float, float], tolerance: float = 0.000001) -> bool:
    return any(abs(a - b) > tolerance for a, b in zip(before, after))


def main() -> int:
    ensure_kicad_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=ROOT / "config" / "placement-semantics.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "routing", "release"}:
        raise SystemExit("Placement semantic checks are allowed only during physical, routing, or release stages")
    input_path = args.input.resolve()
    candidate_path = args.candidate.resolve()
    contract_path = args.contract.resolve()
    if not input_path.is_file() or not candidate_path.is_file():
        raise SystemExit("Input and candidate boards must exist")
    contract = load_json(contract_path, "Placement semantics")
    roles, relations, tolerance = validate_contract(contract)
    input_pads, input_placements = board_pads(input_path)
    candidate_pads, candidate_placements = board_pads(candidate_path)
    required_refs = {str(item["ref"]) for item in roles}
    missing = sorted(required_refs - set(input_pads) | required_refs - set(candidate_pads))
    if missing:
        raise SystemExit("Placement semantic role references are missing: " + ", ".join(missing))
    results = []
    failures = []
    for relation in relations:
        before = measure_relation(input_pads, relation)
        after = measure_relation(candidate_pads, relation)
        delta = after - before
        direction = relation["direction"]
        passed = direction == "informational" or delta <= tolerance
        if direction == "bounded_regression":
            passed = delta <= float(relation["max_regression_mm"]) + tolerance
        absolute = relation.get("max_distance_mm")
        if absolute is not None:
            passed = passed and after <= float(absolute) + tolerance
        row = {
            "id": relation["id"], "category": relation.get("category"),
            "before_mm": round(before, 6), "candidate_mm": round(after, 6),
            "delta_mm": round(delta, 6), "pass": passed, "source": relation["source"],
        }
        if direction == "bounded_regression":
            row["max_regression_mm"] = float(relation["max_regression_mm"])
            row["limit_source"] = relation["limit_source"]
        results.append(row)
        if not passed:
            failures.append(row)
    moved = []
    for ref in sorted(set(input_placements) & set(candidate_placements)):
        before = input_placements[ref]
        after = candidate_placements[ref]
        if placement_changed(before, after):
            moved.append({"ref": ref, "before": [round(value, 6) for value in before], "candidate": [round(value, 6) for value in after]})
    locked_refs = {str(item["ref"]) for item in roles if item.get("mobility") == "locked_without_reviewed_mechanical_revision"}
    for row in moved:
        if row["ref"] in locked_refs:
            failure = {
                "id": f"mechanical-anchor-{row['ref']}", "category": "mechanical_anchor",
                "before_mm": row["before"][:2], "candidate_mm": row["candidate"][:2],
                "delta_mm": None, "pass": False,
                "source": "config/placement-semantics.json reviewed mechanical role",
            }
            failures.append(failure)
    success = not failures
    run = None
    if args.output:
        output = args.output.resolve()
    else:
        run = create_run(stage, "placement-semantics", args.run_id)
        output = run.evidence / "placement-semantics.json"
    receipt = {
        "schema": 1, "kind": "kicad_placement_semantics", "success": success,
        "input": relative(input_path), "input_sha256": sha256(input_path),
        "candidate": relative(candidate_path), "candidate_sha256": sha256(candidate_path),
        "contract": relative(contract_path), "contract_sha256": sha256(contract_path),
        "role_count": len(roles), "relation_count": len(relations),
        "moved_components": moved, "relations": results, "failures": failures,
        "limits": "Pad-distance regression gate only; loop area, return paths, SI, DRC, mechanical access and manufacturing remain separate gates."
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if run:
        finish_run(run, success, artifacts={"receipt": relative(output)}, metrics={"roles": len(roles), "relations": len(relations), "failures": len(failures)})
    print(json.dumps({"success": success, "candidate_sha256": receipt["candidate_sha256"], "moved_components": len(moved), "relation_count": len(results), "failure_count": len(failures), "failure_preview": failures[:8], "output": relative(output)}, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
