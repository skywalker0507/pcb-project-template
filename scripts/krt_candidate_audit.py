#!/usr/bin/env python3
"""Generic before/after KiCad DRC gate for routed candidate boards."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


from pcb_runtime import kicad_cli

DRC_CACHE_CONTRACT = "krt-candidate-audit-v1|json|severity-all"
DEFAULT_KICAD_CLI = kicad_cli()


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def optional_sha256(path: Path) -> str | None:
    return sha256(path) if path.is_file() else None


def drc_context_key(kicad_cli: Path, board: Path) -> tuple[str, str, str | None]:
    version = subprocess.run(
        [str(kicad_cli), "version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    ).stdout.strip()
    project = board.with_suffix(".kicad_pro")
    project_hash = optional_sha256(project)
    context_hashes = [
        project_hash,
        optional_sha256(board.with_suffix(".kicad_dru")),
        optional_sha256(board.parent / "fp-lib-table"),
        optional_sha256(board.parent / "sym-lib-table"),
    ]
    payload = "|".join((
        DRC_CACHE_CONTRACT, sha256(board),
        *(value or "" for value in context_hashes), version,
    ))
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()[:12]
    return key, version, project_hash


def run_drc(kicad_cli: Path, board: Path, output: Path) -> tuple[dict[str, Any], bool]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_file():
        return json.loads(output.read_text(encoding="utf-8")), True
    temporary = output.with_name(f"{output.name}.{os.getpid()}.tmp")
    command = [
        str(kicad_cli), "pcb", "drc", "--format", "json",
        "--severity-all", "--output", str(temporary), str(board),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if not temporary.is_file():
        raise SystemExit(compact({
            "success": False,
            "error": "kicad_drc_failed",
            "exit_code": result.returncode,
            "stderr": result.stderr[-500:],
        }))
    try:
        temporary.replace(output)
    except FileExistsError:
        temporary.unlink(missing_ok=True)
    return json.loads(output.read_text(encoding="utf-8")), False


def category_counts(report: dict[str, Any]) -> Counter[str]:
    counts = Counter(item.get("type", "unknown") for item in report.get("violations", []))
    counts["unconnected_items"] = len(report.get("unconnected_items", []))
    return counts


def item_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    description = str(item.get("description") or "")
    track = re.fullmatch(r"Track \[(.+)] on ([^,]+), length [0-9.]+ mm", description)
    if track:
        # KiCad may choose a different one of two overlapping, collinear track
        # objects after an unrelated board save. Preserve the physical
        # net/layer pair in the fingerprint without treating segment length,
        # start position, or UUID selection as a new DRC violation.
        return ("track", track.group(1), track.group(2))
    # KiCad embeds the current net name in pad descriptions. A reviewed
    # electrical rebase may rename a pad net while leaving the exact same DRC
    # geometry at the exact same UUID. Do not misclassify that existing
    # physical violation as newly introduced.
    description = re.sub(r"\s+\[[^\]]*\]\s*$", "", description)
    uuid = item.get("uuid")
    if uuid:
        # Footprint graphics, pads, and fields retain their UUID when a
        # footprint moves. Their absolute coordinates must not turn the same
        # inherited violation into a new one. A genuinely new collision still
        # has a new item-pair fingerprint, while changed clearance details
        # remain represented by the parent violation description.
        return ("uuid", uuid, description)
    pos = item.get("pos") or {}
    return (
        None,
        description,
        round(float(pos.get("x", 0)), 4),
        round(float(pos.get("y", 0)), 4),
    )


def violation_identity(violation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        violation.get("type", "unknown"),
        violation.get("severity"),
        violation.get("description"),
        tuple(sorted(item_identity(item) for item in violation.get("items", []))),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument(
        "--kicad-cli", type=Path, default=DEFAULT_KICAD_CLI,
        help=f"KiCad CLI executable (default: {DEFAULT_KICAD_CLI})",
    )
    parser.add_argument("--work-dir", type=Path, default=Path("build/06-routing/tmp/manual/drc"))
    parser.add_argument("--cache-dir", type=Path, default=Path("build/06-routing/tmp/drc-cache"))
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
    cache = args.cache_dir.resolve()
    source_hash = sha256(source)
    candidate_hash = sha256(candidate)
    source_context, kicad_version, source_project_hash = drc_context_key(kicad_cli, source)
    candidate_context, _, candidate_project_hash = drc_context_key(kicad_cli, candidate)
    source_json = cache / f"board-{source_context}.drc.json"
    candidate_json = cache / f"board-{candidate_context}.drc.json"
    drc_started = time.perf_counter()
    source_drc, source_cache_hit = run_drc(kicad_cli, source, source_json)
    source_drc_ms = round((time.perf_counter() - drc_started) * 1000, 1)
    drc_started = time.perf_counter()
    candidate_drc, candidate_cache_hit = run_drc(kicad_cli, candidate, candidate_json)
    candidate_drc_ms = round((time.perf_counter() - drc_started) * 1000, 1)
    source_counts = category_counts(source_drc)
    candidate_counts = category_counts(candidate_drc)

    source_fingerprints = {
        violation_identity(item) for item in source_drc.get("violations", [])
    }
    new_violations = [
        item for item in candidate_drc.get("violations", [])
        if violation_identity(item) not in source_fingerprints
    ]
    new_violation_types = Counter(str(item.get("type", "unknown")) for item in new_violations)
    new_violation_signature = hashlib.sha256(
        json.dumps(sorted(violation_identity(item) for item in new_violations), separators=(",", ":")).encode()
    ).hexdigest()[:16] if new_violations else None
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
        "new_violation_types": dict(sorted(new_violation_types.items())),
        "new_violation_signature": new_violation_signature,
        "input_drc_json": str(source_json),
        "candidate_drc_json": str(candidate_json),
        "drc_cache": {
            "directory": str(cache),
            "input_hit": source_cache_hit,
            "candidate_hit": candidate_cache_hit,
        },
        "phase_ms": {
            "input_drc": source_drc_ms,
            "candidate_drc": candidate_drc_ms,
        },
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
        "new_violation_types": dict(sorted(new_violation_types.items())),
        "new_violation_signature": new_violation_signature,
        "new_violation_preview": preview,
        "drc_cache_hits": {"input": source_cache_hit, "candidate": candidate_cache_hit},
        "phase_ms": full_report["phase_ms"],
        "report": str(report_path),
    }))
    return 0 if accepted else 2


if __name__ == "__main__":
    sys.exit(main())
