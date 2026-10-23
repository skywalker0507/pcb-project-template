#!/usr/bin/env python3
"""Classify drilled-hole overlap with paste-bearing SMD pads on an exact KiCad board."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = kicad_python()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    ensure_host_kicad_python()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-findings", type=int, default=100)
    args = parser.parse_args()
    if args.max_findings < 1 or args.max_findings > 500:
        raise SystemExit("--max-findings must be between 1 and 500")
    ensure_kicad_python()
    import pcbnew

    board_path = args.board.resolve()
    board = pcbnew.LoadBoard(str(board_path))
    front_paste = board.GetLayerID("F.Paste")
    back_paste = board.GetLayerID("B.Paste")
    smd_pads: list[tuple[str, object]] = []
    component_holes: list[dict[str, object]] = []
    for footprint in board.GetFootprints():
        ref = footprint.GetReference()
        for pad in footprint.Pads():
            drill = pad.GetDrillSize()
            drill_x = pcbnew.ToMM(drill.x)
            drill_y = pcbnew.ToMM(drill.y)
            if drill_x > 0 or drill_y > 0:
                component_holes.append({
                    "kind": "component_drill",
                    "ref": ref,
                    "pad": pad.GetNumber(),
                    "net": pad.GetNetname(),
                    "position": pad.GetPosition(),
                    "x_mm": round(pcbnew.ToMM(pad.GetPosition().x), 6),
                    "y_mm": round(pcbnew.ToMM(pad.GetPosition().y), 6),
                    "drill_x_mm": round(drill_x, 6),
                    "drill_y_mm": round(drill_y, 6),
                    "source_pad": pad,
                })
            if pad.IsOnLayer(front_paste) or pad.IsOnLayer(back_paste):
                smd_pads.append((ref, pad))

    holes = list(component_holes)
    for item in board.GetTracks():
        if not isinstance(item, pcbnew.PCB_VIA):
            continue
        position = item.GetPosition()
        drill_mm = pcbnew.ToMM(item.GetDrillValue())
        holes.append({
            "kind": "open_via",
            "ref": None,
            "pad": None,
            "net": item.GetNetname(),
            "position": position,
            "x_mm": round(pcbnew.ToMM(position.x), 6),
            "y_mm": round(pcbnew.ToMM(position.y), 6),
            "drill_x_mm": round(drill_mm, 6),
            "drill_y_mm": round(drill_mm, 6),
            "source_pad": None,
        })

    findings: list[dict[str, object]] = []
    for hole in holes:
        radius_iu = pcbnew.FromMM(max(float(hole["drill_x_mm"]), float(hole["drill_y_mm"])) / 2)
        for ref, pad in smd_pads:
            if hole["source_pad"] is pad:
                continue
            if not pad.HitTest(hole["position"], radius_iu):
                continue
            findings.append({
                "classification": (
                    "open_via_intersects_smd_paste_pad"
                    if hole["kind"] == "open_via"
                    else "component_drill_intersects_other_smd_paste_pad"
                ),
                "hole": {
                    key: value for key, value in hole.items()
                    if key not in {"position", "source_pad"}
                },
                "smd_pad": {
                    "ref": ref,
                    "pad": pad.GetNumber(),
                    "net": pad.GetNetname(),
                    "x_mm": round(pcbnew.ToMM(pad.GetPosition().x), 6),
                    "y_mm": round(pcbnew.ToMM(pad.GetPosition().y), 6),
                },
                "same_net": hole["net"] == pad.GetNetname(),
            })

    open_via_findings = [item for item in findings if item["classification"].startswith("open_via")]
    component_findings = [item for item in findings if item["classification"].startswith("component_drill")]
    unique_open_via_intersections = {
        (item["hole"]["x_mm"], item["hole"]["y_mm"], item["hole"]["net"])
        for item in open_via_findings
    }
    open_via_drills = Counter(
        f'{float(item["drill_x_mm"]):.6f}'
        for item in holes if item["kind"] == "open_via"
    )
    payload = {
        "schema": 1,
        "kind": "smt_hole_proximity",
        "success": len(open_via_findings) == 0,
        "board": str(board_path),
        "board_sha256": sha256(board_path),
        "counts": {
            "paste_bearing_smd_pads": len(smd_pads),
            "open_vias": len(holes) - len(component_holes),
            "component_drills": len(component_holes),
            "open_via_intersections": len(open_via_findings),
            "unique_open_via_intersections": len(unique_open_via_intersections),
            "component_drill_intersections": len(component_findings),
        },
        "open_via_drill_distribution_mm": dict(sorted(open_via_drills.items())),
        "findings": findings[:args.max_findings],
        "findings_truncated": len(findings) > args.max_findings,
        "limits": [
            "Drill-to-pad intersection is conservative for plated slots because the maximum drill radius is used.",
            "This does not determine whether a vendor component-model PIN warning is correct.",
            "Open via intersections require review or a documented filling/capping process; component drills are classified separately.",
        ],
    }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
