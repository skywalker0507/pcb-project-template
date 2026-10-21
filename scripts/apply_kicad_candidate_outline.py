#!/usr/bin/env python3
"""Replace the rectangular outline of an isolated, SHA-guarded KiCad candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from stage_policy import current_stage


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
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--width", type=float, required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if current_stage().get("stage") != "physical":
        raise SystemExit("Candidate outline edits are allowed only during the physical stage")
    source = args.input.resolve()
    output = args.output.resolve()
    if not source.is_file() or sha256(source) != args.input_sha256:
        raise SystemExit("Candidate outline input is missing or does not match the declared SHA-256")
    if output == source or args.width <= 0 or args.height <= 0:
        raise SystemExit("Candidate outline requires a distinct output and positive dimensions")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    for suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
        companion = source.with_suffix(suffix)
        if companion.is_file():
            shutil.copy2(companion, output.with_suffix(suffix))
    ensure_kicad_python()
    import pcbnew

    board = pcbnew.LoadBoard(str(output))
    edges = [item for item in board.GetDrawings() if item.GetLayer() == pcbnew.Edge_Cuts]
    if len(edges) != 4 or any(item.GetShape() != pcbnew.SHAPE_T_SEGMENT for item in edges):
        raise SystemExit("Candidate outline replacement requires exactly four existing straight Edge.Cuts")
    for item in edges:
        board.Remove(item)
    x1, y1, x2, y2 = args.x, args.y, args.x + args.width, args.y + args.height
    points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    for start, end in zip(points, points[1:] + points[:1]):
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
        shape.SetLayer(pcbnew.Edge_Cuts)
        shape.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(start[0]), pcbnew.FromMM(start[1])))
        shape.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(end[0]), pcbnew.FromMM(end[1])))
        board.Add(shape)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(output), board)
    run = create_run("physical", "kicad-candidate-outline", args.run_id)
    receipt = {
        "schema": 1, "kind": "kicad_candidate_outline", "success": True,
        "input": relative(source), "input_sha256": args.input_sha256,
        "candidate": relative(output), "candidate_sha256": sha256(output),
        "bounds_mm": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "dimensions_mm": {"width": args.width, "height": args.height},
        "limits": "Outline-only isolated candidate; placement, DRC and visual review remain mandatory.",
    }
    evidence = run.evidence / "candidate-outline.json"
    evidence.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={"receipt": relative(evidence)}, metrics={"width_mm": args.width, "height_mm": args.height})
    print(json.dumps({**receipt, "receipt": relative(evidence)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
