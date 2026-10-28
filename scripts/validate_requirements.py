#!/usr/bin/env python3
"""Validate the minimum structured input required before electrical capture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from validate_spec_consistency import consistency_problems
from engine_runtime import activate

activate()
from pcb_template_engine.requirements import GATES, blocking_unknowns, problems  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, default=ROOT / "config/requirements.json")
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--gate", choices=GATES, default="electrical_freeze")
    parser.add_argument("--output", type=Path, help="Write the deterministic JSON receipt to this path.")
    args = parser.parse_args()
    try:
        data = json.loads(args.requirements.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Requirements file is unavailable or invalid: {args.requirements}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Requirements file must contain a JSON object")
    issues = problems(data, args.require_reviewed, gate=args.gate)
    if args.require_reviewed and args.requirements.resolve() == (ROOT / "config/requirements.json").resolve():
        issues.extend(consistency_problems(ROOT))
    receipt = {
        "schema": 1, "kind": "requirements_validation", "success": not issues,
        "requirements": str(args.requirements.resolve()),
        "requirements_sha256": hashlib.sha256(args.requirements.resolve().read_bytes()).hexdigest().upper(),
        "status": data.get("status"), "gate": args.gate,
        "release_target": data.get("release_target", "production"),
        "blocking_unknowns": blocking_unknowns(data, args.gate),
        "issues": issues[:8],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
