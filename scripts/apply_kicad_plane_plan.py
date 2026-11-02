#!/usr/bin/env python3
"""Apply one bounded, SHA-guarded copper plane; outer layers use isolated candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from design_policy import DEFAULT_OUTPUT, require_policy
from pcb_policy import configured_board
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def load_plan(path: Path) -> dict[str, object]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Plane plan is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("kind") != "kicad_plane_plan":
        raise SystemExit("Plane plan must be schema-1 kicad_plane_plan")
    if plan.get("layer") not in {"F.Cu", "B.Cu", "In1.Cu", "In2.Cu"}:
        raise SystemExit("Plane plans require an explicit supported copper layer")
    if not isinstance(plan.get("net"), str) or not plan["net"]:
        raise SystemExit("Plane plan requires a net")
    if plan.get("action", "create") not in {"create", "update"}:
        raise SystemExit("Plane plan action must be create or update")
    if plan.get("pad_connection", "solid") not in {"solid", "thermal"}:
        raise SystemExit("Plane pad_connection must be solid or thermal")
    points = plan.get("outline_mm")
    if not isinstance(points, list) or not 3 <= len(points) <= 20:
        raise SystemExit("Plane outline requires 3-20 points")
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise SystemExit("Each plane outline point must be [x_mm, y_mm]")
        try:
            valid = all(not isinstance(value, bool) and math.isfinite(float(value)) for value in point)
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise SystemExit("Plane coordinates must be finite numbers")
    return plan


def candidate_paths(source: Path | None, output: Path | None, layer: str) -> tuple[Path, Path]:
    if (source is None) != (output is None):
        raise SystemExit("Plane candidate edits require both --input and --output")
    if source is None:
        if layer in {"F.Cu", "B.Cu"}:
            raise SystemExit("Outer planes require an isolated --input/--output candidate")
        active = configured_board(ROOT).resolve()
        return active, active
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise SystemExit("Plane input board is missing")
    if source == output or output.exists():
        raise SystemExit("Plane output must be a new isolated candidate")
    return source, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if current_stage().get("stage") != "physical":
        raise SystemExit("Plane plans are allowed only during the physical stage")
    plan_path = args.plan.resolve()
    plan = load_plan(plan_path)
    board_path, output = candidate_paths(args.input, args.output, str(plan["layer"]))
    before = sha256(board_path)
    if plan.get("board_sha256") != before:
        raise SystemExit("Plane plan board SHA-256 does not match the active candidate")
    policy = require_policy("physical", args.policy.resolve())
    reviewed_clearance = float(policy["manufacturing"]["routing_values"]["clearance"])
    requested_clearance = float(plan.get("clearance_mm", reviewed_clearance))
    if not math.isfinite(requested_clearance) or requested_clearance <= 0 or requested_clearance != reviewed_clearance:
        raise SystemExit("Plane clearance must match the reviewed resolved policy")

    ensure_kicad_python()
    import pcbnew

    board = pcbnew.LoadBoard(str(board_path))
    layer_id = board.GetLayerID(str(plan["layer"]))
    if not board.IsLayerEnabled(layer_id):
        raise SystemExit(f"Plane layer is not enabled in the actual board stack: {plan['layer']}")
    net = board.FindNet(str(plan["net"]))
    if net is None:
        raise SystemExit(f"Plane net is missing from board: {plan['net']}")
    matching_zones = [zone for zone in board.Zones() if zone.GetLayer() == layer_id and zone.GetNetname() == str(plan["net"])]
    action = str(plan.get("action", "create"))
    if action == "create" and matching_zones:
        raise SystemExit("A plane for this net and layer already exists")
    if action == "update" and len(matching_zones) != 1:
        raise SystemExit("Plane update requires exactly one existing plane for the net and layer")

    zone = matching_zones[0] if action == "update" else pcbnew.ZONE(board)
    zone.SetLayer(layer_id)
    zone.SetNet(net)
    zone.SetZoneName(str(plan.get("name", f"{plan['net']} {plan['layer']} plane")))
    zone.SetLocalClearance(pcbnew.FromMM(requested_clearance))
    zone.SetMinThickness(pcbnew.FromMM(requested_clearance))
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL if plan.get("pad_connection", "solid") == "solid" else pcbnew.ZONE_CONNECTION_THERMAL)
    if action == "create":
        outline = zone.Outline()
        outline_index = outline.NewOutline()
        for x_mm, y_mm in plan["outline_mm"]:
            outline.Append(pcbnew.FromMM(float(x_mm)), pcbnew.FromMM(float(y_mm)), outline_index)
        board.Add(zone)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    run = create_run("physical", "kicad-plane", args.run_id)
    if output != board_path:
        # Reuse the trace editor's tested project/rule/library-table rebasing.
        from apply_kicad_trace_plan import copy_candidate
        copy_candidate(board_path, output)
    pcbnew.SaveBoard(str(output), board)
    receipt = {
        "schema": 1,
        "kind": "kicad_plane",
        "success": True,
        "input": relative(board_path),
        "board": relative(output),
        "before_sha256": before,
        "board_sha256": sha256(output),
        "isolated_candidate": output != board_path,
        "plan_sha256": sha256(plan_path),
        "plan": relative(plan_path),
        "layer": str(plan["layer"]),
        "net": str(plan["net"]),
        "clearance_mm": requested_clearance,
        "outline_point_count": len(plan["outline_mm"]),
        "action": action,
        "pad_connection": str(plan.get("pad_connection", "solid")),
        "limits": "Plane creation and fill only; DRC, return-path review, routing and release remain separate gates.",
    }
    evidence = run.evidence / "kicad-plane.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={"receipt": relative(evidence), "plan": relative(plan_path)}, metrics={"zones": 1})
    print(json.dumps({**receipt, "receipt": relative(evidence)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
