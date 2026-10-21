#!/usr/bin/env python3
"""Validate the minimum structured input required before electrical capture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from validate_spec_consistency import consistency_problems


ROOT = Path(__file__).resolve().parents[1]


def problems(data: dict[str, object], require_reviewed: bool = False) -> list[str]:
    issues: list[str] = []
    for key in ("interfaces", "rails", "connectors", "constraints", "unknowns", "critical_nets", "placement_regions"):
        value = data.get(key)
        if not isinstance(value, list):
            issues.append(f"{key} must be a list")
    mechanical = data.get("mechanical")
    if not isinstance(mechanical, dict):
        issues.append("mechanical must be an object")
    meaningful = sum(len(data.get(key, [])) for key in ("interfaces", "rails", "connectors", "constraints", "unknowns") if isinstance(data.get(key), list))
    if meaningful == 0:
        issues.append("record at least one requirement or explicit unknown")
    if data.get("schema") != 2:
        issues.append("requirements schema must be 2")
    for key in ("critical_nets", "placement_regions"):
        value = data.get(key, [])
        if isinstance(value, list) and any(not isinstance(item, dict) for item in value):
            issues.append(f"{key} entries must be objects")
    if require_reviewed:
        if data.get("status") != "reviewed":
            issues.append("requirements status must be reviewed before the physical stage")
        unknowns = data.get("unknowns", [])
        if isinstance(unknowns, list):
            blocking = [item for item in unknowns if isinstance(item, str) or (isinstance(item, dict) and item.get("blocks") not in (None, [], False))]
            if blocking:
                issues.append("resolve or explicitly clear blocking unknowns before the physical stage")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, default=ROOT / "config/requirements.json")
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--output", type=Path, help="Write the deterministic JSON receipt to this path.")
    args = parser.parse_args()
    try:
        data = json.loads(args.requirements.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Requirements file is unavailable or invalid: {args.requirements}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Requirements file must contain a JSON object")
    issues = problems(data, args.require_reviewed)
    if args.require_reviewed and args.requirements.resolve() == (ROOT / "config/requirements.json").resolve():
        issues.extend(consistency_problems(ROOT))
    receipt = {
        "schema": 1, "kind": "requirements_validation", "success": not issues,
        "requirements": str(args.requirements.resolve()),
        "requirements_sha256": hashlib.sha256(args.requirements.resolve().read_bytes()).hexdigest().upper(),
        "status": data.get("status"), "issues": issues[:8],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
