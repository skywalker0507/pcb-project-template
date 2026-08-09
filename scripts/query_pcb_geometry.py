#!/usr/bin/env python3
"""Emit a bounded, read-only geometry slice from a KiCad PCB."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")


def load_pcbnew():
    try:
        import pcbnew  # type: ignore
    except ImportError:
        raise SystemExit(
            subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        )
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--net", action="append", default=[])
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--bbox", type=float, nargs=4, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--max-items", type=int, default=300)
    parser.add_argument("--max-output-bytes", type=int, default=32_000)
    args = parser.parse_args()
    if not args.net and not args.ref and args.bbox is None:
        raise SystemExit("Select at least one --net, --ref, or --bbox; full-board output is forbidden")
    if args.max_items < 1 or args.max_output_bytes < 1024:
        raise SystemExit("Output limits must be positive and --max-output-bytes must be at least 1024")

    pcbnew = load_pcbnew()
    board_path = args.board.resolve()
    if not board_path.is_file():
        raise SystemExit(f"Board not found: {board_path}")
    board = pcbnew.LoadBoard(str(board_path))
    requested_nets = set(args.net)
    requested_refs = set(args.ref)
    bbox = tuple(args.bbox) if args.bbox is not None else None

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
        if not requested_refs and bbox is not None and not (
            contains(footprint_point, bbox)
            or any(contains((pad["x_mm"], pad["y_mm"]), bbox) for pad in pads)
        ):
            continue
        components.append({
            "ref": footprint.GetReference(),
            "x_mm": mm(position.x),
            "y_mm": mm(position.y),
            "rotation_deg": footprint.GetOrientationDegrees(),
            "pads": pads,
        })

    copper: list[dict[str, object]] = []
    for item in board.GetTracks():
        net = item.GetNetname()
        if requested_nets and net not in requested_nets:
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

    truncated = len(copper) > args.max_items
    result = {
        "schema": 1,
        "board": str(board_path),
        "board_sha256": hashlib.sha256(board_path.read_bytes()).hexdigest().upper(),
        "selectors": {"nets": sorted(requested_nets), "refs": sorted(requested_refs), "bbox_mm": bbox},
        "components": components,
        "copper": copper[: args.max_items],
        "copper_total_matches": len(copper),
        "truncated": truncated,
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
