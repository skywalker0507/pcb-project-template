#!/usr/bin/env python3
"""Compare schema-2 normalized PCB manifests for an EasyEDA import gate."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


LENGTH_UNIT = "nm"
ROTATION_UNIT = "microdegree"
FULL_ROTATION_UDEG = 360_000_000
# EasyEDA's PCB API exposes geometry on a 0.1 mil grid. Comparing both
# manifests on that documented interchange grid avoids false failures below
# the representable resolution while retaining exact electrical comparison.
EASYEDA_GRID_NM = 2_540


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_collection(value: Any) -> str:
    if isinstance(value, list):
        return canonical(sorted(value, key=canonical))
    return canonical(value)


def quantize_nm(value: int) -> int:
    sign = -1 if value < 0 else 1
    magnitude = abs(value)
    return sign * ((magnitude + EASYEDA_GRID_NM // 2) // EASYEDA_GRID_NM) * EASYEDA_GRID_NM


def quantize_length_value(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return quantize_nm(value)
    if isinstance(value, list):
        return [quantize_length_value(item) for item in value]
    return value


def normalize_physical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: quantize_length_value(child) if key.endswith("_nm") else normalize_physical(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [normalize_physical(item) for item in value]
    return value


def reject_floats(value: Any, label: str) -> None:
    if isinstance(value, float):
        raise SystemExit(f"{label} must use fixed integer units, not floating point")
    if isinstance(value, dict):
        for key, child in value.items():
            reject_floats(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_floats(child, f"{label}[{index}]")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Parity manifest is unavailable or invalid: {path}: {exc}") from exc
    validate_manifest(value, str(path))
    return value


def validate_manifest(value: Any, label: str = "manifest") -> None:
    if not isinstance(value, dict) or value.get("schema") != 2:
        raise SystemExit(f"Parity manifest must use schema 2: {label}")
    normalization = value.get("normalization")
    if not isinstance(normalization, dict) or normalization.get("length_unit") != LENGTH_UNIT or normalization.get("rotation_unit") != ROTATION_UNIT:
        raise SystemExit(f"Parity manifest must declare nm and microdegree normalization: {label}")
    if not isinstance(value.get("board_sha256"), str) or not value["board_sha256"]:
        raise SystemExit(f"Parity manifest needs board_sha256: {label}")
    if not isinstance(value.get("components"), list) or not isinstance(value.get("nets"), list):
        raise SystemExit(f"Parity manifest needs components and nets lists: {label}")
    if "board_outline" not in value or not isinstance(value.get("holes"), list):
        raise SystemExit(f"Parity manifest needs board_outline and holes: {label}")
    reject_floats(value["board_outline"], f"{label}.board_outline")
    reject_floats(value["holes"], f"{label}.holes")
    component_map(value)
    net_names(value)


def net_names(manifest: dict[str, Any]) -> list[str]:
    names = []
    for item in manifest["nets"]:
        name = item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str):
            raise SystemExit("Every parity-manifest net must be a string or an object with a string name")
        names.append(name)
    if len(names) != len(set(names)):
        raise SystemExit("Parity manifest contains duplicate net names")
    return sorted(names)


def component_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for component in manifest["components"]:
        if not isinstance(component, dict):
            raise SystemExit("Every parity-manifest component must be an object")
        reference = component.get("reference")
        if not isinstance(reference, str) or not reference or reference in result:
            raise SystemExit("Parity-manifest component references must be non-empty and unique")
        for field in ("footprint", "part_identity", "side", "rotation_udeg", "pads"):
            if field not in component:
                raise SystemExit(f"Component {reference} is missing {field}")
        if not isinstance(component["footprint"], str) or not component["footprint"]:
            raise SystemExit(f"Component {reference} footprint must be a non-empty string")
        if component["part_identity"] is not None and not isinstance(component["part_identity"], str):
            raise SystemExit(f"Component {reference} part_identity must be a string or null")
        if not isinstance(component["side"], str) or not component["side"]:
            raise SystemExit(f"Component {reference} side must be a non-empty string")
        if not isinstance(component["rotation_udeg"], int) or isinstance(component["rotation_udeg"], bool):
            raise SystemExit(f"Component {reference} rotation_udeg must be an integer")
        pads = component["pads"]
        if not isinstance(pads, list):
            raise SystemExit(f"Component {reference} needs a pads list")
        for index, pad in enumerate(pads):
            if not isinstance(pad, dict):
                raise SystemExit(f"Component {reference} pad {index} must be an object")
            number = pad.get("number")
            if not isinstance(number, str):
                raise SystemExit(f"Component {reference} pad {index} needs a string logical number (empty is valid for NPTH)")
            if pad.get("net") is not None and not isinstance(pad.get("net"), str):
                raise SystemExit(f"Component {reference} pad {index} net must be a string or null")
            if "drill_nm" not in pad or "geometry_signature" not in pad or not isinstance(pad.get("pad_type"), str):
                raise SystemExit(f"Component {reference} pad {index} needs drill_nm, pad_type, and geometry_signature")
            drill = pad["drill_nm"]
            valid_drill = (
                drill is None
                or (isinstance(drill, int) and not isinstance(drill, bool))
                or (isinstance(drill, list) and len(drill) == 2 and all(isinstance(item, int) and not isinstance(item, bool) for item in drill))
            )
            if not valid_drill:
                raise SystemExit(f"Component {reference} pad {index} drill_nm must be an integer, integer pair, or null")
            reject_floats(pad["geometry_signature"], f"Component {reference} pad {index} geometry_signature")
        result[reference] = component
    return result


def counter_payload(counter: Counter[Any]) -> list[dict[str, Any]]:
    return [
        {"item": list(item) if isinstance(item, tuple) else item, "count": count}
        for item, count in sorted(counter.items(), key=lambda value: canonical(value[0]))
    ]


def pad_number_counter(component: dict[str, Any]) -> Counter[str]:
    return Counter(str(pad["number"]) for pad in component["pads"])


def pad_net_counter(component: dict[str, Any]) -> Counter[tuple[str, str | None]]:
    return Counter((str(pad["number"]), pad.get("net")) for pad in component["pads"])


def pad_physical_counter(component: dict[str, Any]) -> Counter[str]:
    normalized = []
    for pad in component["pads"]:
        geometry = normalize_physical(pad.get("geometry_signature"))
        size = geometry.get("size_nm") if isinstance(geometry, dict) else None
        shape = geometry.get("shape") if isinstance(geometry, dict) else None
        if isinstance(size, list) and len(size) == 2 and shape in {"0", "1", "2", "4"}:
            geometry["orientation_udeg"] = int(geometry.get("orientation_udeg", 0)) % 180_000_000
        if isinstance(size, list) and len(size) == 2 and size[0] == size[1] and shape in {"0", "1", "4"}:
            geometry["orientation_udeg"] = 0
        normalized.append(canonical({
            "number": str(pad["number"]),
            "net": pad.get("net"),
            "drill_nm": quantize_length_value(pad.get("drill_nm")),
            "pad_type": pad.get("pad_type"),
            "geometry_signature": geometry,
        }))
    return Counter(normalized)


def compare_electrical(source: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(source, "source")
    validate_manifest(candidate, "candidate")
    left_components = component_map(source)
    right_components = component_map(candidate)
    failures: dict[str, Any] = {}
    left_refs, right_refs = sorted(left_components), sorted(right_components)
    if left_refs != right_refs:
        failures["component_references"] = {"source": left_refs, "candidate": right_refs}
    footprint_mismatches: list[str] = []
    part_mismatches: list[str] = []
    pad_number_mismatches: dict[str, Any] = {}
    pad_net_mismatches: dict[str, Any] = {}
    for reference in sorted(set(left_components) & set(right_components)):
        left, right = left_components[reference], right_components[reference]
        if left.get("footprint") != right.get("footprint"):
            footprint_mismatches.append(reference)
        if left.get("part_identity") != right.get("part_identity"):
            part_mismatches.append(reference)
        left_numbers, right_numbers = pad_number_counter(left), pad_number_counter(right)
        if left_numbers != right_numbers:
            pad_number_mismatches[reference] = {"source": counter_payload(left_numbers), "candidate": counter_payload(right_numbers)}
        left_mapping, right_mapping = pad_net_counter(left), pad_net_counter(right)
        if left_mapping != right_mapping:
            pad_net_mismatches[reference] = {"source": counter_payload(left_mapping), "candidate": counter_payload(right_mapping)}
    if footprint_mismatches:
        failures["footprints"] = footprint_mismatches
    if part_mismatches:
        failures["part_identity"] = part_mismatches
    if pad_number_mismatches:
        failures["pad_numbers"] = pad_number_mismatches
    if pad_net_mismatches:
        failures["pad_to_net_mapping"] = pad_net_mismatches
    left_nets, right_nets = net_names(source), net_names(candidate)
    if left_nets != right_nets:
        failures["nets"] = {"source": left_nets, "candidate": right_nets}
    return {
        "electrical_parity_pass": not failures,
        "component_count": {"source": len(left_components), "candidate": len(right_components)},
        "pad_count": {
            "source": sum(len(item["pads"]) for item in left_components.values()),
            "candidate": sum(len(item["pads"]) for item in right_components.values()),
        },
        "failures": failures,
    }


def compare(source: dict[str, Any], imported: dict[str, Any]) -> dict[str, Any]:
    electrical = compare_electrical(source, imported)
    failures = dict(electrical["failures"])
    left_components, right_components = component_map(source), component_map(imported)
    side_mismatches: list[str] = []
    rotation_mismatches: list[str] = []
    pad_geometry_mismatches: list[str] = []
    for reference in sorted(set(left_components) & set(right_components)):
        left, right = left_components[reference], right_components[reference]
        if left.get("side") != right.get("side"):
            side_mismatches.append(reference)
        if int(left["rotation_udeg"]) % FULL_ROTATION_UDEG != int(right["rotation_udeg"]) % FULL_ROTATION_UDEG:
            rotation_mismatches.append(reference)
        if pad_physical_counter(left) != pad_physical_counter(right):
            pad_geometry_mismatches.append(reference)
    if side_mismatches:
        failures["side"] = side_mismatches
    if rotation_mismatches:
        failures["rotation"] = rotation_mismatches
    if pad_geometry_mismatches:
        failures["pad_geometry"] = pad_geometry_mismatches
    if canonical_collection(normalize_physical(source["board_outline"])) != canonical_collection(normalize_physical(imported["board_outline"])):
        failures["board_outline"] = True
    if canonical_collection(normalize_physical(source["holes"])) != canonical_collection(normalize_physical(imported["holes"])):
        failures["holes"] = True
    electrical_keys = {"component_references", "footprints", "part_identity", "pad_numbers", "pad_to_net_mapping", "nets"}
    return {
        "parity_pass": not failures,
        "strict_connectivity_failure": bool(electrical_keys & failures.keys()),
        "component_count": electrical["component_count"],
        "pad_count": electrical["pad_count"],
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Schema-2 manifest from the immutable frozen KiCad board")
    parser.add_argument("--imported", type=Path, required=True, help="Schema-2 manifest exported by EasyEDA immediately after import")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_path, imported_path = args.source.resolve(), args.imported.resolve()
    source_manifest, imported_manifest = load_manifest(source_path), load_manifest(imported_path)
    result = compare(source_manifest, imported_manifest)
    payload = {
        "schema": 1,
        "kind": "easyeda_import_parity",
        "success": result["parity_pass"],
        **result,
        "source_manifest_sha256": file_sha256(source_path),
        "imported_manifest_sha256": file_sha256(imported_path),
        "source_board_sha256": source_manifest.get("board_sha256"),
        "imported_board_sha256": imported_manifest.get("board_sha256"),
        "normalization": source_manifest["normalization"],
        "limits": "This import gate includes physical identity. Final electrical parity remains a separate mandatory gate after placement/routing.",
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
