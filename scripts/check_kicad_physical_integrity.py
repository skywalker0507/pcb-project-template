#!/usr/bin/env python3
"""Check physical-origin semantics before placement can hide a generation loss."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
DEFAULT_CONFIG = ROOT / "config/physical-integrity.json"
DEFAULT_STACK = ROOT / "config/board-stack.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def active_stack_expectation(stack: dict[str, Any]) -> tuple[list[str], list[str]]:
    active = stack.get("active")
    profile = stack.get("profiles", {}).get(active)
    if not isinstance(profile, dict):
        raise SystemExit(f"Invalid active board stack: {active!r}")
    copper = profile.get("copper_layers")
    forbidden = profile.get("forbidden_signal_layers", [])
    if not isinstance(copper, list) or not copper or not isinstance(forbidden, list):
        raise SystemExit("Active board stack must define copper_layers and forbidden_signal_layers")
    return [str(item) for item in copper], [str(item) for item in forbidden]


def board_structure(board_path: Path, copper_layers: list[str], forbidden_layers: list[str]) -> dict[str, Any]:
    import pcbnew

    board = pcbnew.LoadBoard(str(board_path))
    actual_copper = int(board.GetCopperLayerCount())
    edge_items = [item for item in board.GetDrawings() if board.GetLayerName(item.GetLayer()) == "Edge.Cuts"]
    outlines = pcbnew.SHAPE_POLY_SET()
    outline_closed = bool(board.GetBoardPolygonOutlines(outlines, False) and outlines.OutlineCount() > 0)
    forbidden_tracks: dict[str, int] = {}
    for item in board.GetTracks():
        layer = board.GetLayerName(item.GetLayer())
        if layer in forbidden_layers:
            forbidden_tracks[layer] = forbidden_tracks.get(layer, 0) + 1
    return {
        "expected_copper_layer_count": len(copper_layers),
        "actual_copper_layer_count": actual_copper,
        "stack_pass": actual_copper == len(copper_layers) and not forbidden_tracks,
        "forbidden_signal_items": forbidden_tracks,
        "edge_cut_item_count": len(edge_items),
        "closed_outline_count": int(outlines.OutlineCount()),
        "outline_pass": outline_closed,
    }


def footprint_equivalence(board: Path, refs: list[str], output: Path) -> dict[str, Any]:
    command = [
        str(KICAD_PYTHON), str(ROOT / "scripts/check_kicad_footprint_equivalence.py"),
        "--board", str(board), "--output", str(output),
    ]
    for ref in refs:
        command.extend(["--ref", ref])
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if output.is_file():
        return load(output, "Footprint equivalence receipt")
    raise SystemExit("Footprint equivalence did not produce a receipt: " + (result.stdout + result.stderr)[-800:])


def main() -> int:
    ensure_kicad_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--phase", choices=("origin", "placement", "release"), required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--board-stack", type=Path, default=DEFAULT_STACK)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--footprint-output", type=Path)
    args = parser.parse_args()
    board = args.board.resolve()
    output = args.output.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    config = load(args.config.resolve(), "Physical integrity config")
    phase = config.get("phases", {}).get(args.phase)
    provenance_value = config.get("critical_footprints_from")
    if config.get("schema") != 1 or not isinstance(phase, dict) or not isinstance(provenance_value, str):
        raise SystemExit("Physical integrity config must define schema 1 phases and critical_footprints_from")
    provenance_path = Path(provenance_value)
    if not provenance_path.is_absolute():
        provenance_path = ROOT / provenance_path
    provenance = load(provenance_path.resolve(), "Footprint provenance")
    parts = provenance.get("parts")
    if not isinstance(parts, dict):
        raise SystemExit("Footprint provenance must define a parts object")
    refs = sorted(
        str(ref) for ref, item in parts.items()
        if isinstance(item, dict) and item.get("physical_integrity_gate") is True
    )
    if not refs:
        raise SystemExit("At least one reviewed footprint must opt into physical_integrity_gate")
    footprint_output = (args.footprint_output or output.with_name("footprint-equivalence.json")).resolve()
    footprint = footprint_equivalence(board, [str(ref) for ref in refs], footprint_output)
    copper_layers, forbidden_layers = active_stack_expectation(load(args.board_stack.resolve(), "Board stack"))
    structure = board_structure(board, copper_layers, forbidden_layers)
    checks = {
        "critical_footprint_equivalence": footprint.get("success") is True,
        "active_stack": structure["stack_pass"] if phase.get("require_active_stack") else True,
        "closed_outline": structure["outline_pass"] if phase.get("require_closed_outline") else True,
    }
    success = all(checks.values())
    receipt = {
        "schema": 1, "kind": "kicad_physical_integrity", "success": success,
        "physical_integrity_pass": success, "phase": args.phase,
        "board": str(board), "board_sha256": sha256(board),
        "checks": checks, "critical_footprint_refs": refs,
        "footprint_equivalence": str(footprint_output),
        "structure": structure,
        "next": None if success else "Fix the electrical generator/library embedding or reviewed physical policy before placement; do not waive a missing critical rule area.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": success, "phase": args.phase, "board_sha256": receipt["board_sha256"],
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "output": str(output),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
