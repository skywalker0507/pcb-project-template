#!/usr/bin/env python3
"""Check that a tscircuit candidate still covers the reviewed product contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MPNS = {
    "ESP32-S3-WROOM-1-N16R8", "AP63203WU-7", "TMUX1574PWR",
    "TLV803EA26RDBZR", "USBLC6-2P6", "USB4105-GF-A",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, default=ROOT / "config/requirements.json")
    parser.add_argument("--product-decisions", type=Path, default=ROOT / "config/product-decisions.json")
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    if receipt.get("success") is not True:
        raise SystemExit("Build receipt is not successful")
    circuit_path = Path(str(receipt.get("circuit_json", "")))
    circuit = json.loads(circuit_path.read_text(encoding="utf-8"))
    requirements = json.loads(args.requirements.read_text(encoding="utf-8"))
    decisions = json.loads(args.product_decisions.read_text(encoding="utf-8"))
    nets = {item.get("name") for item in circuit if item.get("type") == "source_net"}
    mpns = {item.get("manufacturer_part_number") for item in circuit if item.get("type") == "source_component"}
    critical = {entry["net"] for entry in requirements.get("critical_nets", []) if isinstance(entry, dict) and isinstance(entry.get("net"), str)}
    missing_nets = sorted(critical - nets)
    missing_mpns = sorted(REQUIRED_MPNS - mpns)
    required_decisions = {
        "initial_target_family": "STM32",
        "target_power": "The probe must never power the target; the target is self-powered.",
    }
    decision_values = decisions.get("decisions", {})
    decision_issues = [
        key for key, value in required_decisions.items()
        if decision_values.get(key) != value
    ]
    connectors = decision_values.get("target_connectors", [])
    if not {"J2", "J3"}.issubset(connectors):
        decision_issues.append("target_connectors must retain J2 and J3")
    if decisions.get("status") != "confirmed":
        decision_issues.append("product decision status is not confirmed")
    payload = {
        "success": not missing_nets and not missing_mpns and not decision_issues,
        "receipt": str(args.receipt.resolve()),
        "critical_nets_checked": sorted(critical),
        "missing_critical_nets": missing_nets,
        "missing_required_mpns": missing_mpns,
        "product_decisions": str(args.product_decisions.resolve()),
        "product_decision_issues": decision_issues,
    }
    print(json.dumps(payload, separators=(",", ":")))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
