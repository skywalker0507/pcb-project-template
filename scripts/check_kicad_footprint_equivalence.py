#!/usr/bin/env python3
"""Compare embedded footprints with resolved libraries without modifying the board."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


KICAD_PYTHON = kicad_python()


def load_pcbnew() -> object:
    try:
        import pcbnew  # type: ignore
    except ImportError:
        ensure_host_kicad_python()
        import pcbnew
    return pcbnew


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def libraries(table: Path, project_dir: Path) -> dict[str, Path]:
    text = table.read_text(encoding="utf-8")
    result: dict[str, Path] = {}
    for name, uri in re.findall(r'\(lib\s+\(name\s+"([^"]+)"\).*?\(uri\s+"([^"]+)"\)', text):
        expanded = uri.replace("${KIPRJMOD}", str(project_dir))
        path = Path(expanded)
        result[name] = path.resolve() if path.is_absolute() else (project_dir / path).resolve()
    return result


def mm(pcbnew: object, value: int) -> float:
    return round(float(pcbnew.ToMM(value)), 6)


def pad_signature(pcbnew: object, pad: object) -> tuple[object, ...]:
    position = pad.GetFPRelativePosition()
    size = pad.GetSize()
    drill = pad.GetDrillSize()
    return (
        str(pad.GetNumber()), mm(pcbnew, position.x), mm(pcbnew, position.y),
        mm(pcbnew, size.x), mm(pcbnew, size.y), mm(pcbnew, drill.x), mm(pcbnew, drill.y),
        int(pad.GetShape()), int(pad.GetAttribute()), str(pad.GetLayerSet().FmtHex()),
        round(float(pad.GetFPRelativeOrientation().AsDegrees()), 6),
    )


def graphic_counts(footprint: object, board: object) -> dict[str, int]:
    values = Counter(str(board.GetLayerName(item.GetLayer())) for item in footprint.GraphicalItems())
    return dict(sorted(values.items()))


def rule_area_signatures(pcbnew: object, footprint: object) -> list[tuple[object, ...]]:
    signatures = []
    for zone in footprint.Zones():
        outline = zone.Outline()
        polygons = []
        for outline_index in range(outline.OutlineCount()):
            chain = outline.COutline(outline_index)
            polygons.append(tuple(
                (mm(pcbnew, chain.CPoint(index).x), mm(pcbnew, chain.CPoint(index).y))
                for index in range(chain.PointCount())
            ))
        signatures.append((
            bool(zone.GetIsRuleArea()), str(zone.GetLayerSet().FmtHex()),
            bool(zone.GetDoNotAllowTracks()), bool(zone.GetDoNotAllowVias()),
            bool(zone.GetDoNotAllowPads()), bool(zone.GetDoNotAllowZoneFills()),
            bool(zone.GetDoNotAllowFootprints()), tuple(polygons),
        ))
    return sorted(signatures)


def has_board_rule_area_compensation(board: object, ref: str, library: object) -> bool:
    candidates = [
        zone for zone in board.Zones()
        if zone.GetIsRuleArea() and ref.upper() in str(zone.GetZoneName()).upper()
    ]
    for required in library.Zones():
        matched = False
        for candidate in candidates:
            enabled_layers = [
                layer for layer in range(64)
                if board.IsLayerEnabled(layer) and required.GetLayerSet().Contains(layer)
            ]
            if not all(candidate.GetLayerSet().Contains(layer) for layer in enabled_layers):
                continue
            restrictions = (
                (required.GetDoNotAllowTracks(), candidate.GetDoNotAllowTracks()),
                (required.GetDoNotAllowVias(), candidate.GetDoNotAllowVias()),
                (required.GetDoNotAllowPads(), candidate.GetDoNotAllowPads()),
                (required.GetDoNotAllowZoneFills(), candidate.GetDoNotAllowZoneFills()),
            )
            if all(not needed or provided for needed, provided in restrictions):
                matched = True
                break
        if not matched:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.ref:
        raise SystemExit("At least one --ref is required; whole-board footprint output is forbidden")
    pcbnew = load_pcbnew()
    board_path = args.board.resolve()
    table = board_path.parent / "fp-lib-table"
    if not board_path.is_file() or not table.is_file():
        raise SystemExit("Board or adjacent fp-lib-table is unavailable")
    board = pcbnew.LoadBoard(str(board_path))
    by_ref = {str(item.GetReference()): item for item in board.GetFootprints()}
    lib_dirs = libraries(table, board_path.parent)
    results: list[dict[str, object]] = []
    for ref in args.ref:
        embedded = by_ref.get(ref)
        if embedded is None:
            raise SystemExit(f"Footprint reference not found: {ref}")
        fpid = embedded.GetFPID()
        nickname = str(fpid.GetLibNickname())
        name = str(fpid.GetLibItemName())
        directory = lib_dirs.get(nickname)
        if directory is None:
            raise SystemExit(f"Footprint library nickname is unresolved: {nickname}")
        library_file = directory / f"{name}.kicad_mod"
        if not library_file.is_file():
            raise SystemExit(f"Footprint library item is unavailable: {library_file}")
        library = pcbnew.FootprintLoad(str(directory), name)
        if library is None:
            raise SystemExit(f"KiCad could not load footprint library item: {nickname}:{name}")
        embedded_pads = sorted(pad_signature(pcbnew, pad) for pad in embedded.Pads())
        library_pads = sorted(pad_signature(pcbnew, pad) for pad in library.Pads())
        pad_equal = embedded_pads == library_pads
        embedded_graphics = graphic_counts(embedded, board)
        library_graphics = graphic_counts(library, board)
        embedded_rule_areas = rule_area_signatures(pcbnew, embedded)
        library_rule_areas = rule_area_signatures(pcbnew, library)
        rule_areas_equal = embedded_rule_areas == library_rule_areas
        rule_area_compensated = rule_areas_equal or (
            bool(library_rule_areas) and not embedded_rule_areas
            and has_board_rule_area_compensation(board, ref, library)
        )
        if not pad_equal:
            classification = "pad_geometry_difference"
        elif not rule_areas_equal and rule_area_compensated:
            classification = "nonpad_difference_with_board_rule_area_compensation"
        elif not rule_areas_equal:
            classification = "critical_rule_area_difference"
        elif embedded_graphics != library_graphics:
            classification = "nonpad_difference_only"
        else:
            classification = "exact_pad_rule_area_and_graphic_counts"
        results.append({
            "reference": ref, "library_id": f"{nickname}:{name}",
            "library_file": str(library_file), "library_sha256": sha256(library_file),
            "pad_geometry_equal": pad_equal,
            "embedded_pad_count": len(embedded_pads), "library_pad_count": len(library_pads),
            "pad_difference_count_each_side": [
                len([item for item in embedded_pads if item not in library_pads]),
                len([item for item in library_pads if item not in embedded_pads]),
            ],
            "embedded_graphics_by_layer": embedded_graphics,
            "library_graphics_by_layer": library_graphics,
            "nonpad_graphics_equal": embedded_graphics == library_graphics,
            "embedded_rule_area_count": len(embedded_rule_areas),
            "library_rule_area_count": len(library_rule_areas),
            "rule_areas_equal": rule_areas_equal,
            "board_rule_area_compensation": rule_area_compensated and not rule_areas_equal,
            "critical_rule_area_pass": rule_area_compensated,
            "classification": classification,
        })
    success = all(
        item["pad_geometry_equal"] is True and item["critical_rule_area_pass"] is True
        for item in results
    )
    receipt = {
        "schema": 1, "kind": "kicad_footprint_equivalence", "success": success,
        "board": str(board_path), "board_sha256": sha256(board_path),
        "fp_lib_table": str(table), "fp_lib_table_sha256": sha256(table),
        "footprints": results,
        "limits": [
            "Pad equivalence proves pad geometry only; it does not approve courtyard, fabrication, silkscreen, 3D-model, or mechanical differences.",
            "Library rule areas are critical. A missing embedded rule area must be restored or matched by an explicit all-copper board rule area before the receipt can pass.",
            "A nonpad-only result must not trigger an automatic library refresh or DRC waiver.",
        ],
    }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt["output"] = str(output)
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
