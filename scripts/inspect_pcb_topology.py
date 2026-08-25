#!/usr/bin/env python3
"""Emit compact topology counts for a KiCad PCB without exposing board content."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    args = parser.parse_args()
    board_path = args.board.resolve()
    if not board_path.is_file():
        raise SystemExit(f"Board is unavailable: {board_path}")

    pcbnew = load_pcbnew()
    board = pcbnew.LoadBoard(str(board_path))
    copper = list(board.GetTracks())
    vias = [item for item in copper if item.__class__.__name__ == "PCB_VIA"]
    tracks = [item for item in copper if item.__class__.__name__ != "PCB_VIA"]
    edge_primitives = [
        item for item in board.GetDrawings() if item.GetLayer() == pcbnew.Edge_Cuts
    ]
    zones = list(board.Zones())
    payload = {
        "schema": 1,
        "kind": "pcb_topology_summary",
        "board": str(board_path),
        "board_sha256": hashlib.sha256(board_path.read_bytes()).hexdigest().upper(),
        "footprints": len(list(board.GetFootprints())),
        "tracks": len(tracks),
        "vias": len(vias),
        "zones": len(zones),
        "edge_primitives": len(edge_primitives),
        "pre_layout": not tracks and not vias and not zones and not edge_primitives,
        "limit": "Counts only; this is not DRC, connectivity, or release evidence.",
    }
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
