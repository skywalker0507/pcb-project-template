#!/usr/bin/env python3
"""Validate a backend-neutral electrical candidate against reviewed records.

The manifest is a compact deterministic extraction from the generated KiCad
candidate. Backend-private artifacts such as tscircuit Circuit JSON are never
part of this contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from check_easyeda_import_parity import validate_manifest
from validate_requirements import problems as requirements_problems


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def manifest_net_names(manifest: dict[str, object]) -> set[str]:
    values = manifest.get("nets")
    if not isinstance(values, list):
        raise SystemExit("Candidate manifest needs a nets list")
    result: set[str] = set()
    for item in values:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name:
            result.add(name)
    return result


def manifest_refs(manifest: dict[str, object]) -> set[str]:
    values = manifest.get("components")
    if not isinstance(values, list):
        raise SystemExit("Candidate manifest needs a components list")
    refs: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            reference = item.get("reference", item.get("ref"))
            if isinstance(reference, str) and reference:
                refs.add(reference)
    return refs


def input_record(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": sha256(path.resolve())}


def expected_part_map(record: dict[str, object], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in record.get("parts", []):
        if not isinstance(part, dict) or not isinstance(part.get("mpn"), str):
            continue
        for reference in part.get("refs", []):
            if not isinstance(reference, str):
                continue
            previous = result.get(reference)
            if previous is not None and previous != part["mpn"]:
                raise SystemExit(
                    f"{label} assigns conflicting MPNs to {reference}: "
                    f"{previous!r} and {part['mpn']!r}"
                )
            result[reference] = part["mpn"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True, help="Compact deterministic KiCad candidate manifest")
    parser.add_argument("--requirements", type=Path, default=ROOT / "config/requirements.json")
    parser.add_argument("--product-decisions", type=Path, default=ROOT / "config/product-decisions.json")
    parser.add_argument("--selected-parts", type=Path, default=ROOT / "config/selected-parts.json")
    parser.add_argument("--assembly-parts", type=Path, default=ROOT / "config/jlcpcb-parts.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt_path, manifest_path = args.receipt.resolve(), args.manifest.resolve()
    receipt = load(receipt_path, "Build receipt")
    manifest = load(manifest_path, "Candidate manifest")
    validate_manifest(manifest, str(manifest_path))
    requirements = load(args.requirements.resolve(), "Requirements")
    decisions = load(args.product_decisions.resolve(), "Product decisions")
    selected_parts = load(args.selected_parts.resolve(), "Selected parts")
    assembly_parts = load(args.assembly_parts.resolve(), "Assembly parts")
    if receipt.get("success") is not True:
        raise SystemExit("Build receipt is not successful")
    board = Path(str(receipt.get("candidate_board", ""))).resolve()
    if not board.is_file():
        raise SystemExit("Build receipt candidate_board is unavailable")
    board_hash = sha256(board)
    manifest_board = manifest.get("board")
    if isinstance(manifest_board, str) and Path(manifest_board).resolve() != board:
        raise SystemExit("Candidate manifest references a different board")
    if manifest.get("board_sha256") != board_hash:
        raise SystemExit("Candidate manifest does not match the candidate board SHA-256")

    nets = manifest_net_names(manifest)
    refs = manifest_refs(manifest)
    critical = {
        entry["net"] for entry in requirements.get("critical_nets", [])
        if isinstance(entry, dict) and isinstance(entry.get("net"), str)
    }
    selected_expected = expected_part_map(selected_parts, "Selected parts")
    assembly_expected = expected_part_map(assembly_parts, "Assembly parts")
    expected_parts = dict(assembly_expected)
    identity_record_conflicts = sorted(
        reference for reference in selected_expected.keys() & assembly_expected.keys()
        if selected_expected[reference] != assembly_expected[reference]
    )
    expected_parts.update(selected_expected)
    expected_refs = set(expected_parts)
    manifest_components = {
        str(item.get("reference", item.get("ref"))): item
        for item in manifest.get("components", []) if isinstance(item, dict)
    }
    actual_pad_nets = {
        f"{reference}:{pad.get('number')}": pad.get("net")
        for reference, component in manifest_components.items()
        for pad in component.get("pads", [])
        if isinstance(pad, dict) and pad.get("number") not in {None, ""}
    }
    critical_pad_mismatches = []
    for entry in requirements.get("critical_nets", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("net"), str):
            continue
        for pad_key in entry.get("required_pads", []):
            if not isinstance(pad_key, str) or actual_pad_nets.get(pad_key) != entry["net"]:
                critical_pad_mismatches.append({
                    "pad": pad_key,
                    "expected_net": entry["net"],
                    "actual_net": actual_pad_nets.get(pad_key) if isinstance(pad_key, str) else None,
                })
    missing_nets = sorted(critical - nets)
    missing_refs = sorted(expected_refs - refs)
    part_identity_mismatches = sorted(
        reference for reference in expected_refs & refs
        if manifest_components[reference].get("part_identity") != expected_parts[reference]
    )
    decision_issues: list[str] = []
    if identity_record_conflicts:
        decision_issues.append(
            "selected-parts and assembly-parts disagree for: "
            + ", ".join(identity_record_conflicts)
        )
    decision_issues.extend(
        f"requirements: {issue}" for issue in requirements_problems(requirements, require_reviewed=True, gate="electrical_freeze")
    )
    if decisions.get("status") != "confirmed":
        decision_issues.append("product decision status is not confirmed")
    if selected_parts.get("status") not in {"electrical_review", "reviewed", "release_review"}:
        decision_issues.append("selected-parts status is not reviewed")
    if assembly_parts.get("status") not in {"electrical_review", "reviewed", "release_review"}:
        decision_issues.append("assembly-parts status is not reviewed")
    exempt_refs = {
        ref for ref in requirements.get("assembly_exempt_refs", []) if isinstance(ref, str)
    }
    dnp_refs = {
        ref for ref in assembly_parts.get("dnp_refs", []) if isinstance(ref, str)
    }
    unexpected_refs = sorted(refs - set(assembly_expected) - exempt_refs - dnp_refs)
    success = (
        not missing_nets
        and not critical_pad_mismatches
        and not missing_refs
        and not part_identity_mismatches
        and not unexpected_refs
        and not decision_issues
    )
    inputs = {
        "requirements": input_record(args.requirements.resolve()),
        "product_decisions": input_record(args.product_decisions.resolve()),
        "selected_parts": input_record(args.selected_parts.resolve()),
        "assembly_parts": input_record(args.assembly_parts.resolve()),
    }
    payload = {
        "schema": 1,
        "kind": "electrical_contract_validation",
        "success": success,
        "contract_pass": success,
        "backend": receipt.get("backend"),
        "candidate_board": str(board),
        "candidate_board_sha256": board_hash,
        "manifest_sha256": sha256(manifest_path),
        "manifest": str(manifest_path),
        "inputs": inputs,
        "critical_nets_checked": sorted(critical),
        "missing_critical_nets": missing_nets,
        "critical_pad_mismatches": critical_pad_mismatches,
        "selected_references_checked": len(selected_expected),
        "assembly_references_checked": len(assembly_expected),
        "total_exact_references_checked": len(expected_refs),
        "missing_selected_references": missing_refs,
        "unexpected_non_exempt_references": unexpected_refs,
        "assembly_exempt_references": sorted(exempt_refs),
        "part_identity_mismatches": part_identity_mismatches,
        "record_issues": decision_issues,
        "limits": "Exact identities are bound to both reviewed selected-parts and assembly-parts records; this check does not replace ERC, DRC, or candidate review.",
    }
    if args.output:
        args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
