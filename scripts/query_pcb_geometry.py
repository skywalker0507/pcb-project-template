#!/usr/bin/env python3
"""Emit a bounded, read-only geometry slice from a KiCad PCB."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


KICAD_PYTHON = kicad_python()


def load_pcbnew():
    try:
        import pcbnew  # type: ignore
    except ImportError:
        ensure_host_kicad_python()
        import pcbnew
    return pcbnew


def mm(value: int) -> float:
    return round(value / 1_000_000, 6)


def intersects(
    start: tuple[float, float],
    end: tuple[float, float],
    bbox: tuple[float, float, float, float] | None,
) -> bool:
    if bbox is None:
        return True
    x1, y1, x2, y2 = bbox
    return not (
        max(start[0], end[0]) < x1
        or min(start[0], end[0]) > x2
        or max(start[1], end[1]) < y1
        or min(start[1], end[1]) > y2
    )


def contains(point: tuple[float, float], bbox: tuple[float, float, float, float] | None) -> bool:
    if bbox is None:
        return True
    x1, y1, x2, y2 = bbox
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def corridor_bbox(
    values: list[float] | None, width_mm: float, clearance_mm: float,
) -> tuple[float, float, float, float] | None:
    if values is None:
        return None
    x1, y1, x2, y2 = values
    margin = width_mm / 2 + clearance_mm
    return (
        min(x1, x2) - margin,
        min(y1, y2) - margin,
        max(x1, x2) + margin,
        max(y1, y2) + margin,
    )


def footprint_identity(footprint: object) -> dict[str, str]:
    fields = {
        str(field.GetName()): str(field.GetText())
        for field in footprint.GetFields()
        if str(field.GetText()).strip()
    }
    allowed = {
        "manufacturer", "manufacturer part number", "mpn", "partnumber",
        "lcsc", "lcsc part", "lcsc part number", "lcsc_id", "atopile_address",
    }
    return {key: value for key, value in fields.items() if key.casefold() in allowed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--net", action="append", default=[])
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--bbox", type=float, nargs=4, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--corridor", type=float, nargs=4, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--corridor-width-mm", type=float, default=0.2)
    parser.add_argument("--clearance-mm", type=float, default=0.2)
    parser.add_argument("--pads-only", action="store_true")
    parser.add_argument("--max-items", type=int, default=300)
    parser.add_argument("--max-components", type=int, default=40)
    parser.add_argument("--max-pads-per-component", type=int, default=80)
    parser.add_argument("--max-output-bytes", type=int, default=32_000)
    args = parser.parse_args()
    if not args.net and not args.ref and args.bbox is None and args.corridor is None:
        raise SystemExit("Select at least one --net, --ref, or --bbox; full-board output is forbidden")
    if args.bbox is not None and args.corridor is not None:
        raise SystemExit("Use either --bbox or --corridor, not both")
    if args.corridor_width_mm <= 0 or args.clearance_mm < 0:
        raise SystemExit("Corridor width must be positive and clearance must be non-negative")
    if (
        args.max_items < 1 or args.max_components < 1
        or args.max_pads_per_component < 1 or args.max_output_bytes < 1024
    ):
        raise SystemExit("Output limits must be positive and --max-output-bytes must be at least 1024")

    pcbnew = load_pcbnew()
    board_path = args.board.resolve()
    if not board_path.is_file():
        raise SystemExit(f"Board not found: {board_path}")
    board = pcbnew.LoadBoard(str(board_path))
    requested_nets = set(args.net)
    requested_refs = set(args.ref)
    bbox = (
        tuple(args.bbox) if args.bbox is not None
        else corridor_bbox(args.corridor, args.corridor_width_mm, args.clearance_mm)
    )

    components: list[dict[str, object]] = []
    for footprint in board.GetFootprints():
        if requested_refs and footprint.GetReference() not in requested_refs:
            continue
        position = footprint.GetPosition()
        pads = []
        for pad in footprint.Pads():
            pad_position = pad.GetPosition()
            size = pad.GetSize()
            pads.append({
                "number": pad.GetNumber(),
                "net": pad.GetNetname(),
                "x_mm": mm(pad_position.x),
                "y_mm": mm(pad_position.y),
                "size_x_mm": mm(size.x),
                "size_y_mm": mm(size.y),
            })
        footprint_point = (mm(position.x), mm(position.y))
        has_requested_net = any(pad["net"] in requested_nets for pad in pads)
        in_bbox = (
            bbox is None
            or contains(footprint_point, bbox)
            or any(contains((pad["x_mm"], pad["y_mm"]), bbox) for pad in pads)
        )
        if requested_nets and not has_requested_net:
            continue
        if not in_bbox:
            continue
        selected_pads = pads
        if requested_nets:
            selected_pads = [pad for pad in pads if pad["net"] in requested_nets]
        elif bbox is not None and not requested_refs:
            selected_pads = [
                pad for pad in pads
                if contains((pad["x_mm"], pad["y_mm"]), bbox)
            ]
        components.append({
            "ref": footprint.GetReference(),
            "value": footprint.GetValue(),
            "identity": footprint_identity(footprint),
            "x_mm": mm(position.x),
            "y_mm": mm(position.y),
            "rotation_deg": footprint.GetOrientationDegrees(),
            "pads": selected_pads[: args.max_pads_per_component],
            "pad_matches": len(selected_pads),
            "pads_truncated": len(selected_pads) > args.max_pads_per_component,
        })

    copper: list[dict[str, object]] = []
    for item in [] if args.pads_only else board.GetTracks():
        net = item.GetNetname()
        if requested_nets and net not in requested_nets:
            continue
        # A component-only query is intentionally component-only. Returning
        # every board track was both misleading and a major token leak.
        if requested_refs and not requested_nets and bbox is None:
            continue
        if isinstance(item, pcbnew.PCB_VIA):
            position = item.GetPosition()
            start = end = (mm(position.x), mm(position.y))
            selected = intersects(start, end, bbox)
            if selected:
                copper.append({
                    "kind": "via",
                    "net": net,
                    "x_mm": start[0],
                    "y_mm": start[1],
                    "diameter_mm": mm(item.GetWidth(pcbnew.F_Cu)),
                    "drill_mm": mm(item.GetDrillValue()),
                    "via_type_code": int(item.GetViaType()),
                })
            continue
        start_value = item.GetStart()
        end_value = item.GetEnd()
        start = (mm(start_value.x), mm(start_value.y))
        end = (mm(end_value.x), mm(end_value.y))
        selected = intersects(start, end, bbox)
        if selected:
            copper.append({
                "kind": "segment",
                "net": net,
                "layer": board.GetLayerName(item.GetLayer()),
                "width_mm": mm(item.GetWidth()),
                "start_mm": list(start),
                "end_mm": list(end),
            })

    components_truncated = len(components) > args.max_components
    copper_truncated = len(copper) > args.max_items
    result = {
        "schema": 1,
        "board": str(board_path),
        "board_sha256": hashlib.sha256(board_path.read_bytes()).hexdigest().upper(),
        "selectors": {
            "nets": sorted(requested_nets), "refs": sorted(requested_refs),
            "bbox_mm": bbox, "corridor_mm": args.corridor,
            "corridor_width_mm": args.corridor_width_mm if args.corridor else None,
            "clearance_mm": args.clearance_mm if args.corridor else None,
            "pads_only": args.pads_only,
        },
        "components": components[: args.max_components],
        "component_total_matches": len(components),
        "components_truncated": components_truncated,
        "copper": copper[: args.max_items],
        "copper_total_matches": len(copper),
        "copper_truncated": copper_truncated,
        "truncated": components_truncated or copper_truncated,
        "limits": [
            "This is a local geometry query, not DRC, connectivity, return-path, or release evidence.",
            "Numeric acceptance values still come from reviewed project and manufacturer records.",
        ],
    }
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > args.max_output_bytes:
        raise SystemExit("Geometry slice exceeds --max-output-bytes; narrow the selectors or bbox")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
