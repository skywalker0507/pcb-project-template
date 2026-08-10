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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True, help="Compact deterministic KiCad candidate manifest")
    parser.add_argument("--requirements", type=Path, default=ROOT / "config/requirements.json")
    parser.add_argument("--product-decisions", type=Path, default=ROOT / "config/product-decisions.json")
    parser.add_argument("--selected-parts", type=Path, default=ROOT / "config/selected-parts.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt_path, manifest_path = args.receipt.resolve(), args.manifest.resolve()
    receipt = load(receipt_path, "Build receipt")
    manifest = load(manifest_path, "Candidate manifest")
    requirements = load(args.requirements.resolve(), "Requirements")
    decisions = load(args.product_decisions.resolve(), "Product decisions")
    selected_parts = load(args.selected_parts.resolve(), "Selected parts")
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
    expected_refs = {
        reference for part in selected_parts.get("parts", []) if isinstance(part, dict)
        for reference in part.get("refs", []) if isinstance(reference, str)
    }
    missing_nets = sorted(critical - nets)
    missing_refs = sorted(expected_refs - refs)
    decision_issues: list[str] = []
    if requirements.get("status") != "reviewed":
        decision_issues.append("requirements status is not reviewed")
    if decisions.get("status") != "confirmed":
        decision_issues.append("product decision status is not confirmed")
    if selected_parts.get("status") not in {"reviewed", "release_review"}:
        decision_issues.append("selected-parts status is not reviewed")
    success = not missing_nets and not missing_refs and not decision_issues
    payload = {
        "schema": 1,
        "kind": "electrical_contract_validation",
        "success": success,
        "contract_pass": success,
        "backend": receipt.get("backend"),
        "candidate_board": str(board),
        "candidate_board_sha256": board_hash,
        "manifest_sha256": sha256(manifest_path),
        "critical_nets_checked": sorted(critical),
        "missing_critical_nets": missing_nets,
        "selected_references_checked": len(expected_refs),
        "missing_selected_references": missing_refs,
        "record_issues": decision_issues,
        "limits": "Exact part identities remain bound to the reviewed selected-parts record; this check does not replace ERC, DRC, or candidate review.",
    }
    if args.output:
        args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
