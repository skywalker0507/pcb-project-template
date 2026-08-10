#!/usr/bin/env python3
"""Extract a compact schema-2 parity manifest from a KiCad PCB using pcbnew."""

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


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def point_nm(value: Any) -> list[int]:
    return [int(value.x), int(value.y)]


def optional_call(item: Any, method: str) -> Any | None:
    function = getattr(item, method, None)
    if not callable(function):
        return None
    try:
        return function()
    except (TypeError, RuntimeError):
        return None


def vector_value(value: Any) -> int | list[int] | None:
    if value is None:
        return None
    x, y = int(value.x), int(value.y)
    if x <= 0 and y <= 0:
        return None
    return x if x == y else [x, y]


def selected_parts(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for part in data.get("parts", []):
        if not isinstance(part, dict) or not isinstance(part.get("mpn"), str):
            continue
        for reference in part.get("refs", []):
            if isinstance(reference, str):
                result[reference] = part["mpn"]
    return result


def shape_signature(item: Any) -> dict[str, Any]:
    import pcbnew

    shape = optional_call(item, "GetShape")
    result: dict[str, Any] = {"shape": str(shape)}
    for method, key in (("GetStart", "start_nm"), ("GetEnd", "end_nm"), ("GetCenter", "center_nm"), ("GetMid", "mid_nm")):
        value = optional_call(item, method)
        if value is not None and hasattr(value, "x"):
            result[key] = point_nm(value)
    scalar_methods = [("GetWidth", "width_nm")]
    if shape in {pcbnew.SHAPE_T_ARC, pcbnew.SHAPE_T_CIRCLE}:
        scalar_methods.append(("GetRadius", "radius_nm"))
    for method, key in scalar_methods:
        value = optional_call(item, method)
        if isinstance(value, int):
            result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--selected-parts", type=Path, default=ROOT / "config/selected-parts.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ensure_kicad_python()
    import pcbnew

    board_path = args.board.resolve()
    board = pcbnew.LoadBoard(str(board_path))
    part_map = selected_parts(args.selected_parts.resolve())
    components: list[dict[str, Any]] = []
    holes: list[dict[str, Any]] = []
    nets: set[str] = set()
    for footprint in sorted(board.GetFootprints(), key=lambda item: item.GetReference()):
        reference = footprint.GetReference()
        fpid = footprint.GetFPID()
        footprint_name = f"{fpid.GetLibNickname()}:{fpid.GetLibItemName()}"
        pads: list[dict[str, Any]] = []
        for pad in footprint.Pads():
            number = str(pad.GetNumber())
            net = str(pad.GetNetname()) or None
            if net:
                nets.add(net)
            drill = vector_value(optional_call(pad, "GetDrillSize"))
            size = optional_call(pad, "GetSize")
            position = optional_call(pad, "GetPosition")
            orientation = optional_call(pad, "GetOrientationDegrees")
            signature = {
                "shape": str(optional_call(pad, "GetShape")),
                "size_nm": point_nm(size) if size is not None else None,
                "position_nm": point_nm(position) if position is not None else None,
                "orientation_udeg": int(round(float(orientation or 0) * 1_000_000)),
            }
            pad_record = {
                "number": number,
                "net": net,
                "drill_nm": drill,
                "pad_type": str(optional_call(pad, "GetAttribute")),
                "geometry_signature": signature,
            }
            pads.append(pad_record)
            if drill is not None:
                holes.append({"reference": reference, "number": number, "position_nm": signature["position_nm"], "drill_nm": drill})
        components.append({
            "reference": reference,
            "footprint": footprint_name,
            "part_identity": part_map.get(reference),
            "side": str(board.GetLayerName(footprint.GetLayer())),
            "rotation_udeg": int(round(float(footprint.GetOrientationDegrees()) * 1_000_000)),
            "pads": sorted(pads, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))),
        })

    outline = [
        shape_signature(item) for item in board.GetDrawings()
        if item.GetLayer() == pcbnew.Edge_Cuts
    ]
    outline.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    holes.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    payload = {
        "schema": 2,
        "kind": "physical_interchange_manifest",
        "normalization": {"length_unit": "nm", "rotation_unit": "microdegree"},
        "board": str(board_path),
        "board_sha256": hashlib.sha256(board_path.read_bytes()).hexdigest().upper(),
        "components": components,
        "nets": sorted(nets),
        "board_outline": outline,
        "holes": holes,
        "limits": "Deterministic compact extraction only; this manifest is not DRC, placement review, or release evidence.",
    }
    from check_easyeda_import_parity import validate_manifest
    validate_manifest(payload, "extracted KiCad manifest")
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": True,
        "kind": payload["kind"],
        "board_sha256": payload["board_sha256"],
        "component_count": len(components),
        "pad_count": sum(len(item["pads"]) for item in components),
        "net_count": len(nets),
        "hole_count": len(holes),
        "outline_primitive_count": len(outline),
        "output": str(args.output.resolve()),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
