#!/usr/bin/env python3
"""Emit bounded per-net track/via metrics without exposing a complete PCB."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re


def sexpr_blocks(text: str, head: str):
    marker = f"({head}"
    start = 0
    while True:
        index = text.find(marker, start)
        if index < 0:
            return
        boundary = index + len(marker)
        if boundary < len(text) and not text[boundary].isspace():
            start = boundary
            continue
        depth = 0
        quoted = False
        escaped = False
        for end in range(index, len(text)):
            char = text[end]
            if escaped:
                escaped = False
            elif char == "\\" and quoted:
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        yield text[index:end + 1]
                        start = end + 1
                        break
        else:
            return


def field(block: str, name: str) -> str:
    match = re.search(rf'\({re.escape(name)}\s+([^()]+?)\)', block)
    return " ".join(match.group(1).split()) if match else ""


def point(value: str) -> tuple[float, float]:
    values = value.replace('"', "").split()
    if len(values) < 2:
        raise ValueError(f"Expected an x/y point, got: {value}")
    return float(values[0]), float(values[1])


def net_map(text: str) -> dict[str, str]:
    return {
        code: bytes(name, "utf-8").decode("unicode_escape")
        for code, name in re.findall(r'\(net\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\)', text)
    }


def net_name(value: str, names: dict[str, str]) -> str:
    if value.startswith('"') and value.endswith('"'):
        return bytes(value[1:-1], "utf-8").decode("unicode_escape")
    return names.get(value, value)


def arc_length_mm(start, middle, end) -> float:
    ax, ay = start; bx, by = middle; cx, cy = end
    denominator = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(denominator) < 1e-12:
        return math.dist(start, middle) + math.dist(middle, end)
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
    angles = [math.atan2(y - uy, x - ux) % (2 * math.pi) for x, y in (start, middle, end)]
    ccw_end = (angles[2] - angles[0]) % (2 * math.pi)
    ccw_mid = (angles[1] - angles[0]) % (2 * math.pi)
    sweep = ccw_end if ccw_mid <= ccw_end else 2 * math.pi - ccw_end
    return math.hypot(ax - ux, ay - uy) * sweep


def merge_bounds(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return (
        min(left[0], right[0]), min(left[1], right[1]),
        max(left[2], right[2]), max(left[3], right[3]),
    )


def bbox_excursion(bounds, reference):
    """Return non-negative copper excursion beyond a reference rectangle."""
    if bounds is None or reference is None:
        return None
    left = max(0.0, reference[0] - bounds[0])
    top = max(0.0, reference[1] - bounds[1])
    right = max(0.0, bounds[2] - reference[2])
    bottom = max(0.0, bounds[3] - reference[3])
    maximum = max(left, top, right, bottom)
    return {
        "outside": maximum > 0.001,
        "max_mm": round(maximum, 3),
        "by_side_mm": {
            "left": round(left, 3), "top": round(top, 3),
            "right": round(right, 3), "bottom": round(bottom, 3),
        },
    }


def collect(path: Path, selected: set[str], max_via_positions: int):
    text = path.read_text(encoding="utf-8", errors="replace")
    names = net_map(text)
    segments: dict[str, Counter[str]] = defaultdict(Counter)
    lengths: dict[str, Counter[str]] = defaultdict(Counter)
    via_positions: dict[str, list[list[float]]] = defaultdict(list)
    via_counts: Counter[str] = Counter()
    bounds_by_net = {}
    items = {}
    for head in ("segment", "arc"):
        for block in sexpr_blocks(text, head):
            net = net_name(field(block, "net"), names)
            if net not in selected:
                continue
            layer = field(block, "layer").strip('"')
            width = float(field(block, "width"))
            start = point(field(block, "start"))
            end = point(field(block, "end"))
            points = [start, end]
            if head == "arc":
                middle = point(field(block, "mid"))
                points.append(middle)
                length_mm = arc_length_mm(start, middle, end)
            else:
                middle = None
                length_mm = math.dist(start, end)
            radius = width / 2
            bounds = (
                min(value[0] for value in points) - radius,
                min(value[1] for value in points) - radius,
                max(value[0] for value in points) + radius,
                max(value[1] for value in points) + radius,
            )
            bounds_by_net[net] = merge_bounds(bounds_by_net.get(net), bounds)
            ordered = (start, end) if start <= end else (end, start)
            signature = (head, net, layer, *ordered, middle, width)
            segments[net][layer] += 1
            lengths[net][layer] += length_mm
            items[signature] = {"net": net, "bounds": bounds, "length_mm": length_mm}
    for block in sexpr_blocks(text, "via"):
        net = net_name(field(block, "net"), names)
        if net not in selected:
            continue
        position = point(field(block, "at"))
        size = float(field(block, "size"))
        drill = float(field(block, "drill"))
        radius = size / 2
        bounds = (
            position[0] - radius, position[1] - radius,
            position[0] + radius, position[1] + radius,
        )
        bounds_by_net[net] = merge_bounds(bounds_by_net.get(net), bounds)
        signature = ("via", net, position, size, drill, field(block, "layers"))
        via_counts[net] += 1
        if len(via_positions[net]) < max_via_positions:
            via_positions[net].append([round(position[0], 3), round(position[1], 3)])
        items[signature] = {"net": net, "bounds": bounds, "length_mm": 0.0}
    return {
        "segments": segments,
        "lengths": lengths,
        "via_positions": via_positions,
        "via_counts": via_counts,
        "bounds_by_net": bounds_by_net,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--nets", nargs="+", required=True)
    parser.add_argument("--max-via-positions", type=int, default=6)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--reference-bbox-mm", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.reference_bbox_mm and (
        args.reference_bbox_mm[0] >= args.reference_bbox_mm[2]
        or args.reference_bbox_mm[1] >= args.reference_bbox_mm[3]
    ):
        raise SystemExit("--reference-bbox-mm requires ordered X0 Y0 X1 Y1 coordinates")

    requested = list(dict.fromkeys(args.nets))
    selected = set(requested)
    board_path = args.board.resolve()
    if not board_path.is_file():
        raise SystemExit(f"Routing geometry board not found: {board_path}")
    geometry = collect(board_path, selected, max(0, args.max_via_positions))

    results = []
    for net in requested:
        results.append({
            "net": net,
            "segment_count_by_layer": dict(sorted(geometry["segments"][net].items())),
            "track_length_mm_by_layer": {
                layer: round(value, 3) for layer, value in sorted(geometry["lengths"][net].items())
            },
            "track_length_mm": round(sum(geometry["lengths"][net].values()), 3),
            "via_count": geometry["via_counts"][net],
            "via_positions_preview_mm": geometry["via_positions"][net],
            "via_positions_truncated": geometry["via_counts"][net] > len(geometry["via_positions"][net]),
            "copper_bbox_mm": [round(value, 3) for value in geometry["bounds_by_net"][net]]
            if net in geometry["bounds_by_net"] else None,
        })
    change = None
    warnings = []
    if args.baseline:
        baseline_path = args.baseline.resolve()
        if not baseline_path.is_file():
            raise SystemExit(f"Routing geometry baseline not found: {baseline_path}")
        baseline = collect(baseline_path, selected, 0)
        added_keys = set(geometry["items"]) - set(baseline["items"])
        removed_keys = set(baseline["items"]) - set(geometry["items"])
        added_bounds = None
        added_by_net: Counter[str] = Counter()
        removed_by_net: Counter[str] = Counter()
        added_length_by_net: Counter[str] = Counter()
        for key in added_keys:
            value = geometry["items"][key]
            added_bounds = merge_bounds(added_bounds, value["bounds"])
            added_by_net[value["net"]] += 1
            added_length_by_net[value["net"]] += value["length_mm"]
        for key in removed_keys:
            removed_by_net[baseline["items"][key]["net"]] += 1
        reference = tuple(args.reference_bbox_mm) if args.reference_bbox_mm else None
        excursion = bbox_excursion(added_bounds, reference)
        change = {
            "added_item_count": len(added_keys),
            "removed_item_count": len(removed_keys),
            "added_items_by_net": dict(sorted(added_by_net.items())),
            "removed_items_by_net": dict(sorted(removed_by_net.items())),
            "added_track_length_mm_by_net": {
                net: round(value, 3) for net, value in sorted(added_length_by_net.items())
            },
            "added_copper_bbox_mm": [round(value, 3) for value in added_bounds]
            if added_bounds else None,
            "reference_bbox_mm": list(reference) if reference else None,
            "reference_bbox_excursion": excursion,
        }
        if excursion and excursion["outside"]:
            warnings.append({
                "type": "route_bbox_excursion",
                "severity": "soft",
                "max_mm": excursion["max_mm"],
                "message": "Selected-net copper extends beyond the reviewed route reference box; inspect the automatic changed-copper crop.",
            })
    receipt = {
        "success": True,
        "nets": results,
        "change": change,
        "quality_warnings": warnings,
        "limits": "Route bbox excursion is a soft quality signal; DRC, connectivity and reviewed policy remain authoritative.",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        receipt["output"] = str(args.output.resolve())
    print(json.dumps(receipt, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
