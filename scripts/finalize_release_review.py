#!/usr/bin/env python3
"""Bind an explicit visual assessment to a prepared release review packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from artifact_store import create_run, finish_run, guard_run, relative
from release_inputs import adopted_routing_board, dependencies_fresh, load_json, resolve_path, sha256
from run_pcb_pipeline import checkpoint_dependencies, reusable_step


ROOT = Path(__file__).resolve().parents[1]


def require_current_review_inputs(packet: dict[str, Any], board: Path) -> None:
    if 'checkpoint_inputs' not in packet:
        return  # Legacy packets retain their existing producer/artifact guards.
    baseline = resolve_path(packet.get('placement_baseline'))
    if baseline is None or not baseline.is_file() or sha256(baseline) != packet.get('placement_baseline_sha256'):
        raise SystemExit('Review placement baseline changed; prepare a new packet')
    flow = load_json(ROOT / 'config/pcb-flow.json') or {}
    freeze = resolve_path((flow.get('manufacturing') or {}).get('gate_receipts', {}).get('electrical_freeze'))
    current = checkpoint_dependencies(board, baseline, ROOT / 'config/pcb-flow.json', freeze)
    if current != packet['checkpoint_inputs']:
        raise SystemExit('Review inputs changed; prepare a new packet and reuse valid checks')
    records = packet.get('step_records')
    if not isinstance(records, dict) or not all(isinstance(record, dict)
            and isinstance(record.get('inputs'), dict) and reusable_step(record, record['inputs'])
            and dependencies_fresh(record['inputs'].get('dependencies')) for record in records.values()):
        raise SystemExit('Review step evidence is stale or incomplete; prepare a new packet')


def packet_artifact(packet: dict[str, Any], name: str, *, required: bool = True) -> Path | None:
    value = (packet.get("artifacts") or {}).get(name)
    if value is None and not required:
        return None
    path = resolve_path(value)
    digest = (packet.get("artifact_sha256") or {}).get(name)
    if path is None or not path.is_file() or sha256(path) != digest:
        raise SystemExit(f"Release review packet artifact is missing or stale: {name}")
    return path


def run_checked(name: str, command: list[str], run: Any) -> None:
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    log = run.tmp / f"{name}.log"
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode:
        finish_run(run, False, artifacts={"failed_log": relative(log)}, metrics={"failed_step": name})
        raise SystemExit(f"Release review finalization failed at {name}; see {relative(log)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    packet_path = args.packet.resolve()
    assessment = args.assessment.resolve()
    packet = load_json(packet_path)
    if packet is None or packet.get("kind") != "release_review_packet" or packet.get("ready_for_assessment") is not True:
        raise SystemExit("A passing release review packet is required")
    if not dependencies_fresh(packet.get("producer_dependencies")):
        raise SystemExit("Release review packet was produced by stale review tooling; prepare a new packet")
    board = resolve_path(packet.get("board"))
    adopted = adopted_routing_board()
    if (
        board is None or not board.is_file() or board != adopted
        or sha256(board) != packet.get("board_sha256")
    ):
        raise SystemExit("Release review packet does not match the adopted routing artifact")
    require_current_review_inputs(packet, board)
    if not assessment.is_file() or assessment == packet_artifact(packet, "assessment_template"):
        raise SystemExit("Complete a separate visual assessment file; the generated template is not approval")
    audit = packet_artifact(packet, "audit")
    drc_report = packet_artifact(packet, "drc_report")
    semantics = packet_artifact(packet, "placement_semantics")
    visual_bundle = packet_artifact(packet, "visual_bundle")
    footprint = packet_artifact(packet, "footprint_equivalence", required=False)
    semantics_receipt = load_json(semantics)
    placement_state = "moved" if list((semantics_receipt or {}).get("moved_components") or []) else "unchanged"

    with guard_run(create_run("release", "release-review-finalize", args.run_id)) as run:
        return execute_review(run, board, packet, packet_path, assessment, audit, drc_report, semantics, visual_bundle, footprint, placement_state)


def execute_review(run, board: Path, packet: dict[str, Any], packet_path: Path, assessment: Path, audit: Path, drc_report: Path, semantics: Path, visual_bundle: Path, footprint: Path | None, placement_state: str) -> int:
    visual_review = run.evidence / "visual-review.json"
    drc_review = run.evidence / "drc-review.json"
    physical_review = run.evidence / "physical-review.json"
    run_checked("visual-review", [
        sys.executable, str(ROOT / "scripts/record_pcb_visual_review.py"),
        "--board", str(board), "--bundle", str(visual_bundle), "--assessment", str(assessment),
        "--placement-semantics", str(semantics), "--placement-state", placement_state,
        "--output", str(visual_review),
    ], run)
    visual = load_json(visual_review)
    if visual is None or visual.get("review_pass") is not True:
        finish_run(run, False, artifacts={"visual_review": relative(visual_review)}, metrics={"failed_step": "visual_verdict"})
        raise SystemExit("Visual assessment did not pass; revise the board or assessment findings before approval")

    if bool((packet.get("drc") or {}).get("review_required")):
        command = [
            sys.executable, str(ROOT / "scripts/record_kicad_drc_review.py"),
            "--board", str(board), "--drc-report", str(drc_report), "--audit", str(audit),
            "--visual-review", str(visual_review), "--output", str(drc_review),
        ]
        if footprint is not None:
            command.extend(["--footprint-equivalence", str(footprint)])
        run_checked("drc-review", command, run)

    run_checked("physical-review", [
        sys.executable, str(ROOT / "scripts/record_physical_review.py"),
        "--board", str(board), "--placement-evidence", str(semantics),
        "--critical-net-evidence", str(audit), "--return-path-evidence", str(audit),
        "--visual-review", str(visual_review), "--output", str(physical_review),
    ], run)
    finish_run(
        run, True,
        artifacts={
            "packet": relative(packet_path), "assessment": relative(assessment),
            "visual_review": relative(visual_review),
            "drc_review": relative(drc_review) if drc_review.is_file() else None,
            "physical_review": relative(physical_review),
        },
        metrics={"board_sha256": packet["board_sha256"], "drc_reviewed": drc_review.is_file()},
    )
    print(json.dumps({
        "success": True, "board_sha256": packet["board_sha256"],
        "visual_review": relative(visual_review),
        "drc_review": relative(drc_review) if drc_review.is_file() else None,
        "physical_review": relative(physical_review),
        "next_command": f'py -3.14 scripts/pcbctl.py verify readiness --board "{relative(board)}"',
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
