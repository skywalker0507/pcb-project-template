#!/usr/bin/env python3
"""Read-only, all-component resource integrity gate before electrical build success."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from electrical_backend import ROOT, footprint_libraries, read_config, selected_backend, sha256, source_sha256
from check_kicad_footprint_equivalence import load_pcbnew, pad_signature


def canonical_ring(points: list[tuple[int, int]]) -> tuple:
    """Polygon start vertex and winding do not change its geometry."""
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if not points:
        return ()
    variants = []
    for order in (points, list(reversed(points))):
        start = min(range(len(order)), key=lambda i: order[i:]+order[:i])
        variants.append(tuple(order[start:] + order[:start]))
    return min(variants)


def polygon_signature(shape: object) -> tuple:
    def ring(chain: object) -> tuple:
        return canonical_ring([(chain.CPoint(i).x, chain.CPoint(i).y) for i in range(chain.PointCount())])
    return tuple(sorted((
        ring(shape.COutline(index)),
        tuple(sorted(ring(shape.CHole(index, hole)) for hole in range(shape.HoleCount(index)))),
    ) for index in range(shape.OutlineCount())))


def resource_pad_signature(pcbnew: object, pad: object) -> tuple:
    custom = ()
    if pad.GetShape() == pcbnew.PAD_SHAPE_CUSTOM:
        polygons = []
        for layer in range(pcbnew.PCB_LAYER_ID_COUNT):
            if pcbnew.IsCopperLayer(layer) and pad.GetLayerSet().Contains(layer):
                shape = pcbnew.SHAPE_POLY_SET()
                pad.MergePrimitivesAsPolygon(layer, shape)
                polygons.append((layer, int(pad.GetAnchorPadShape(layer)), polygon_signature(shape)))
        custom = (int(pad.GetCustomShapeInZoneOpt()), tuple(polygons))
    return (*pad_signature(pcbnew, pad), custom)


def area_signatures(footprint: object, board: object | None = None) -> list[tuple]:
    signatures = []
    for zone in footprint.Zones():
        layer_mask = int(str(zone.GetLayerSet().FmtHex()).replace("_", ""), 16)
        if board is not None:
            # KiCad serializes *.Cu footprint areas to the board's enabled
            # layers. Compare that exact effective mask on both sides.
            layer_mask &= int(str(board.GetEnabledLayers().FmtHex()).replace("_", ""), 16)
        signatures.append((
            bool(zone.GetIsRuleArea()), layer_mask,
            bool(zone.GetDoNotAllowTracks()), bool(zone.GetDoNotAllowVias()),
            bool(zone.GetDoNotAllowPads()), bool(zone.GetDoNotAllowZoneFills()),
            bool(zone.GetDoNotAllowFootprints()), polygon_signature(zone.Outline()),
        ))
    return sorted(signatures)


def audit(pcbnew: object, board_path: Path, source_root: Path) -> dict:
    before = sha256(board_path)
    source_hash = source_sha256(source_root)
    libraries = footprint_libraries(source_root)
    board = pcbnew.LoadBoard(str(board_path))
    footprints = list(board.GetFootprints())
    counts = Counter(str(fp.GetReference()) for fp in footprints)
    results = []
    for embedded in sorted(footprints, key=lambda fp: str(fp.GetReference())):
        ref = str(embedded.GetReference())
        fpid = embedded.GetFPID()
        nickname, name = str(fpid.GetLibNickname()), str(fpid.GetLibItemName())
        record = {"ref": ref, "footprint": f"{nickname}:{name}", "failures": []}
        if counts[ref] != 1 or not ref or "*" in ref:
            record["failures"].append("invalid_or_duplicate_reference")
        directory = libraries.get(nickname)
        resource = directory / f"{name}.kicad_mod" if directory else None
        if resource is None or not resource.is_file():
            record["failures"].append("unresolved_source_footprint")
        else:
            resource.resolve().relative_to(source_root.resolve())
            library = pcbnew.FootprintLoad(str(directory), name)
            if library is None:
                raise ValueError(f"Cannot load source footprint: {resource}")
            record["source"] = resource.relative_to(source_root).as_posix()
            record["source_sha256"] = sha256(resource)
            # Align an in-memory library instance; never modify the candidate.
            # KiCad 10 Flip requires board layer context even for a library item.
            library.SetParent(board)
            if library.IsFlipped() != embedded.IsFlipped():
                library.Flip(library.GetPosition(), False)
            library.SetOrientation(embedded.GetOrientation())
            library.SetPosition(embedded.GetPosition())
            pads_equal = sorted(resource_pad_signature(pcbnew, pad) for pad in library.Pads()) == sorted(resource_pad_signature(pcbnew, pad) for pad in embedded.Pads())
            if not pads_equal:
                record["failures"].append("pad_geometry_or_numbering_changed")
            expected, actual = area_signatures(library, board), area_signatures(embedded, board)
            record["source_rule_areas"] = len(expected)
            record["candidate_rule_areas"] = len(actual)
            if expected != actual:
                record["failures"].append("footprint_rule_areas_changed_or_missing")
            for field in ("Manufacturer", "Partnumber"):
                required = str(library.GetFieldText(field)) if library.HasField(field) else ""
                actual_value = str(embedded.GetFieldText(field)) if embedded.HasField(field) else ""
                if required and actual_value != required:
                    record["failures"].append(f"identity_property_changed:{field}")
        record["success"] = not record["failures"]
        results.append(record)
    unchanged = before == sha256(board_path) and source_hash == source_sha256(source_root)
    return {
        "schema": 1, "kind": "electrical_candidate_integrity", "success": bool(results) and unchanged and all(item["success"] for item in results),
        "candidate_board": str(board_path), "candidate_board_sha256": before,
        "source_root": str(source_root), "source_sha256": source_hash,
        "read_only_inputs_unchanged": unchanged, "component_count": len(results),
        "enabled_layers": str(board.GetEnabledLayers().FmtHex()),
        "kicad_version": str(pcbnew.Version()),
        "failed_refs": [item["ref"] for item in results if not item["success"]],
        "source_rule_area_count": sum(item.get("source_rule_areas", 0) for item in results),
        "candidate_rule_area_count": sum(item.get("candidate_rule_areas", 0) for item in results),
        "components": results,
        "limits": "Local source resources only. This is not ERC, connectivity, placement, DRC, visual review, or release approval. Board-level substitutes do not excuse missing footprint rule areas.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        pcbnew = load_pcbnew(Path(__file__).resolve())
        if args.source_root:
            source = args.source_root.resolve()
        else:
            _, backend = selected_backend(read_config())
            source = (ROOT / backend["source_root"]).resolve()
        payload = audit(pcbnew, args.board.resolve(), source)
    except Exception as exc:
        payload = {"schema": 1, "kind": "electrical_candidate_integrity", "success": False, "failure": type(exc).__name__, "evidence": str(exc).splitlines()[-4:]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("success", "component_count", "failed_refs", "source_rule_area_count", "candidate_rule_area_count", "failure", "evidence") if key in payload}, separators=(",", ":")))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
