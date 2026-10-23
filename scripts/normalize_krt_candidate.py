#!/usr/bin/env python3
"""Normalize reviewed KRT copper values and refill zones on one candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


KICAD_PYTHON = kicad_python()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    ensure_host_kicad_python()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize explicitly routed nets, refill zones, and save an isolated KRT candidate."
    )
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--nets", nargs="+", required=True)
    parser.add_argument("--track-width", type=float, required=True)
    parser.add_argument("--via-size", type=float, required=True)
    parser.add_argument("--via-drill", type=float, required=True)
    args = parser.parse_args()

    ensure_kicad_python()
    import pcbnew

    board_path = args.board.resolve()
    before = sha256(board_path)
    if before != args.expected_sha256.upper():
        raise SystemExit("Candidate SHA-256 changed before normalization")

    selected = set(args.nets)
    board = pcbnew.LoadBoard(str(board_path))
    track_count = 0
    via_count = 0
    for item in board.GetTracks():
        if item.GetNetname() not in selected:
            continue
        if isinstance(item, pcbnew.PCB_VIA):
            item.SetWidth(pcbnew.FromMM(args.via_size))
            item.SetDrill(pcbnew.FromMM(args.via_drill))
            via_count += 1
        else:
            item.SetWidth(pcbnew.FromMM(args.track_width))
            track_count += 1

    zone_count = len(board.Zones())
    if zone_count:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(board_path), board)

    print(json.dumps({
        "success": True,
        "board_sha256_before": before,
        "board_sha256_after": sha256(board_path),
        "selected_net_count": len(selected),
        "normalized_track_count": track_count,
        "normalized_via_count": via_count,
        "refilled_zone_count": zone_count,
        "track_width_mm": args.track_width,
        "via_size_mm": args.via_size,
        "via_drill_mm": args.via_drill,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
