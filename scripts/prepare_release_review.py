#!/usr/bin/env python3
"""Prepare exact-board deterministic evidence for one release review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from artifact_store import create_run, finish_run, relative
from audit_kicad_release import FATAL_CATEGORIES
from record_kicad_drc_review import REVIEWABLE
from release_inputs import (
    adopted_routing_board,
    dependencies_fresh,
    dependency,
    load_json,
    register_release_input,
    resolve_path,
    resolve_release_input,
    sha256,
)


ROOT = Path(__file__).resolve().parents[1]


def reviewed_placement_baseline(flow: dict[str, Any], explicit: Path | None) -> Path:
    if explicit is not None:
        baseline = explicit.resolve()
        if not baseline.is_file():
            raise SystemExit(f"Placement baseline not found: {baseline}")
        return baseline
    _, review, _ = resolve_release_input(flow, "physical_review")
    if review is None:
        raise SystemExit("No reviewed physical baseline is available; pass --placement-baseline")
    direct = resolve_path(review.get("board"))
    if direct is not None and direct.is_file() and sha256(direct) == review.get("board_sha256"):
        return direct
    evidence = review.get("evidence") or {}
    for value in evidence.get("placement", []) if isinstance(evidence, dict) else []:
        receipt_path = resolve_path(value)
        receipt = load_json(receipt_path) if receipt_path is not None else None
        candidate = resolve_path((receipt or {}).get("candidate"))
        if (
            receipt is not None and receipt.get("kind") == "kicad_placement_semantics"
            and receipt.get("success") is True and candidate is not None and candidate.is_file()
            and sha256(candidate) == receipt.get("candidate_sha256")
        ):
            return candidate
    raise SystemExit("Reviewed physical evidence no longer contains its placement baseline; pass --placement-baseline")


def run_step(
    name: str, command: list[str], log_dir: Path, accepted: set[int] = {0},
) -> tuple[int, Path]:
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{name}.log"
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode not in accepted:
        raise SystemExit(f"Release review preparation failed at {name}; see {relative(log)}")
    return completed.returncode, log


def mismatch_refs(report: str) -> list[str]:
    sections = re.findall(
        r"(?ms)^\[lib_footprint_mismatch\]:.*?(?=^\[[^\]]+\]:|\Z)", report,
    )
    return sorted(set(re.findall(r"Footprint\s+([A-Za-z]+\d+)\b", "\n".join(sections))))


def reusable_packet(flow: dict[str, Any], board: Path) -> tuple[Path, dict[str, Any]] | None:
    path, packet, _ = resolve_release_input(flow, "release_review_packet")
    if (
        path is None or packet is None or packet.get("kind") != "release_review_packet"
        or packet.get("success") is not True or packet.get("board_sha256") != sha256(board)
        or not dependencies_fresh(packet.get("producer_dependencies"))
    ):
        return None
    for name, digest in (packet.get("artifact_sha256") or {}).items():
        artifact = resolve_path((packet.get("artifacts") or {}).get(name))
        if artifact is None or not artifact.is_file() or sha256(artifact) != digest:
            return None
    return path, packet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path)
    parser.add_argument("--placement-baseline", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    board = args.board.resolve() if args.board else adopted_routing_board()
    adopted = adopted_routing_board()
    if board != adopted or sha256(board) != sha256(adopted):
        raise SystemExit("Release review preparation accepts only the adopted routing artifact")
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    existing = reusable_packet(flow, board)
    if existing is not None and not args.refresh:
        path, packet = existing
        print(json.dumps({
            "success": True, "reused": True, "board_sha256": packet["board_sha256"],
            "packet": relative(path), "visual_bundle": (packet.get("artifacts") or {}).get("visual_bundle"),
            "assessment_template": (packet.get("artifacts") or {}).get("assessment_template"),
            "next_command": packet.get("next_command"),
        }, separators=(",", ":")))
        return 0
    baseline = reviewed_placement_baseline(flow, args.placement_baseline)
    run = create_run("release", "release-review-packet", args.run_id)
    logs = run.tmp / "logs"
    drc_report = run.evidence / "drc" / "kicad-cli-drc.rpt"
    audit_path = run.evidence / "pre-review-routing-audit.json"
    footprint_path = run.evidence / "footprint-equivalence.json"
    semantics_path = run.evidence / "placement-semantics.json"
    visual_dir = run.tmp / "visual"
    visual_bundle = visual_dir / "visual-bundle.json"

    audit_config = flow.get("audit") or {}
    audit_code, _ = run_step("release-audit", [
        sys.executable, str(ROOT / "scripts/audit_kicad_release.py"),
        "--board", str(board), "--drc-report", str(drc_report),
        "--audit-json", str(audit_path), "--baseline", str(ROOT / str(audit_config.get("baseline"))),
        "--routing-paths", str(ROOT / str(audit_config.get("routing_paths"))),
        "--require-in1-ground",
    ], logs, {0, 4})
    audit = load_json(audit_path)
    if audit is None:
        raise SystemExit("Release audit did not produce a receipt")
    categories = {str(key): int(value) for key, value in (audit.get("drc_categories") or {}).items()}
    forbidden = sorted(set(categories) - REVIEWABLE)
    missing_zones = list(((audit.get("baseline") or {}).get("missing_required_zones") or []))
    if forbidden or FATAL_CATEGORIES.intersection(categories) or audit.get("unconnected_signatures") or missing_zones:
        raise SystemExit("Release review packet cannot package fatal/non-reviewable DRC or missing required zones")

    report_text = drc_report.read_text(encoding="utf-8", errors="replace")
    refs = mismatch_refs(report_text)
    if refs:
        command = [
            sys.executable, str(ROOT / "scripts/check_kicad_footprint_equivalence.py"),
            "--board", str(board), "--output", str(footprint_path),
        ]
        for ref in refs:
            command.extend(["--ref", ref])
        run_step("footprint-equivalence", command, logs)

    run_step("placement-semantics", [
        sys.executable, str(ROOT / "scripts/check_kicad_placement_semantics.py"),
        "--input", str(baseline), "--candidate", str(board), "--output", str(semantics_path),
    ], logs)
    run_step("release-visual-bundle", [
        sys.executable, str(ROOT / "scripts/render_kicad_visual_bundle.py"),
        "--board", str(board), "--tier", "release", "--output-dir", str(visual_dir),
    ], logs)

    assessment_template = run.evidence / "visual-assessment-template.json"
    assessment_template.write_text(json.dumps({
        "schema": 1,
        "kind": "pcb_visual_assessment",
        "verdict": "REPLACE_WITH_pass_revise_or_reject",
        "ratings": {
            "layout": None, "critical_routing": None, "return_paths": None,
            "silkscreen": None, "manufacturability": None,
        },
        "findings": [],
        "summary": "Replace placeholders only after inspecting every image in the bound release visual bundle.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts = {
        "drc_report": relative(drc_report),
        "audit": relative(audit_path),
        "footprint_equivalence": relative(footprint_path) if footprint_path.is_file() else None,
        "placement_semantics": relative(semantics_path),
        "visual_bundle": relative(visual_bundle),
        "assessment_template": relative(assessment_template),
    }
    packet = {
        "schema": 1, "kind": "release_review_packet", "success": True,
        "ready_for_assessment": True,
        "board": relative(board), "board_sha256": sha256(board),
        "placement_baseline": relative(baseline), "placement_baseline_sha256": sha256(baseline),
        "drc": {
            "categories": categories, "audit_exit_code": audit_code,
            "baseline_pass": audit.get("baseline_pass"),
            "review_required": audit.get("baseline_pass") is not True,
            "library_mismatch_refs": refs,
        },
        "artifacts": artifacts,
        "artifact_sha256": {
            name: sha256((ROOT / value).resolve())
            for name, value in artifacts.items() if isinstance(value, str)
        },
        "producer_dependencies": {
            item["path"]: item["sha256"] for item in [
                dependency(Path(__file__).resolve()),
                dependency(ROOT / "scripts/audit_kicad_release.py"),
                dependency(ROOT / "scripts/check_kicad_footprint_equivalence.py"),
                dependency(ROOT / "scripts/check_kicad_placement_semantics.py"),
                dependency(ROOT / "scripts/render_kicad_visual_bundle.py"),
            ]
        },
        "next_action": "inspect_visual_bundle_and_complete_assessment",
        "next_command": (
            f'py -3.14 scripts/pcbctl.py verify finalize-review --packet "{relative(run.evidence / "release-review-packet.json")}" '
            '--assessment "<completed-visual-assessment.json>"'
        ),
        "limits": "This packet prepares deterministic evidence only. It cannot create a visual verdict, DRC waiver, physical approval, or manufacturing release.",
    }
    packet_path = run.evidence / "release-review-packet.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_release_input("release_review_packet", packet_path, packet)
    finish_run(
        run, True,
        artifacts={"packet": relative(packet_path), **artifacts},
        metrics={"board_sha256": packet["board_sha256"], "drc_categories": categories, "image_count": 4},
    )
    print(json.dumps({
        "success": True, "board_sha256": packet["board_sha256"],
        "drc_categories": categories, "library_mismatch_refs": refs,
        "packet": relative(packet_path), "visual_bundle": relative(visual_bundle),
        "assessment_template": relative(assessment_template), "next_command": packet["next_command"],
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
