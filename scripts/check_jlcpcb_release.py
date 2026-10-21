#!/usr/bin/env python3
"""Inspect a generated JLCPCB package without treating it as release approval."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STACK = ROOT / "config/board-stack.json"


def read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def csv_rows(path: Path, required: set[str]) -> tuple[int, list[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            rows = list(reader)
    except OSError:
        return 0, [f"cannot read {path.name}"]
    problems = [] if required.issubset(fields) else [
        f"{path.name} lacks columns: {', '.join(sorted(required - fields))}"
    ]
    return len(rows), problems


def package_report(package: Path, stack_path: Path, gerber_rules_path: Path | None = None) -> dict[str, object]:
    # stack_path remains in the compatibility API, but Gerber layer/file rules
    # are exclusively compiled into and evaluated by the manufacturing registry.
    _ = stack_path
    assembly = package / "assembly"
    gerber_rules = read_json(gerber_rules_path, "Manufacturing registry Gerber receipt") if gerber_rules_path and gerber_rules_path.is_file() else {}
    gerber_registry_ok = bool(
        gerber_rules.get("kind") == "manufacturing_registry_gerber_gate"
        and gerber_rules.get("success") is True
        and gerber_rules.get("manufacturing_registry_pass") is True
        and gerber_rules.get("gerber_rules_pass") is True
    )
    bom = assembly / "BOM_JLCPCB.csv"
    cpl = assembly / "CPL_JLCPCB.csv"
    bom_rows, bom_problems = csv_rows(bom, {"Comment", "Designator", "Footprint", "LCSC Part #"})
    cpl_rows, cpl_problems = csv_rows(cpl, {"Designator", "Mid X", "Mid Y", "Layer", "Rotation"})
    validation_path = assembly / "assembly-validation.json"
    validation = read_json(validation_path, "Assembly validation") if validation_path.is_file() else {}
    match = validation.get("bom_cpl_designators_match")
    match_ok = isinstance(match, dict) and not match.get("missing_from_cpl") and not match.get("extra_in_cpl")
    checks = {
        "gerber_files_present": gerber_registry_ok,
        "manufacturing_registry_gerber_pass": gerber_registry_ok,
        "bom_columns_valid": not bom_problems,
        "cpl_columns_valid": not cpl_problems,
        "bom_cpl_designators_match": match_ok,
    }
    failures = [name for name, ok in checks.items() if not ok]
    return {
        "success": not failures,
        "kind": "package_sanity_check",
        "package": str(package.resolve()),
        "checks": checks,
        "failures": failures,
        "details": {
            "gerber_registry_receipt": str(gerber_rules_path.resolve()) if gerber_rules_path else None,
            "gerber_registry_sha256": gerber_rules.get("registry_sha256"),
            "gerber_rule_results": [
                {"id": item.get("id"), "kind": item.get("kind"), "pass": item.get("pass")}
                for item in gerber_rules.get("rules", []) if isinstance(item, dict)
            ],
            "bom_rows": bom_rows,
            "cpl_rows": cpl_rows,
            "problems": bom_problems + cpl_problems,
        },
        "plain_language": (
            "Package structure is consistent, but this is not manufacturing approval; require release_pass and physical review."
            if not failures else
            "Do not upload this package: " + ", ".join(failures)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True, help="JLCPCB export directory")
    parser.add_argument("--board-stack", type=Path, default=DEFAULT_STACK)
    parser.add_argument("--gerber-rules", type=Path, help="Passing manufacturing registry Gerber receipt")
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    report = package_report(
        args.package.resolve(), args.board_stack.resolve(),
        args.gerber_rules.resolve() if args.gerber_rules else None,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["success"] else 2


if __name__ == "__main__":
    sys.exit(main())
