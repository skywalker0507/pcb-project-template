#!/usr/bin/env python3
"""Generic before/after KiCad DRC gate for routed candidate boards."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def drc_context_key(kicad_cli: Path, board: Path) -> tuple[str, str, str | None]:
    version = subprocess.run(
        [str(kicad_cli), "version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    ).stdout.strip()
    project = board.with_suffix(".kicad_pro")
    project_hash = sha256(project) if project.is_file() else None
    payload = "|".join((sha256(board), project_hash or "", version))
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()[:12]
    return key, version, project_hash


def run_drc(kicad_cli: Path, board: Path, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_file():
        return json.loads(output.read_text(encoding="utf-8"))
    command = [
        str(kicad_cli), "pcb", "drc", "--format", "json",
        "--severity-all", "--output", str(output), str(board),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if not output.is_file():
        raise SystemExit(compact({
            "success": False,
            "error": "kicad_drc_failed",
            "exit_code": result.returncode,
            "stderr": result.stderr[-500:],
        }))
    return json.loads(output.read_text(encoding="utf-8"))


def category_counts(report: dict[str, Any]) -> Counter[str]:
    counts = Counter(item.get("type", "unknown") for item in report.get("violations", []))
    counts["unconnected_items"] = len(report.get("unconnected_items", []))
    return counts


def item_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    pos = item.get("pos") or {}
    return (
        item.get("uuid"),
        item.get("description"),
        round(float(pos.get("x", 0)), 4),
        round(float(pos.get("y", 0)), 4),
    )


def violation_identity(violation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        violation.get("type", "unknown"),
        violation.get("severity"),
        tuple(sorted(item_identity(item) for item in violation.get("items", []))),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--kicad-cli", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path, default=Path("build/reports/krt/drc"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-preview", type=int, default=6)
    args = parser.parse_args()

    source = args.input.resolve()
    candidate = args.candidate.resolve()
    kicad_cli = args.kicad_cli.resolve()
    for path, label in ((source, "input"), (candidate, "candidate"), (kicad_cli, "kicad_cli")):
        if not path.is_file():
            raise SystemExit(f"{label} not found: {path}")
    work = args.work_dir.resolve()
    source_hash = sha256(source)
    candidate_hash = sha256(candidate)
    source_context, kicad_version, source_project_hash = drc_context_key(kicad_cli, source)
    candidate_context, _, candidate_project_hash = drc_context_key(kicad_cli, candidate)
    source_json = work / f"input-{source_context}.drc.json"
    candidate_json = work / f"candidate-{candidate_context}.drc.json"
    source_drc = run_drc(kicad_cli, source, source_json)
    candidate_drc = run_drc(kicad_cli, candidate, candidate_json)
    source_counts = category_counts(source_drc)
    candidate_counts = category_counts(candidate_drc)

    source_fingerprints = {
        violation_identity(item) for item in source_drc.get("violations", [])
    }
    new_violations = [
        item for item in candidate_drc.get("violations", [])
        if violation_identity(item) not in source_fingerprints
    ]
    unconnected_increase = max(
        0, candidate_counts["unconnected_items"] - source_counts["unconnected_items"]
    )
    accepted = not new_violations and unconnected_increase == 0
    full_report = {
        "schema": 1,
        "success": accepted,
        "baseline_pass": accepted,
        "input": str(source),
        "candidate": str(candidate),
        "input_sha256": source_hash,
        "candidate_sha256": candidate_hash,
        "kicad_version": kicad_version,
        "input_project_sha256": source_project_hash,
        "candidate_project_sha256": candidate_project_hash,
        "input_counts": dict(sorted(source_counts.items())),
        "candidate_counts": dict(sorted(candidate_counts.items())),
        "unconnected_delta": (
            candidate_counts["unconnected_items"] - source_counts["unconnected_items"]
        ),
        "new_violations": new_violations,
        "input_drc_json": str(source_json),
        "candidate_drc_json": str(candidate_json),
    }
    report_path = args.report.resolve() if args.report else work / f"compare-{candidate_hash[:12]}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(full_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    preview = [
        {
            "type": item.get("type"),
            "severity": item.get("severity"),
            "description": item.get("description"),
        }
        for item in new_violations[: max(0, args.max_preview)]
    ]
    print(compact({
        "success": accepted,
        "baseline_pass": accepted,
        "board_sha256": candidate_hash,
        "unconnected_before": source_counts["unconnected_items"],
        "unconnected_after": candidate_counts["unconnected_items"],
        "new_violation_count": len(new_violations),
        "new_violation_preview": preview,
        "report": str(report_path),
    }))
    return 0 if accepted else 2


if __name__ == "__main__":
    sys.exit(main())
