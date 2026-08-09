#!/usr/bin/env python3
"""Widen only net tracks added between an input board and a routed candidate."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")

try:
    import pcbnew
except ImportError:
    raise SystemExit(
        subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Board before routing")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--net", required=True)
    parser.add_argument("--width", type=float, required=True)
    args = parser.parse_args()
    source = args.input.resolve()
    candidate_path = args.candidate.resolve()
    output = args.output.resolve()
    if not source.is_file() or not candidate_path.is_file() or output.exists():
        raise SystemExit("Input/candidate must exist and output must be new")

    before = pcbnew.LoadBoard(str(source))
    before_uuids = {item.m_Uuid.AsString() for item in before.GetTracks()}
    board = pcbnew.LoadBoard(str(candidate_path))
    changed = []
    for item in board.GetTracks():
        if not isinstance(item, pcbnew.PCB_TRACK) or isinstance(item, pcbnew.PCB_VIA):
            continue
        if item.GetNetname() != args.net or item.m_Uuid.AsString() in before_uuids:
            continue
        item.SetWidth(pcbnew.FromMM(args.width))
        changed.append(item.m_Uuid.AsString())
    if not changed:
        raise SystemExit("No added matching tracks found")

    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    output.parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(output), board)
    project = candidate_path.with_suffix(".kicad_pro")
    if project.is_file():
        shutil.copy2(project, output.with_suffix(".kicad_pro"))
    print(
        json.dumps(
            {
                "success": True,
                "net": args.net,
                "width_mm": args.width,
                "changed_track_count": len(changed),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
