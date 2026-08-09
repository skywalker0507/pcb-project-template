#!/usr/bin/env python3
"""Verify the reviewed buck-controller and inductor selection cannot silently regress."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, default=ROOT / "config/power-component-review.json")
    parser.add_argument("--parts", type=Path, default=ROOT / "config/selected-parts.json")
    args = parser.parse_args()
    try:
        review = json.loads(args.review.read_text(encoding="utf-8"))
        parts = json.loads(args.parts.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read selection data: {exc}") from exc
    failures: list[str] = []
    buck, inductor = review.get("buck"), review.get("inductor")
    if review.get("schema") != 1 or review.get("status") != "electrical_review":
        failures.append("review must be schema 1 with electrical_review status")
    if not isinstance(buck, dict) or not isinstance(inductor, dict):
        failures.append("buck and inductor records are required")
    else:
        load = buck.get("maximum_load_current_a")
        margin = buck.get("inductor_current_margin")
        isat = inductor.get("isat_20_percent_drop_a")
        irms = inductor.get("irms_40c_rise_a")
        if not all(isinstance(value, (int, float)) and value > 0 for value in (load, margin, isat, irms)):
            failures.append("current values must be positive numbers")
        else:
            required = load * margin
            if isat < required:
                failures.append(f"L1 Isat {isat:g} A is below reviewed {required:g} A current margin")
            if irms < load:
                failures.append(f"L1 Irms {irms:g} A is below U3 maximum load {load:g} A")
    by_ref = {ref: item.get("mpn") for item in parts.get("parts", []) if isinstance(item, dict) for ref in item.get("refs", []) if isinstance(ref, str)}
    if isinstance(buck, dict) and by_ref.get(buck.get("ref")) != buck.get("mpn"):
        failures.append("U3 MPN does not match selected-parts.json")
    if isinstance(inductor, dict) and by_ref.get(inductor.get("ref")) != inductor.get("mpn"):
        failures.append("L1 MPN does not match selected-parts.json")
    result = {"success": not failures, "review": str(args.review.resolve()), "failures": failures, "required_inductor_current_a": round(buck["maximum_load_current_a"] * buck["inductor_current_margin"], 4) if not failures and isinstance(buck, dict) else None}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if not failures else 1

if __name__ == "__main__":
    raise SystemExit(main())
