#!/usr/bin/env python3
"""Record a SHA-bound review of declared physical PCB requirements."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from design_policy import DEFAULT_OUTPUT, require_policy
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, default=ROOT / "elec/layout/default/default.kicad_pcb")
    parser.add_argument("--policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=ROOT / "build/reports/physical-review.json")
    parser.add_argument("--placement-evidence", action="append", default=[])
    parser.add_argument("--critical-net-evidence", action="append", default=[])
    parser.add_argument("--return-path-evidence", action="append", default=[])
    args = parser.parse_args()
    require_operation("record_review")
    policy = require_policy("release", args.policy.resolve())
    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    evidence = {
        "placement": [item.strip() for item in args.placement_evidence if item.strip()],
        "critical_nets": [item.strip() for item in args.critical_net_evidence if item.strip()],
        "return_paths": [item.strip() for item in args.return_path_evidence if item.strip()],
    }
    required = ["placement", "return_paths"]
    if policy.get("requirements", {}).get("critical_nets"):
        required.append("critical_nets")
    missing = [name for name in required if not evidence[name]]
    receipt = {
        "schema": 1,
        "kind": "physical_review",
        "review_pass": not missing,
        "board_sha256": sha256(board),
        "policy_sha256": policy["policy_sha256"],
        "required_categories": required,
        "missing_categories": missing,
        "evidence": evidence,
        "note": "Evidence is SHA-bound reviewer evidence, not an automatic physical-quality score.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": receipt["review_pass"], "output": str(args.output.resolve()), "missing_categories": missing, "board_sha256": receipt["board_sha256"]}, separators=(",", ":")))
    return 0 if receipt["review_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
