#!/usr/bin/env python3
"""Apply a bounded, SHA-guarded all-copper board rule area to an isolated candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
PROHIBIT_CLASSES = {"tracks", "vias", "pads", "copperpour"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def load_plan(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Keepout plan is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("kind") != "kicad_keepout_plan":
        raise SystemExit("Keepout plan must be schema-1 kicad_keepout_plan")
    layers = value.get("layers")
    if not isinstance(layers, list) or not layers or not set(layers) <= set(COPPER_LAYERS):
        raise SystemExit("Keepout layers must be a non-empty subset of the reviewed four copper layers")
    points = value.get("polygon_mm")
    if not isinstance(points, list) or not 3 <= len(points) <= 20:
        raise SystemExit("Keepout polygon requires 3-20 points")
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise SystemExit("Every keepout point must be [x_mm, y_mm]")
        float(point[0]); float(point[1])
    prohibit = value.get("prohibit")
    if not isinstance(prohibit, list) or not prohibit or not set(prohibit) <= PROHIBIT_CLASSES:
        raise SystemExit("Keepout prohibit must be a non-empty set of reviewed copper object classes")
    if str(value.get("name", "")).startswith("RF_ANTENNA_") and (
        layers != list(COPPER_LAYERS) or set(prohibit) != PROHIBIT_CLASSES
    ):
        raise SystemExit("Production antenna keepout must cover all layers and every copper object class")
    return value


def copy_companions(source: Path, destination: Path) -> None:
    for suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
        companion = source.with_suffix(suffix)
        if companion.is_file():
            shutil.copy2(companion, destination.with_suffix(suffix))
    for name in ("fp-lib-table", "sym-lib-table"):
        companion = source.parent / name
        if not companion.is_file():
            continue
        target = destination.parent / name
        text = companion.read_text(encoding="utf-8")

        def rebase(match: re.Match[str]) -> str:
            resolved = (source.parent / match.group(1)).resolve()
            return "${KIPRJMOD}/" + Path(os.path.relpath(resolved, destination.parent)).as_posix()

        target.write_text(re.sub(r"\$\{KIPRJMOD\}/([^\")]+)", rebase, text), encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "release"}:
        raise SystemExit("Keepout plans are allowed only during physical or release cleanup")
    source = args.input.resolve()
    destination = args.output.resolve()
    if not source.is_file() or destination.exists() or destination == source:
        raise SystemExit("Keepout edit requires an existing input and a new isolated output")
    plan_path = args.plan.resolve()
    plan = load_plan(plan_path)
    before = sha256(source)
    if plan.get("board_sha256") != before:
        raise SystemExit("Keepout plan board SHA-256 does not match the input candidate")

    ensure_kicad_python()
    import pcbnew

    board = pcbnew.LoadBoard(str(source))
    name = str(plan.get("name", "RF_ANTENNA_KEEPOUT"))
    if any(zone.GetIsRuleArea() and str(zone.GetZoneName()) == name for zone in board.Zones()):
        raise SystemExit(f"Named keepout already exists: {name}")
    layer_set = pcbnew.LSET()
    for layer_name in plan["layers"]:
        layer_id = board.GetLayerID(layer_name)
        if not board.IsLayerEnabled(layer_id):
            raise SystemExit(f"Reviewed keepout layer is not enabled: {layer_name}")
        layer_set.AddLayer(layer_id)
    zone = pcbnew.ZONE(board)
    zone.SetZoneName(name)
    zone.SetLayerSet(layer_set)
    zone.SetIsRuleArea(True)
    prohibited = set(plan["prohibit"])
    zone.SetDoNotAllowTracks("tracks" in prohibited)
    zone.SetDoNotAllowVias("vias" in prohibited)
    zone.SetDoNotAllowPads("pads" in prohibited)
    zone.SetDoNotAllowZoneFills("copperpour" in prohibited)
    zone.SetDoNotAllowFootprints(False)
    outline = zone.Outline()
    outline_index = outline.NewOutline()
    for x_mm, y_mm in plan["polygon_mm"]:
        outline.Append(pcbnew.FromMM(float(x_mm)), pcbnew.FromMM(float(y_mm)), outline_index)
    board.Add(zone)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    run = create_run(stage, "kicad-keepout", args.run_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(destination), board)
    copy_companions(source, destination)
    receipt = {
        "schema": 1,
        "kind": "kicad_keepout",
        "success": True,
        "source": relative(source),
        "before_sha256": before,
        "board": relative(destination),
        "board_sha256": sha256(destination),
        "plan": relative(plan_path),
        "name": name,
        "layers": list(plan["layers"]),
        "polygon_mm": plan["polygon_mm"],
        "prohibit": plan["prohibit"],
        "zones_refilled": True,
        "limits": "Board-level rule area only; exact candidate DRC, connectivity and Gerber copper inspection remain mandatory.",
    }
    evidence = run.evidence / "kicad-keepout.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={"receipt": relative(evidence), "plan": relative(plan_path)}, metrics={"layers": len(plan["layers"]), "polygon_points": len(plan["polygon_mm"])})
    print(json.dumps({**receipt, "receipt": relative(evidence)}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
