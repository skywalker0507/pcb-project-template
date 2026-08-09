#!/usr/bin/env python3
"""Classify repeated routing-candidate failures that require physical re-planning.

The result is a compact escalation receipt, not a DRC waiver or a routing
decision.  It prevents repeated coordinate tweaks after independent candidates
show that board edge, courtyard, or drilled-hole constraints are the blocker.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


STRUCTURAL = {"board_edge_clearance", "courtyards_overlap", "npth_inside_courtyard", "hole_clearance"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="append", type=Path, required=True, help="Candidate DRC comparison JSON; repeat as needed.")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Output already exists: {args.output}")
    reports = []
    for path in args.audit:
        try:
            data = json.loads(path.resolve().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Invalid candidate audit {path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("success") is not False:
            raise SystemExit(f"Escalation input must be a rejected candidate audit: {path}")
        types = [str(item.get("type", "unknown")) for item in data.get("new_violations", []) if isinstance(item, dict)]
        reports.append({"audit": str(path.resolve()), "candidate": data.get("candidate"), "types": types})
    counts = Counter(kind for report in reports for kind in report["types"])
    structural_reports = [report for report in reports if STRUCTURAL.intersection(report["types"])]
    result = {
        "schema": 1,
        "kind": "routing_escalation",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope,
        "rejected_candidates": reports,
        "violation_type_counts": dict(sorted(counts.items())),
        "structural_failure_count": len(structural_reports),
        "requires_physical_replan": len(structural_reports) >= 2,
        "limits": [
            "This receipt classifies repeated failed candidates only.",
            "It does not waive DRC, approve a layout, or authorize a product-outline change.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": True, "requires_physical_replan": result["requires_physical_replan"], "output": str(args.output.resolve())}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
