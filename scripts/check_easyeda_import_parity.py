#!/usr/bin/env python3
"""Compare compact normalized PCB manifests for an EasyEDA import gate.

The bridge/API adapter is responsible for converting each backend's data into
this small manifest. This validator never sends or prints a full PCB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Parity manifest is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SystemExit(f"Parity manifest must use schema 1: {path}")
    if not isinstance(value.get("components"), list):
        raise SystemExit(f"Parity manifest needs a components list: {path}")
    if not isinstance(value.get("nets"), list):
        raise SystemExit(f"Parity manifest needs a nets list: {path}")
    if "board_outline" not in value or not isinstance(value.get("holes"), list):
        raise SystemExit(f"Parity manifest needs board_outline and holes: {path}")
    return value


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
        pads = component.get("pads")
        if not isinstance(pads, list):
            raise SystemExit(f"Component {reference} needs a pads list")
        numbers = [str(pad.get("number")) for pad in pads if isinstance(pad, dict)]
        if len(numbers) != len(pads) or len(numbers) != len(set(numbers)):
            raise SystemExit(f"Component {reference} pad numbers must be present and unique")
        result[reference] = component
    return result


def pad_map(component: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(pad["number"]): pad for pad in component["pads"]}


def compare(source: dict[str, Any], imported: dict[str, Any]) -> dict[str, Any]:
    source_components = component_map(source)
    imported_components = component_map(imported)
    failures: dict[str, Any] = {}
    source_refs = sorted(source_components)
    imported_refs = sorted(imported_components)
    if source_refs != imported_refs:
        failures["component_references"] = {"source": source_refs, "imported": imported_refs}

    footprint_mismatches: list[str] = []
    pad_number_mismatches: list[str] = []
    pad_net_mismatches: list[str] = []
    pad_drill_mismatches: list[str] = []
    side_mismatches: list[str] = []
    rotation_mismatches: list[str] = []
    for reference in sorted(set(source_components) & set(imported_components)):
        left = source_components[reference]
        right = imported_components[reference]
        if left.get("footprint") != right.get("footprint"):
            footprint_mismatches.append(reference)
        left_pads, right_pads = pad_map(left), pad_map(right)
        if sorted(left_pads) != sorted(right_pads):
            pad_number_mismatches.append(reference)
        for number in sorted(set(left_pads) & set(right_pads)):
            if left_pads[number].get("net") != right_pads[number].get("net"):
                pad_net_mismatches.append(f"{reference}.{number}")
            if canonical(left_pads[number].get("drill")) != canonical(right_pads[number].get("drill")):
                pad_drill_mismatches.append(f"{reference}.{number}")
        if left.get("side") != right.get("side"):
            side_mismatches.append(reference)
        if canonical(left.get("rotation")) != canonical(right.get("rotation")):
            rotation_mismatches.append(reference)
    for key, value in (
        ("footprints", footprint_mismatches),
        ("pad_numbers", pad_number_mismatches),
        ("pad_to_net_mapping", pad_net_mismatches),
        ("pad_drills", pad_drill_mismatches),
        ("side", side_mismatches),
        ("rotation", rotation_mismatches),
    ):
        if value:
            failures[key] = value
    source_nets, imported_nets = net_names(source), net_names(imported)
    if source_nets != imported_nets:
        failures["nets"] = {"source": source_nets, "imported": imported_nets}
    if canonical(source["board_outline"]) != canonical(imported["board_outline"]):
        failures["board_outline"] = True
    if canonical(source["holes"]) != canonical(imported["holes"]):
        failures["holes"] = True
    strict_keys = {"component_references", "pad_numbers", "pad_to_net_mapping", "nets"}
    return {
        "parity_pass": not failures,
        "strict_connectivity_failure": bool(strict_keys & failures.keys()),
        "component_count": {"source": len(source_components), "imported": len(imported_components)},
        "pad_count": {
            "source": sum(len(item["pads"]) for item in source_components.values()),
            "imported": sum(len(item["pads"]) for item in imported_components.values()),
        },
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Normalized manifest from the frozen KiCad interchange board")
    parser.add_argument("--imported", type=Path, required=True, help="Normalized manifest exported by the EasyEDA bridge after import")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_path, imported_path = args.source.resolve(), args.imported.resolve()
    source_manifest = load_manifest(source_path)
    imported_manifest = load_manifest(imported_path)
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
        "limits": "Manifests must be produced by reviewed deterministic adapters; this receipt does not prove DRC or manufacturing readiness.",
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
