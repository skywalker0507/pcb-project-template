#!/usr/bin/env python3
"""Validate the compact selected-part provenance record used by electrical review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


def issues(data: object, require_electrical_review: bool) -> list[str]:
    if not isinstance(data, dict):
        return ["record must be a JSON object"]
    failures: list[str] = []
    if data.get("schema") != 1:
        failures.append("schema must be 1")
    if require_electrical_review and data.get("status") != "electrical_review":
        failures.append("status must be electrical_review")
    parts = data.get("parts")
    if not isinstance(parts, list) or not parts:
        return failures + ["parts must be a non-empty list"]
    seen_refs: set[str] = set()
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            failures.append(f"parts[{index}] must be an object")
            continue
        refs = part.get("refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            failures.append(f"parts[{index}].refs must be a non-empty string list")
        else:
            for ref in refs:
                if ref in seen_refs:
                    failures.append(f"duplicate reference {ref}")
                seen_refs.add(ref)
        for field in ("mpn", "manufacturer", "source", "evidence"):
            if not isinstance(part.get(field), str) or not part[field].strip():
                failures.append(f"parts[{index}].{field} must be a non-empty string")
        source = part.get("source")
        if isinstance(source, str) and urlparse(source).scheme not in {"https", "http"}:
            failures.append(f"parts[{index}].source must be an http(s) URL")
    return failures


def cross_file_issues(
    selected: dict[str, object], assembly: dict[str, object], requirements: dict[str, object]
) -> list[str]:
    """Check that a reviewed exact part cannot silently diverge from assembly data."""
    failures: list[str] = []
    parts = selected.get("parts", [])
    selected_by_ref: dict[str, str] = {}
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("refs"), list):
                continue
            for ref in part["refs"]:
                if isinstance(ref, str) and isinstance(part.get("mpn"), str):
                    selected_by_ref[ref] = part["mpn"]

    review = selected.get("footprint_and_polarity_review")
    reviewed_refs = set(review.get("reviewed_refs", [])) if isinstance(review, dict) else set()
    if not isinstance(review, dict) or review.get("status") != "reviewed":
        failures.append("footprint_and_polarity_review.status must be reviewed")
    missing_review = sorted(set(selected_by_ref) - reviewed_refs)
    extra_review = sorted(reviewed_refs - set(selected_by_ref))
    if missing_review:
        failures.append("selected refs missing footprint/polarity review: " + ", ".join(missing_review))
    if extra_review:
        failures.append("footprint/polarity review contains unknown refs: " + ", ".join(extra_review))
    evidence_file = review.get("evidence_file") if isinstance(review, dict) else None
    if not isinstance(evidence_file, str) or not (ROOT / evidence_file).is_file():
        failures.append("footprint/polarity evidence_file must exist")

    assembly_by_ref: dict[str, str] = {}
    assembly_parts = assembly.get("parts", [])
    if isinstance(assembly_parts, list):
        for part in assembly_parts:
            if not isinstance(part, dict) or not isinstance(part.get("refs"), list):
                continue
            for ref in part["refs"]:
                if isinstance(ref, str) and isinstance(part.get("mpn"), str):
                    assembly_by_ref[ref] = part["mpn"]
    exempt = set(requirements.get("assembly_exempt_refs", []))
    for ref, mpn in sorted(selected_by_ref.items()):
        if ref in exempt:
            continue
        if ref not in assembly_by_ref:
            failures.append(f"selected ref {ref} is missing from assembly mapping")
        elif assembly_by_ref[ref] != mpn:
            failures.append(f"MPN mismatch for {ref}: selected={mpn!r} assembly={assembly_by_ref[ref]!r}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", type=Path, default=ROOT / "config/selected-parts.json")
    parser.add_argument("--require-electrical-review", action="store_true")
    parser.add_argument("--assembly", type=Path, default=ROOT / "config/jlcpcb-parts.json")
    parser.add_argument("--requirements", type=Path, default=ROOT / "config/requirements.json")
    parser.add_argument("--output", type=Path, help="Write the deterministic JSON receipt to this path.")
    args = parser.parse_args()
    try:
        data = json.loads(args.parts.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read provenance record: {exc}") from exc
    result = issues(data, args.require_electrical_review)
    if args.require_electrical_review:
        try:
            assembly = json.loads(args.assembly.read_text(encoding="utf-8"))
            requirements = json.loads(args.requirements.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Cannot read cross-file parts evidence: {exc}") from exc
        if not isinstance(assembly, dict) or not isinstance(requirements, dict):
            raise SystemExit("Assembly and requirements records must contain JSON objects")
        result.extend(cross_file_issues(data, assembly, requirements))
    receipt = {
        "schema": 1, "kind": "part_provenance_validation", "success": not result,
        "parts": str(args.parts.resolve()),
        "parts_sha256": hashlib.sha256(args.parts.resolve().read_bytes()).hexdigest().upper(),
        "issues": result[:8],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if not result else 1


if __name__ == "__main__":
    raise SystemExit(main())
