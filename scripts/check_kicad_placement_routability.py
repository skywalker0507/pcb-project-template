#!/usr/bin/env python3
"""Emit a compact relative routability score without exposing PCB source."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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


def mst(points: list[tuple[float, float]]) -> tuple[float, list[tuple[tuple[float, float], tuple[float, float]]]]:
    if len(points) < 2:
        return 0.0, []
    used = {0}
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    total = 0.0
    while len(used) < len(points):
        best: tuple[float, int, int] | None = None
        for left in used:
            for right in range(len(points)):
                if right in used:
                    continue
                distance = math.dist(points[left], points[right])
                if best is None or distance < best[0]:
                    best = (distance, left, right)
        assert best is not None
        distance, left, right = best
        used.add(right)
        total += distance
        edges.append((points[left], points[right]))
    return total, edges


def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def crosses(first, second) -> bool:
    a, b = first
    c, d = second
    if a in second or b in second:
        return False
    return orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(c, d, a) * orientation(c, d, b) < 0


def board_metrics(path: Path, policy: dict[str, object]) -> dict[str, object]:
    import pcbnew

    board = pcbnew.LoadBoard(str(path))
    outline = board.GetBoardEdgesBoundingBox()
    board_width_mm = round(pcbnew.ToMM(outline.GetWidth()), 4)
    board_height_mm = round(pcbnew.ToMM(outline.GetHeight()), 4)
    nets: dict[str, list[tuple[float, float]]] = {}
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            name = str(pad.GetNetname())
            if not name:
                continue
            position = pad.GetPosition()
            point = (round(pcbnew.ToMM(position.x), 4), round(pcbnew.ToMM(position.y), 4))
            if point not in nets.setdefault(name, []):
                nets[name].append(point)

    excluded = set(policy.get("exclude_from_airwire_score") or [])
    weights = dict((policy.get("weights") or {}).get("nets") or {})
    default_weight = float((policy.get("weights") or {}).get("default", 1))
    net_rows: dict[str, dict[str, object]] = {}
    all_edges: list[tuple[str, tuple[tuple[float, float], tuple[float, float]]]] = []
    weighted_mst = total_mst = total_hpwl = 0.0
    for name, points in sorted(nets.items()):
        if len(points) < 2 or name in excluded:
            continue
        length, edges = mst(points)
        hpwl = max(point[0] for point in points) - min(point[0] for point in points) + max(point[1] for point in points) - min(point[1] for point in points)
        weight = float(weights.get(name, default_weight))
        net_rows[name] = {"pads": len(points), "hpwl_mm": round(hpwl, 3), "mst_mm": round(length, 3), "weight": weight}
        total_mst += length
        total_hpwl += hpwl
        weighted_mst += length * weight
        all_edges.extend((name, edge) for edge in edges)

    crossings = 0
    for index, (left_name, left_edge) in enumerate(all_edges):
        for right_name, right_edge in all_edges[index + 1:]:
            if left_name != right_name and crosses(left_edge, right_edge):
                crossings += 1

    footprint_rows = []
    for footprint in board.GetFootprints():
        position = footprint.GetPosition()
        footprint_rows.append((
            str(footprint.GetReference()),
            round(pcbnew.ToMM(position.x), 4), round(pcbnew.ToMM(position.y), 4),
            round(float(footprint.GetOrientationDegrees()), 3), int(footprint.GetLayer()),
        ))
    placement_hash = hashlib.sha256(json.dumps(sorted(footprint_rows), separators=(",", ":")).encode()).hexdigest().upper()

    groups: dict[str, object] = {}
    for group, names in dict(policy.get("critical_groups") or {}).items():
        rows = [net_rows[name] for name in names if name in net_rows]
        groups[group] = {
            "nets_present": len(rows),
            "mst_mm": round(sum(float(row["mst_mm"]) for row in rows), 3),
            "weighted_mst": round(sum(float(row["mst_mm"]) * float(row["weight"]) for row in rows), 3),
        }
    worst = sorted(net_rows.items(), key=lambda item: float(item[1]["mst_mm"]) * float(item[1]["weight"]), reverse=True)[:10]
    return {
        "board_sha256": sha256(path), "placement_sha256": placement_hash,
        "board_width_mm": board_width_mm, "board_height_mm": board_height_mm,
        "board_bbox_area_mm2": round(board_width_mm * board_height_mm, 3),
        "footprints": len(footprint_rows), "scored_nets": len(net_rows),
        "total_hpwl_mm": round(total_hpwl, 3), "total_mst_mm": round(total_mst, 3),
        "weighted_mst": round(weighted_mst, 3), "airwire_crossings": crossings,
        "critical_groups": groups,
        "highest_cost_nets": [{"net": name, **row} for name, row in worst],
    }


def compare(
    candidate: dict[str, object], baseline: dict[str, object], hard_groups: list[str] | None = None,
) -> dict[str, object]:
    keys = ("total_hpwl_mm", "total_mst_mm", "weighted_mst", "airwire_crossings")
    deltas = {key: round(float(candidate[key]) - float(baseline[key]), 3) for key in keys}
    critical: dict[str, object] = {}
    candidate_groups = dict(candidate.get("critical_groups") or {})
    baseline_groups = dict(baseline.get("critical_groups") or {})
    for name in sorted(set(candidate_groups) | set(baseline_groups)):
        current = float((candidate_groups.get(name) or {}).get("weighted_mst", 0))
        old = float((baseline_groups.get(name) or {}).get("weighted_mst", 0))
        critical[name] = {"weighted_mst_delta": round(current - old, 3), "regressed": current > old + 0.001}
    hard_groups = hard_groups or list(critical)
    hard_regressions = sorted(name for name in hard_groups if (critical.get(name) or {}).get("regressed") is True)
    outline_changed = any(
        abs(float(candidate[key]) - float(baseline[key])) > 0.001
        for key in ("board_width_mm", "board_height_mm")
    )
    area_delta = round(float(candidate["board_bbox_area_mm2"]) - float(baseline["board_bbox_area_mm2"]), 3)
    improved = deltas["weighted_mst"] < 0 and deltas["airwire_crossings"] <= 0 and not hard_regressions and not outline_changed
    return {
        "improved_without_critical_regression": improved,
        "outline_changed": outline_changed, "board_bbox_area_delta_mm2": area_delta,
        "hard_group_regressions": hard_regressions,
        "deltas": deltas, "critical_groups": critical,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="Baseline .kicad_pcb used for relative comparison.")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/placement-routability.json")
    parser.add_argument("--require-improvement", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    board = args.board.resolve()
    baseline_path = args.baseline.resolve() if args.baseline else None
    if not board.is_file() or (baseline_path and not baseline_path.is_file()):
        raise SystemExit("Candidate or baseline board is missing")
    ensure_kicad_python()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    candidate = board_metrics(board, policy)
    comparison = compare(
        candidate, board_metrics(baseline_path, policy),
        [str(value) for value in policy.get("hard_no_regression_groups", [])],
    ) if baseline_path else None
    success = not args.require_improvement or bool(comparison and comparison["improved_without_critical_regression"])
    receipt = {
        "schema": 1, "kind": "kicad_placement_routability", "success": success,
        "method": policy.get("method"), "candidate": candidate, "comparison": comparison,
        "critical_probe_receipts_required": bool((policy.get("checkpoint") or {}).get("require_critical_probe_receipts")),
        "limits": policy.get("limits"),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
