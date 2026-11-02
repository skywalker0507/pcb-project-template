#!/usr/bin/env python3
"""Prove that a routed KiCad candidate preserved every footprint placement."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"Expected JSON object: {path}")
    return value


def native_layout_signature(path: Path) -> str:
    """Inputs to placement checks only; copper is deliberately excluded."""
    import pcbnew
    from check_kicad_footprint_equivalence import pad_signature

    board = pcbnew.LoadBoard(str(path))
    footprints = {}
    for item in board.GetFootprints():
        ref = str(item.GetReference())
        if ref in footprints:
            raise SystemExit(f"Duplicate footprint reference: {ref}")
        footprints[ref] = {
            "pose": [item.GetPosition().x, item.GetPosition().y,
                     float(item.GetOrientationDegrees()), int(item.GetLayer()), bool(item.IsLocked())],
            "library": str(item.GetFPIDAsString()),
            "pads": sorted([
                [str(pad.GetNetname()), pad.GetPosition().x, pad.GetPosition().y, pad_signature(pcbnew, pad)]
                for pad in item.Pads()
            ], key=repr),
        }
    polygons = pcbnew.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(polygons, False):
        raise SystemExit("Placement inheritance requires a closed native board outline")
    def points(chain):
        return [(chain.CPoint(i).x, chain.CPoint(i).y) for i in range(chain.PointCount())]
    outlines = [
        [points(polygons.COutline(i)), [points(polygons.CHole(i, j)) for j in range(polygons.HoleCount(i))]]
        for i in range(polygons.OutlineCount())
    ]
    bbox = board.GetBoardEdgesBoundingBox()
    inputs = {"footprints": footprints, "outlines": outlines,
              "outline_bbox": [bbox.GetX(), bbox.GetY(), bbox.GetWidth(), bbox.GetHeight()],
              "copper_layer_count": board.GetCopperLayerCount()}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest().upper()


def inherit_checkpoint(source: Path, candidate: Path, checkpoint_path: Path, reference_policy: Path | None) -> dict[str, object]:
    checkpoint = load(checkpoint_path)
    if (checkpoint.get("kind") != "pcb_pipeline_summary" or checkpoint.get("success") is not True
            or checkpoint.get("inputs_stable") is not True or checkpoint.get("board_sha256") != sha256(source)):
        raise SystemExit("Inheritance requires a passing, stable checkpoint for the exact reference board")
    inputs = checkpoint.get("checkpoint_inputs") or {}
    dependencies: dict[str, str | None] = {}
    def bind(path: Path, expected: str | None = None) -> None:
        actual = sha256(path)
        if expected is not None and actual != expected:
            raise SystemExit(f"Placement inheritance dependency changed: {path}")
        dependencies[str(path.resolve())] = actual
    bind(source); bind(candidate); bind(checkpoint_path)
    baseline = resolve(checkpoint["requested"]["baseline_board"])
    bind(baseline, checkpoint["baseline_board_sha256"])
    evidence = checkpoint.get("evidence_hashes") or {}
    sources = {}
    for name in ("placement-semantics", "routability"):
        paths = [resolve(path) for path in evidence if Path(path).name == f"{name}.json"]
        if len(paths) != 1:
            raise SystemExit(f"Checkpoint lacks unique {name} evidence")
        path = paths[0]
        expected = next(value for key, value in evidence.items() if resolve(key) == path)
        bind(path, expected)
        sources[name] = str(path)
    semantic, routability = load(Path(sources["placement-semantics"])), load(Path(sources["routability"]))
    if (semantic.get("kind") != "kicad_placement_semantics" or semantic.get("success") is not True
            or semantic.get("candidate_sha256") != sha256(source)
            or routability.get("kind") != "kicad_placement_routability" or routability.get("success") is not True
            or (routability.get("candidate") or {}).get("board_sha256") != sha256(source)
            or (routability.get("comparison") or {}).get("improved_without_critical_regression") is not True):
        raise SystemExit("Checkpoint lacks passing reference semantics/routability")
    bind(resolve(semantic["contract"]), semantic["contract_sha256"])
    policy_path = ROOT / "config/placement-routability.json"
    old_policy_path = reference_policy or policy_path
    expected_policy = inputs.get("config/placement-routability.json")
    if not isinstance(expected_policy, str):
        raise SystemExit("Checkpoint has no policy binding; inheritance is unavailable")
    bind(old_policy_path, expected_policy); bind(policy_path)
    # Probe coverage can grow after layout; current coverage is enforced by
    # promotion. All metric/comparison settings must remain identical.
    metric_policy = lambda value: {key: item for key, item in value.items() if key != "checkpoint"}
    if metric_policy(load(old_policy_path)) != metric_policy(load(policy_path)):
        raise SystemExit("Routability metric policy changed; cannot inherit old comparison")
    for name in ("check_kicad_placement_semantics.py", "check_kicad_placement_routability.py"):
        key = f"scripts/{name}"
        if key not in inputs:
            raise SystemExit(f"Checkpoint lacks placement checker binding: {key}")
        bind(ROOT / key, inputs[key])
    native_names = {"pcbnew.py", "_pcbnew.pyd", "python.exe"}
    if not native_names.issubset({Path(key).name for key in inputs}):
        raise SystemExit("Checkpoint lacks native placement runtime bindings")
    for key, expected in inputs.items():
        if Path(key).name in {"pcbnew.py", "_pcbnew.pyd", "python.exe"}:
            bind(resolve(key), expected)
    for path in (Path(__file__), ROOT / "scripts/check_kicad_footprint_equivalence.py"):
        bind(path)
    for suffix in (".kicad_pro", ".kicad_dru"):
        for board_path in (source, candidate):
            companion = board_path.with_suffix(suffix)
            if companion.is_file():
                bind(companion)
            else:
                dependencies[str(companion.resolve())] = None
    for board_path in (source, candidate):
        table = board_path.parent / "fp-lib-table"
        if table.is_file():
            bind(table)
        else:
            dependencies[str(table.resolve())] = None
    before, after = native_layout_signature(source), native_layout_signature(candidate)
    if before != after:
        raise SystemExit("Placement inputs changed: pose, pads, nets, footprint identity, outline or stack")
    return {"schema": 1, "success": True, "reference_sha256": sha256(source),
            "native_layout_sha256": before, "source_checkpoint": str(checkpoint_path),
            "source_semantics": sources["placement-semantics"], "source_routability": sources["routability"],
            "dependencies": dependencies,
            "limits": "Reuses placement evidence only; current critical probes, integrity, electrical parity, DRC and visual gates remain required."}


def validate_inheritance(path: Path, candidate_hash: str, *, semantic: Path | None = None, routability: Path | None = None) -> dict[str, object]:
    receipt = load(path)
    inherited = receipt.get("evidence_inheritance") or {}
    if (receipt.get("kind") != "kicad_placement_immutability" or receipt.get("success") is not True
            or receipt.get("candidate_sha256") != candidate_hash or inherited.get("success") is not True
            or inherited.get("schema") != 1 or not inherited.get("dependencies")):
        raise SystemExit("A matching passing native placement-inheritance receipt is required")
    deps = inherited["dependencies"]
    if (inherited.get("reference_sha256") != receipt.get("input_sha256")
            or deps.get(str(resolve(receipt["input"]))) != receipt.get("input_sha256")
            or deps.get(str(resolve(receipt["candidate"]))) != candidate_hash
            or any(str(resolve(inherited[name])) not in deps for name in ("source_semantics", "source_routability"))):
        raise SystemExit("Placement inheritance reference/candidate bindings are inconsistent")
    for name, expected in inherited["dependencies"].items():
        target = resolve(name)
        if (target.exists() if expected is None else not target.is_file() or sha256(target) != expected):
            raise SystemExit(f"Placement inheritance dependency changed: {target}")
    for name, supplied in (("source_semantics", semantic), ("source_routability", routability)):
        if supplied is not None and resolve(inherited[name]) != supplied.resolve():
            raise SystemExit(f"Placement inheritance does not bind supplied {name}")
    return inherited


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inherit-checkpoint", type=Path, help="Reuse exact reference semantics/scoring after native input equivalence.")
    parser.add_argument("--reference-policy", type=Path, help="Original policy archive matching the reference checkpoint hash.")
    args = parser.parse_args()
    source, candidate, output = args.input.resolve(), args.candidate.resolve(), args.output.resolve()
    if not source.is_file() or not candidate.is_file():
        raise SystemExit("Input and candidate boards must exist")
    ensure_kicad_python()
    import pcbnew

    def placements(path: Path) -> dict[str, dict[str, object]]:
        board = pcbnew.LoadBoard(str(path))
        return {
            str(item.GetReference()): {
                "x_nm": item.GetPosition().x,
                "y_nm": item.GetPosition().y,
                "rotation_deg": round(float(item.GetOrientationDegrees()), 6),
                "layer": str(item.GetLayerName()),
                "locked": bool(item.IsLocked()),
            }
            for item in board.GetFootprints()
        }

    before, after = placements(source), placements(candidate)
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = [
        {"reference": ref, "input": before[ref], "candidate": after[ref]}
        for ref in sorted(set(before) & set(after))
        if before[ref] != after[ref]
    ]
    success = not missing and not added and not changed
    inherited = None
    if args.reference_policy and not args.inherit_checkpoint:
        raise SystemExit("--reference-policy requires --inherit-checkpoint")
    if args.inherit_checkpoint:
        if not success:
            raise SystemExit("Cannot inherit placement evidence after footprint movement")
        inherited = inherit_checkpoint(source, candidate, args.inherit_checkpoint.resolve(),
                                       args.reference_policy.resolve() if args.reference_policy else None)
    receipt = {
        "schema": 1,
        "kind": "kicad_placement_immutability",
        "success": success,
        "input": str(source),
        "input_sha256": sha256(source),
        "candidate": str(candidate),
        "candidate_sha256": sha256(candidate),
        "footprint_count": {"input": len(before), "candidate": len(after)},
        "locked_count": {
            "input": sum(bool(item["locked"]) for item in before.values()),
            "candidate": sum(bool(item["locked"]) for item in after.values()),
        },
        "missing": missing,
        "added": added,
        "changed": changed,
        "evidence_inheritance": inherited,
        "limits": "Footprint position, rotation, side and lock-state comparison only; copper, DRC and electrical parity are separate gates.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": success,
        "input_sha256": receipt["input_sha256"],
        "candidate_sha256": receipt["candidate_sha256"],
        "footprint_count": receipt["footprint_count"],
        "locked_count": receipt["locked_count"],
        "changed_count": len(changed),
        "output": str(output),
    }, separators=(",", ":")))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
