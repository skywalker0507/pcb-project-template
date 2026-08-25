#!/usr/bin/env python3
"""Emit a bounded nearest-GND-via summary for selected signal vias."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys


KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--signal-nets", nargs="+", required=True)
    parser.add_argument("--ground-net", default="GND")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-signal-vias", type=int, default=32)
    args = parser.parse_args()
    board_path = args.board.resolve()
    ensure_kicad_python()
    import pcbnew

    board = pcbnew.LoadBoard(str(board_path))
    signal_names = set(args.signal_nets)
    signal_vias = []
    ground_vias = []
    for item in board.GetTracks():
        if not isinstance(item, pcbnew.PCB_VIA):
            continue
        pos = item.GetPosition()
        entry = (str(item.GetNetname()), pos.x / 1_000_000, pos.y / 1_000_000)
        if entry[0] in signal_names:
            signal_vias.append(entry)
        elif entry[0] == args.ground_net:
            ground_vias.append(entry)
    if len(signal_vias) > args.max_signal_vias:
        raise SystemExit("Selected signal-via count exceeds the bounded summary limit")
    if signal_vias and not ground_vias:
        raise SystemExit("Signal vias exist but the board has no GND return vias")
    rows = []
    for net, x, y in sorted(signal_vias):
        _, gx, gy = min(ground_vias, key=lambda item: math.hypot(x - item[1], y - item[2]))
        rows.append({
            "net": net, "signal_via_mm": [round(x, 6), round(y, 6)],
            "nearest_ground_via_mm": [round(gx, 6), round(gy, 6)],
            "center_distance_mm": round(math.hypot(x - gx, y - gy), 6),
        })
    receipt = {
        "schema": 1, "kind": "kicad_return_via_summary", "success": True,
        "board": str(board_path), "board_sha256": sha256(board_path),
        "signal_nets": sorted(signal_names), "ground_net": args.ground_net,
        "signal_via_count": len(signal_vias), "ground_via_count": len(ground_vias),
        "nearest_return_vias": rows,
        "limits": "Distance summary only; the reviewed physical return-path decision remains a separate gate.",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**receipt, "output": str(output)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
