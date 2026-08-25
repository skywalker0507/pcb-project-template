#!/usr/bin/env python3
"""Apply a reviewed rectangular outline to the active KiCad physical candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def configured_board() -> Path:
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    return (ROOT / str(flow["project"]["board"])).resolve()


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def mm(value: float) -> int:
    import pcbnew
    return pcbnew.FromMM(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path)
    parser.add_argument("--x", type=float, required=True, help="Left edge in mm")
    parser.add_argument("--y", type=float, required=True, help="Top edge in mm")
    parser.add_argument("--width", type=float, required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if current_stage().get("stage") != "physical":
        raise SystemExit("Board-outline edits are allowed only during the physical stage")
    board_path = (args.board or configured_board()).resolve()
    if board_path != configured_board():
        raise SystemExit("Board-outline edits must target the configured active physical candidate")
    try:
        board_path.relative_to((ROOT / "elec/layout").resolve())
    except ValueError as exc:
        raise SystemExit("Board-outline edits must stay below elec/layout") from exc
    if args.width <= 0 or args.height <= 0:
        raise SystemExit("Board width and height must be positive reviewed values")

    ensure_kicad_python()
    import pcbnew

    board = pcbnew.LoadBoard(str(board_path))
    existing = [item for item in board.GetDrawings() if item.GetLayer() == pcbnew.Edge_Cuts]
    if existing:
        raise SystemExit("Refusing to replace an existing board outline; review it explicitly")
    run = create_run("physical", "kicad-board-outline", args.run_id)
    before = sha256(board_path)
    x1, y1 = args.x, args.y
    x2, y2 = args.x + args.width, args.y + args.height
    points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    for start, end in zip(points, points[1:] + points[:1]):
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
        shape.SetLayer(pcbnew.Edge_Cuts)
        shape.SetStart(pcbnew.VECTOR2I(mm(start[0]), mm(start[1])))
        shape.SetEnd(pcbnew.VECTOR2I(mm(end[0]), mm(end[1])))
        board.Add(shape)
    pcbnew.SaveBoard(str(board_path), board)
    receipt = {
        "schema": 1,
        "kind": "kicad_board_outline",
        "success": True,
        "board": relative(board_path),
        "before_sha256": before,
        "board_sha256": sha256(board_path),
        "bounds_mm": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "dimensions_mm": {"width": args.width, "height": args.height},
        "edge_primitives": 4,
        "limits": "This proves the reviewed rectangular Edge.Cuts edit only; placement, DRC, and release remain pending.",
    }
    evidence = run.evidence / "kicad-board-outline.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={"receipt": relative(evidence)}, metrics={"edge_primitives": 4})
    print(json.dumps({**receipt, "receipt": relative(evidence)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
