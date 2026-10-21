#!/usr/bin/env python3
"""Create a board-bound, exact-item KiCad DRC baseline from reviewed evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from release_inputs import register_release_input

from audit_kicad_release import FATAL_CATEGORIES, violation_items


ROOT = Path(__file__).resolve().parents[1]
REVIEWABLE = {
    "lib_footprint_mismatch",
    "silk_edge_clearance",
    "silk_overlap",
    "silk_over_copper",
}


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


def categories(report: str) -> Counter[str]:
    return Counter(re.findall(r"(?m)^\[([^\]]+)\]:", report))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--drc-report", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--visual-review", type=Path, required=True)
    parser.add_argument("--footprint-equivalence", type=Path)
    parser.add_argument(
        "--baseline", type=Path, default=ROOT / "config/pcb-release-baseline.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    board = args.board.resolve()
    report_path = args.drc_report.resolve()
    board_hash = sha256(board)
    report = report_path.read_text(encoding="utf-8", errors="replace")
    counts = categories(report)
    forbidden = set(counts) - REVIEWABLE
    if forbidden or FATAL_CATEGORIES.intersection(counts) or counts.get("unconnected_items"):
        raise SystemExit(
            "DRC review may contain only explicitly reviewable cosmetic/library categories; "
            f"found: {sorted(forbidden or FATAL_CATEGORIES.intersection(counts))}"
        )

    audit = load(args.audit.resolve(), "release audit")
    if audit.get("board_sha256") != board_hash:
        raise SystemExit("Release audit is not bound to the reviewed board SHA")
    if Path(str(audit.get("drc_report", ""))).resolve() != report_path:
        raise SystemExit("Release audit is not bound to the reviewed DRC report")
    if audit.get("drc_categories") != dict(counts.most_common()):
        raise SystemExit("Release audit category counts differ from the reviewed report")

    visual = load(args.visual_review.resolve(), "visual review")
    if not (
        visual.get("success") is True
        and visual.get("review_pass") is True
        and visual.get("tier") == "release"
        and visual.get("board_sha256") == board_hash
    ):
        raise SystemExit("A passing release-tier visual review for the exact board is required")

    mismatch_refs = {
        match.group(1)
        for item in violation_items(report, "lib_footprint_mismatch")
        if (match := re.search(r"Footprint\s+([A-Za-z]+\d+)\b", item))
    }
    equivalence = load(args.footprint_equivalence.resolve(), "footprint equivalence") if args.footprint_equivalence else None
    if mismatch_refs and equivalence is None:
        raise SystemExit("Footprint equivalence is required for library mismatch review")
    if equivalence is not None and (
        equivalence.get("success") is not True or equivalence.get("board_sha256") != board_hash
    ):
        raise SystemExit("Footprint equivalence must pass for the exact reviewed board")
    proven = {
        str(item.get("reference"))
        for item in (equivalence or {}).get("footprints", [])
        if isinstance(item, dict)
        and item.get("pad_geometry_equal") is True
        and item.get("critical_rule_area_pass", True) is True
        and item.get("classification") in {
            "nonpad_difference_only",
            "nonpad_difference_with_board_rule_area_compensation",
        }
    }
    if mismatch_refs != proven:
        raise SystemExit(
            "Library mismatch review requires matching non-pad-only equivalence evidence; "
            f"DRC={sorted(mismatch_refs)}, evidence={sorted(proven)}"
        )

    exporter = (ROOT / "scripts/export_jlcpcb_release.py").read_text(encoding="utf-8")
    if counts.get("silk_over_copper") and '"--subtract-soldermask"' not in exporter:
        raise SystemExit("Manufacturing export must explicitly subtract soldermask from silkscreen")

    previous = load(args.baseline.resolve(), "baseline")
    baseline = {
        "reviewed_board_sha256": board_hash,
        "allowed_violation_items": {
            category: violation_items(report, category) for category in sorted(counts)
        },
        "expected_category_counts": dict(sorted(counts.items())),
        "required_zones": previous.get("required_zones", []),
        "review_evidence": {
            "release_audit": str(args.audit.resolve()),
            "visual_review": str(args.visual_review.resolve()),
            "footprint_equivalence": str(args.footprint_equivalence.resolve()) if args.footprint_equivalence else None,
        },
        "notes": [
            "Exact DRC objects only; category-level waivers are forbidden.",
            "The baseline is valid only for reviewed_board_sha256.",
            "Silkscreen over masked copper is clipped by the mandatory manufacturing export option.",
            "Library mismatches are accepted only where pad geometry is equal and differences are non-pad graphics.",
        ],
    }
    args.baseline.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    receipt = {
        "schema": 1,
        "kind": "kicad_drc_review",
        "success": True,
        "board": str(board),
        "board_sha256": board_hash,
        "drc_report": str(report_path),
        "reviewed_categories": dict(sorted(counts.items())),
        "reviewed_item_count": sum(counts.values()),
        "baseline": str(args.baseline.resolve()),
        "baseline_sha256": sha256(args.baseline.resolve()),
        "evidence": baseline["review_evidence"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    register_release_input("kicad_drc_review", args.output.resolve(), receipt)
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
