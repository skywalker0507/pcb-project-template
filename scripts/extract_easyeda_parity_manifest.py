#!/usr/bin/env python3
"""Normalize a bulk EasyEDA API capture into the compact parity schema."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


NM_PER_MIL = 25_400
NM_PER_HOLE_UNIT = 254_000


def nm(value: Any) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SystemExit(f"Expected a numeric EasyEDA PCB coordinate, got {value!r}")
    return int(round(float(value) * NM_PER_MIL))


def shape_code(pad: dict[str, Any]) -> str:
    raw = pad.get("pad")
    kind = str(raw[0] if isinstance(raw, list) and raw else pad.get("shape", "")).upper()
    width, height = nm(pad.get("width", 0)), nm(pad.get("height", 0))
    if kind in {"ELLIPSE", "ROUND", "CIRCLE"}:
        return "0" if width == height else "2"
    if kind in {"OVAL", "OBLONG"}:
        return "2"
    if kind in {"RECT", "RECTANGLE"}:
        rounded = len(raw) > 3 and isinstance(raw[3], (int, float)) and raw[3] > 0 if isinstance(raw, list) else False
        return "4" if rounded else "1"
    return "6"


def drill_nm(pad: dict[str, Any]) -> int | list[int] | None:
    hole = pad.get("hole")
    if not isinstance(hole, list) or len(hole) < 2 or not hole[1]:
        return None
    convert = lambda value: int(round(float(value) * NM_PER_HOLE_UNIT))
    if str(hole[0]).upper() == "SLOT" and len(hole) >= 3:
        return [convert(hole[1]), convert(hole[2])]
    return convert(hole[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture_path = args.capture.resolve()
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    if not isinstance(capture, dict) or not isinstance(capture.get("components"), list):
        raise SystemExit("Invalid EasyEDA PCB capture")

    components: list[dict[str, Any]] = []
    holes: list[dict[str, Any]] = []
    for component in capture["components"]:
        reference = str(component.get("designator", ""))
        identity = component.get("identity") if isinstance(component.get("identity"), dict) else {}
        pads = []
        for pad in component.get("pads", []):
            drill = drill_nm(pad)
            signature = {
                "shape": shape_code(pad),
                "size_nm": [nm(pad.get("width", 0)), nm(pad.get("height", 0))],
                # EasyEDA PCB uses a screen-style +Y-down coordinate system;
                # parity manifests use KiCad's +Y-up interchange coordinates.
                "position_nm": [nm(pad.get("x", 0)), -nm(pad.get("y", 0))],
                "orientation_udeg": int(round(-float(pad.get("rotation", 0)) * 1_000_000)) % 360_000_000,
            }
            record = {
                "number": str(pad.get("number", "")),
                "net": str(pad.get("net")) if pad.get("net") else None,
                "drill_nm": drill,
                "pad_type": (
                    "0" if drill is not None and pad.get("metallization") is not False
                    else "3" if drill is not None
                    else "2" if isinstance(pad.get("maskAndPasteExpansion"), dict) and (
                        pad["maskAndPasteExpansion"].get("topPasteMask", 0) <= -1000
                        or pad["maskAndPasteExpansion"].get("bottomPasteMask", 0) <= -1000
                    )
                    else "1"
                ),
                "geometry_signature": signature,
            }
            pads.append(record)
            if drill is not None:
                holes.append({
                    "reference": reference,
                    "number": record["number"],
                    "position_nm": signature["position_nm"],
                    "drill_nm": drill,
                })
        components.append({
            "reference": reference,
            "footprint": identity.get("footprint"),
            "part_identity": identity.get("partIdentity"),
            "side": "F.Cu" if int(component.get("layer", 1)) == 1 else "B.Cu",
            "rotation_udeg": int(round(-float(component.get("rotation", 0)) * 1_000_000)) % 360_000_000,
            "pads": sorted(pads, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))),
        })

    board = capture.get("board") if isinstance(capture.get("board"), dict) else {}
    outline: list[dict[str, Any]] = []
    for item in board.get("outlineLines", []):
        outline.append({
            "shape": "SEGMENT",
            "start_nm": [nm(item.get("startX", 0)), -nm(item.get("startY", 0))],
            "end_nm": [nm(item.get("endX", 0)), -nm(item.get("endY", 0))],
        })
    # Arc normalization is deliberately deferred until a frozen source contains arcs.
    if board.get("outlineArcs"):
        raise SystemExit("EasyEDA outline arc normalization is not yet proven")
    outline.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    holes.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    payload = {
        "schema": 2,
        "kind": "physical_interchange_manifest",
        "normalization": {"length_unit": "nm", "rotation_unit": "microdegree"},
        "board": str(capture_path),
        "board_sha256": hashlib.sha256(capture_path.read_bytes()).hexdigest().upper(),
        "components": sorted(components, key=lambda item: item["reference"]),
        "nets": sorted(str(name) for name in capture.get("nets", []) if name),
        "board_outline": outline,
        "holes": holes,
        "limits": "Deterministic EasyEDA API normalization only; strict parity is decided separately.",
    }
    from check_easyeda_import_parity import validate_manifest
    validate_manifest(payload, "EasyEDA capture manifest")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": True,
        "component_count": len(components),
        "pad_count": sum(len(item["pads"]) for item in components),
        "net_count": len(payload["nets"]),
        "hole_count": len(holes),
        "outline_primitive_count": len(outline),
        "output": str(output),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
