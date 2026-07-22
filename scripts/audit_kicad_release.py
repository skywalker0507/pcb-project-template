#!/usr/bin/env python3
"""Run KiCad CLI DRC and emit a compact, deterministic routing audit."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KICAD_CLI = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
DEFAULT_BASELINE = ROOT / "config/pcb-release-baseline.json"
DEFAULT_ROUTING_PATHS = ROOT / "config/routing-paths.json"
FATAL_CATEGORIES = {
    "shorting_items",
    "clearance",
    "tracks_crossing",
    "track_dangling",
    "via_dangling",
    "via_diameter",
    "drill_out_of_range",
    "annular_width",
    "hole_clearance",
    "copper_edge_clearance",
}


def balanced_forms(text: str, names: tuple[str, ...]):
    pattern = re.compile(r"(?m)^\s*\((" + "|".join(names) + r")\s")
    for match in pattern.finditer(text):
        depth = 0
        quoted = False
        escaped = False
        for index in range(match.start(), len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield match.group(1), text[match.start() : index + 1]
                    break


def form_net_name(form: str, nets: dict[int, str]) -> str:
    named = re.search(r'\(net\s+"([^"]*)"\)', form)
    if named:
        return named.group(1)
    numbered = re.search(r"\(net\s+(\d+)\)", form)
    return nets.get(int(numbered.group(1)), "") if numbered else ""


def route_length_mm(kind: str, form: str) -> float:
    points = [
        (float(x), float(y))
        for _, x, y in re.findall(
            r"\((start|mid|end)\s+(-?[\d.]+)\s+(-?[\d.]+)\)",
            form,
        )
    ]
    if kind == "segment" and len(points) >= 2:
        return math.dist(points[0], points[-1])
    if kind != "arc" or len(points) < 3:
        return 0.0
    start, mid, end = points[:3]
    ax, ay = start
    bx, by = mid
    cx, cy = end
    denominator = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(denominator) < 1e-12:
        return math.dist(start, mid) + math.dist(mid, end)
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / denominator
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / denominator
    radius = math.dist((ux, uy), start)
    start_angle = math.atan2(ay - uy, ax - ux)
    mid_angle = math.atan2(by - uy, bx - ux)
    end_angle = math.atan2(cy - uy, cx - ux)
    ccw_total = (end_angle - start_angle) % (2 * math.pi)
    ccw_mid = (mid_angle - start_angle) % (2 * math.pi)
    angle = ccw_total if ccw_mid <= ccw_total else (2 * math.pi - ccw_total)
    return radius * angle


def board_audit(board: Path) -> dict[str, object]:
    text = board.read_text(encoding="utf-8")
    nets = {
        int(number): name
        for number, name in re.findall(r'(?m)^\s*\(net\s+(\d+)\s+"([^"]*)"\)', text)
    }
    counts: Counter[str] = Counter()
    layer_nets: dict[str, set[str]] = {}
    routed: dict[str, dict[str, object]] = defaultdict(
        lambda: {"length_mm": 0.0, "segments": 0, "vias": 0, "layers": defaultdict(float)}
    )
    for kind, form in balanced_forms(text, ("segment", "arc")):
        layer_match = re.search(r'\(layer\s+"([^"]+)"\)', form)
        if not layer_match:
            continue
        layer = layer_match.group(1)
        net_name = form_net_name(form, nets)
        counts[layer] += 1
        if net_name:
            layer_nets.setdefault(layer, set()).add(net_name)
            length = route_length_mm(kind, form)
            routed[net_name]["length_mm"] = float(routed[net_name]["length_mm"]) + length
            routed[net_name]["segments"] = int(routed[net_name]["segments"]) + 1
            routed[net_name]["layers"][layer] += length
    for _, form in balanced_forms(text, ("via",)):
        net_name = form_net_name(form, nets)
        if net_name:
            routed[net_name]["vias"] = int(routed[net_name]["vias"]) + 1
    zones: set[tuple[str, str]] = set()
    for _, form in balanced_forms(text, ("zone",)):
        layer_match = re.search(r'\(layer\s+"([^"]+)"\)', form)
        net_name_match = re.search(r'\(net_name\s+"([^"]*)"\)', form)
        if not layer_match:
            continue
        net_name = (
            net_name_match.group(1)
            if net_name_match
            else form_net_name(form, nets)
        )
        zones.add((layer_match.group(1), net_name))
    routed_metrics = [
        {
            "net": name,
            "length_mm": round(float(data["length_mm"]), 4),
            "segments": int(data["segments"]),
            "vias": int(data["vias"]),
            "layers_mm": {
                layer: round(length, 4)
                for layer, length in sorted(data["layers"].items())
            },
        }
        for name, data in routed.items()
    ]
    return {
        "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
        "segment_counts": dict(sorted(counts.items())),
        "in1_non_ground_segments": counts.get("In1.Cu", 0),
        "in1_nets": sorted(layer_nets.get("In1.Cu", set())),
        "zones": [
            {"layer": layer, "net": net}
            for layer, net in sorted(zones)
        ],
        "longest_routed_nets": sorted(
            routed_metrics,
            key=lambda item: float(item["length_mm"]),
            reverse=True,
        )[:20],
        "highest_via_count_nets": sorted(
            routed_metrics,
            key=lambda item: int(item["vias"]),
            reverse=True,
        )[:20],
        "routed_net_metrics": sorted(
            routed_metrics,
            key=lambda item: str(item["net"]),
        ),
    }


def violation_signatures(report: str, category: str) -> list[str]:
    starts = list(re.finditer(r"(?m)^\[([^\]]+)\]:", report))
    signatures: list[str] = []
    for index, match in enumerate(starts):
        if match.group(1) != category:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(report)
        block = report[match.start() : end]
        items = []
        for line in block.splitlines():
            item = re.match(r"\s*@\([^)]*\):\s*(.+?)\s*$", line)
            if item:
                items.append(item.group(1))
        signatures.append(category + "|" + "|".join(items))
    return sorted(signatures)


def violation_items(report: str, category: str) -> list[str]:
    starts = list(re.finditer(r"(?m)^\[([^\]]+)\]:", report))
    items: list[str] = []
    for index, match in enumerate(starts):
        if match.group(1) != category:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(report)
        block = report[match.start() : end]
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped.startswith("@("):
                continue
            if ": Zone [" in stripped:
                stripped = stripped.split(": ", 1)[1]
            items.append(stripped)
    return sorted(items)


def baseline_differences(
    report: str, categories: Counter[str], baseline: dict[str, object] | None,
) -> tuple[list[str], list[str]]:
    """Return unexpected and missing DRC objects for a strict reviewed baseline.

    A baseline is an allow-list, never a blanket permission.  In particular an
    empty scaffold baseline accepts only a clean DRC report.
    """
    allowed = (baseline or {}).get("allowed_violation_items", {})
    expected_counts = (baseline or {}).get("expected_category_counts", {})
    if not isinstance(allowed, dict) or not isinstance(expected_counts, dict):
        raise SystemExit("Malformed DRC baseline: expected object maps")

    unexpected: list[str] = []
    missing: list[str] = []
    for category, actual_count in sorted(categories.items()):
        actual = set(violation_items(report, category))
        if category not in allowed:
            unexpected.append(f"{category}|category not allowlisted; count={actual_count}")
            continue
        expected = allowed[category]
        if not isinstance(expected, list):
            raise SystemExit(f"Malformed DRC baseline items for {category}")
        expected_set = {str(item) for item in expected}
        unexpected.extend(
            f"{category}|{item}" for item in sorted(actual - expected_set)
        )
        missing.extend(
            f"{category}|{item}" for item in sorted(expected_set - actual)
        )
        if not actual and actual_count:
            unexpected.append(f"{category}|unable to identify {actual_count} DRC objects")

    for category, expected in sorted(expected_counts.items()):
        if not isinstance(expected, int):
            raise SystemExit(f"Malformed DRC baseline count for {category}")
        actual = categories.get(category, 0)
        if actual != expected:
            unexpected.append(f"{category}|count expected {expected}, actual {actual}")
    for category in set(allowed) - set(categories):
        for item in allowed[category]:
            missing.append(f"{category}|{item}")
    return unexpected, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--board",
        type=Path,
        default=ROOT / "elec/layout/default/default.kicad_pcb",
    )
    parser.add_argument("--kicad-cli", type=Path, default=DEFAULT_KICAD_CLI)
    parser.add_argument(
        "--drc-report",
        type=Path,
        default=ROOT / "build/reports/final-kicad-cli-drc.rpt",
    )
    parser.add_argument(
        "--audit-json",
        type=Path,
        default=ROOT / "build/reports/final-routing-audit.json",
    )
    parser.add_argument("--require-in1-ground", action="store_true")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Expected accepted signatures and required zones.",
    )
    parser.add_argument(
        "--routing-paths",
        type=Path,
        default=DEFAULT_ROUTING_PATHS,
    )
    args = parser.parse_args()

    args.drc_report.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(args.kicad_cli),
        "pcb",
        "drc",
        "--output",
        str(args.drc_report),
        str(args.board),
    ]
    result = subprocess.run(command, check=False)
    if result.returncode not in {0, 5}:
        raise SystemExit(result.returncode)

    report = args.drc_report.read_text(encoding="utf-8")
    categories = Counter(re.findall(r"(?m)^\[([^\]]+)\]:", report))
    audit = board_audit(args.board)
    route_by_net = {
        item["net"]: item
        for item in audit["routed_net_metrics"]
    }
    aggregate_paths: list[dict[str, object]] = []
    aggregate_pairs: list[dict[str, object]] = []
    if args.routing_paths.is_file():
        routing_paths = json.loads(args.routing_paths.read_text(encoding="utf-8"))
        for definition in routing_paths.get("paths", []):
            members = [
                route_by_net[net]
                for net in definition["nets"]
                if net in route_by_net
            ]
            aggregate_paths.append(
                {
                    "name": definition["name"],
                    "nets": definition["nets"],
                    "aggregate_copper_mm": round(
                        sum(float(item["length_mm"]) for item in members),
                        4,
                    ),
                    "vias": sum(int(item["vias"]) for item in members),
                    "missing_nets": [
                        net for net in definition["nets"] if net not in route_by_net
                    ],
                    "note": definition.get("note", ""),
                }
            )
        aggregate_by_name = {item["name"]: item for item in aggregate_paths}
        for pair in routing_paths.get("pairs", []):
            positive = aggregate_by_name.get(pair["positive"])
            negative = aggregate_by_name.get(pair["negative"])
            aggregate_pairs.append(
                {
                    "name": pair["name"],
                    "positive": pair["positive"],
                    "negative": pair["negative"],
                    "aggregate_delta_mm": (
                        round(
                            abs(
                                float(positive["aggregate_copper_mm"])
                                - float(negative["aggregate_copper_mm"])
                            ),
                            4,
                        )
                        if positive and negative
                        else None
                    ),
                    "note": pair.get("note", ""),
                }
            )
    audit["aggregate_routing_paths"] = aggregate_paths
    audit["aggregate_routing_pairs"] = aggregate_pairs
    audit["drc_categories"] = dict(categories.most_common())
    audit["unconnected_signatures"] = violation_signatures(report, "unconnected_items")
    audit["drc_report"] = str(args.drc_report.resolve())

    unexpected_violations: list[str] = []
    missing_baseline_violations: list[str] = []
    missing_required_zones: list[dict[str, str]] = []
    if args.baseline.is_file():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        unexpected_violations, missing_baseline_violations = baseline_differences(
            report, categories, baseline,
        )
        actual_zones = {
            (item["layer"], item["net"])
            for item in audit["zones"]
        }
        for required in baseline.get("required_zones", []):
            pair = (required["layer"], required["net"])
            if pair not in actual_zones:
                missing_required_zones.append(required)
    else:
        unexpected_violations, missing_baseline_violations = baseline_differences(
            report, categories, None,
        )
    audit["baseline"] = {
        "path": str(args.baseline.resolve()),
        "unexpected_violations": unexpected_violations,
        "missing_accepted_violations": missing_baseline_violations,
        "missing_required_zones": missing_required_zones,
    }
    audit["baseline_pass"] = not (
        unexpected_violations or missing_baseline_violations or missing_required_zones
    )
    audit["release_pass"] = audit["baseline_pass"] and not FATAL_CATEGORIES.intersection(categories)
    args.audit_json.parent.mkdir(parents=True, exist_ok=True)
    args.audit_json.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "board_sha256": audit["board_sha256"],
                "segment_counts": audit["segment_counts"],
                "in1_non_ground_segments": audit["in1_non_ground_segments"],
                "zones": audit["zones"],
                "drc_categories": audit["drc_categories"],
                "baseline_pass": audit["baseline_pass"],
                "release_pass": audit["release_pass"],
                "longest_routed_nets": [
                    {
                        "net": item["net"],
                        "length_mm": item["length_mm"],
                        "vias": item["vias"],
                    }
                    for item in audit["longest_routed_nets"][:5]
                ],
                "aggregate_routing_paths": aggregate_paths,
                "aggregate_routing_pairs": aggregate_pairs,
                "audit_json": str(args.audit_json.resolve()),
                "drc_report": str(args.drc_report.resolve()),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

    if FATAL_CATEGORIES.intersection(categories):
        return 2
    if unexpected_violations or missing_baseline_violations or missing_required_zones:
        return 4
    if args.require_in1_ground and audit["in1_non_ground_segments"] != 0:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
