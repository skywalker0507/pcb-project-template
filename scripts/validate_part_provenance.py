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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", type=Path, default=ROOT / "config/selected-parts.json")
    parser.add_argument("--require-electrical-review", action="store_true")
    args = parser.parse_args()
    try:
        data = json.loads(args.parts.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read provenance record: {exc}") from exc
    result = issues(data, args.require_electrical_review)
    print(json.dumps({
        "schema": 1, "kind": "part_provenance_validation", "success": not result,
        "parts": str(args.parts.resolve()),
        "parts_sha256": hashlib.sha256(args.parts.resolve().read_bytes()).hexdigest().upper(),
        "issues": result[:8],
    }, separators=(",", ":")))
    return 0 if not result else 1


if __name__ == "__main__":
    raise SystemExit(main())
