#!/usr/bin/env python3
"""Classify repeated route failures before any return to placement.

The receipt distinguishes routing-strategy review, corridor review and
placement-review eligibility. It never authorizes a footprint move by itself.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path


STRUCTURAL_DRC = {"board_edge_clearance", "courtyards_overlap", "npth_inside_courtyard", "hole_clearance"}
STRUCTURAL_OUTCOMES = {"structural_no_path_or_incomplete"}


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid routing evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Routing evidence must be a JSON object: {path}")
    return value


def evidence_row(path: Path, data: dict[str, object], source_kind: str) -> dict[str, object]:
    if source_kind == "report":
        outcome = str(data.get("outcome_class", "unknown"))
        audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
        types = dict(audit.get("new_violation_types") or {})
        return {
            "path": str(path.resolve()), "kind": source_kind,
            "route_area": data.get("route_area"), "strategy_signature": data.get("strategy_signature"),
            "strategy_class": data.get("strategy_class"),
            "outcome_class": outcome,
            "structural": data.get("structural_failure") is True or outcome in STRUCTURAL_OUTCOMES or bool(STRUCTURAL_DRC.intersection(types)),
            "new_violation_types": sorted(types),
            "new_violation_signature": audit.get("new_violation_signature"),
        }
    if data.get("success") is not False:
        raise SystemExit(f"Escalation audit input must be rejected: {path}")
    types = [str(item.get("type", "unknown")) for item in data.get("new_violations", []) if isinstance(item, dict)]
    return {
        "path": str(path.resolve()), "kind": source_kind,
        "route_area": None, "strategy_signature": None,
        "outcome_class": "legacy_candidate_drc_regression",
        "structural": bool(STRUCTURAL_DRC.intersection(types)),
        "new_violation_types": sorted(set(types)), "new_violation_signature": None,
    }


def assess(rows: list[dict[str, object]]) -> dict[str, object]:
    structural = [row for row in rows if row["structural"] is True]
    strategies = {str(row["strategy_signature"]) for row in structural if row.get("strategy_signature")}
    no_path = [row for row in structural if row["outcome_class"] in STRUCTURAL_OUTCOMES]
    type_counts = Counter(kind for row in rows for kind in row.get("new_violation_types", []))
    repeated_structural_drc = sorted(kind for kind, count in type_counts.items() if count >= 2 and kind in STRUCTURAL_DRC)
    routing_review = len(structural) >= 2
    independent = len(strategies) >= 2
    corridor_review = routing_review and independent
    placement_review_eligible = independent and (len(no_path) >= 2 or bool(repeated_structural_drc))
    return {
        "routing_review_required": routing_review,
        "corridor_review_required": corridor_review,
        "placement_review_eligible": placement_review_eligible,
        "placement_change_authorized": False,
        "structural_failure_count": len(structural),
        "distinct_strategy_count": len(strategies),
        "repeated_structural_drc_types": repeated_structural_drc,
        "next": (
            "review placement evidence and propose an isolated candidate; do not move footprints without improved routability, semantics and critical probes"
            if placement_review_eligible
            else "try or document a meaningfully distinct routing strategy before considering placement"
            if structural
            else "fix non-structural tool, guardrail, audit or DRC issues within routing"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", type=Path, default=[], help="KRT report JSON; repeat for independent strategies.")
    parser.add_argument("--audit", action="append", type=Path, default=[], help="Legacy rejected candidate audit; cannot prove strategy independence.")
    parser.add_argument("--scope", required=True, help="Stable reviewed route-area name.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Output already exists: {args.output}")
    if len(args.report) + len(args.audit) < 2:
        raise SystemExit("Escalation requires at least two routing evidence records")
    rows = [evidence_row(path, load_json(path), "report") for path in args.report]
    rows.extend(evidence_row(path, load_json(path), "audit") for path in args.audit)
    recorded_areas = {str(row["route_area"]) for row in rows if row.get("route_area")}
    if len(recorded_areas) > 1:
        raise SystemExit("KRT reports belong to different route areas")
    decision = assess(rows)
    result = {
        "schema": 2, "kind": "routing_escalation",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope, "recorded_route_area": next(iter(recorded_areas), None),
        "evidence": rows, **decision,
        "requires_physical_replan": decision["placement_review_eligible"],
        "limits": [
            "This receipt never waives DRC or approves a layout.",
            "Placement review is not permission to move footprints; a changed placement must independently improve routability, pass semantics and pass critical probes.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": True, "routing_review_required": decision["routing_review_required"],
        "corridor_review_required": decision["corridor_review_required"],
        "placement_review_eligible": decision["placement_review_eligible"],
        "placement_change_authorized": False, "output": str(args.output.resolve()),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
