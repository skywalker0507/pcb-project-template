#!/usr/bin/env python3
"""Bind a zero-unconnected routing checkpoint and atomically advance the handoff."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from artifact_store import config as artifact_config, create_run, finish_run, publish_stage_artifact, prune_checkpoint_runs, relative


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--visual-review", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    board = args.board.resolve()
    audit_path = args.audit.resolve()
    visual_path = args.visual_review.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    board_hash = sha256(board)
    audit = load(audit_path, "routing audit")
    visual = load(visual_path, "visual review")
    counts = audit.get("candidate_counts")
    if not isinstance(counts, dict):
        raise SystemExit("Routing audit is missing candidate_counts")
    if (
        audit.get("success") is not True
        or audit.get("baseline_pass") is not True
        or audit.get("candidate_sha256") != board_hash
        or int(counts.get("unconnected_items", -1)) != 0
    ):
        raise SystemExit("Routing checkpoint requires a matching passing audit with zero unconnected items")
    if (
        visual.get("kind") != "pcb_visual_review"
        or visual.get("success") is not True
        or visual.get("review_pass") is not True
        or visual.get("tier") != "checkpoint"
        or visual.get("board_sha256") != board_hash
    ):
        raise SystemExit("Routing checkpoint requires a matching passing checkpoint visual review")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-routing-checkpoint-finalize")
    run = create_run("routing", "routing-checkpoint-finalize", run_id)
    evidence_dir = run.evidence
    published = publish_stage_artifact(
        run, board, "routed.kicad_pcb",
        evidence={"audit": portable(audit_path), "visual_review": portable(visual_path)},
    )
    published_board = ROOT / str(published["artifact"])
    candidate_path = evidence_dir / "routing-candidate.json"
    checkpoint_path = evidence_dir / "routing-checkpoint.json"
    project = published_board.with_suffix(".kicad_pro")
    candidate = {
        "schema": 1, "kind": "routing_candidate", "success": True,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "candidate": portable(published_board), "candidate_sha256": board_hash,
        "source_candidate": portable(board),
        "candidate_project": str(project) if project.is_file() else None,
        "candidate_project_sha256": sha256(project) if project.is_file() else None,
        "audit": portable(audit_path), "audit_sha256": sha256(audit_path),
        "scope": "Routing-complete exact board with zero unconnected items.",
        "evidence": {"baseline_pass": True, "unconnected_items": 0, "new_violations": []},
        "limits": ["This is a routing-stage candidate, not manufacturing or release approval."],
    }
    checkpoint = {
        "schema": 1, "kind": "routing_checkpoint", "success": True,
        "routing_complete": True, "board": portable(published_board), "board_sha256": board_hash,
        "unconnected_items": 0,
        "audit": portable(audit_path), "audit_sha256": sha256(audit_path),
        "visual_review": portable(visual_path), "visual_review_sha256": sha256(visual_path),
        "candidate_receipt": portable(candidate_path),
        "limits": "Routing completion does not prove release or manufacturing readiness.",
    }
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    debt = Counter({str(name): int(count) for name, count in counts.items() if name != "unconnected_items" and int(count) > 0})
    command = [
        sys.executable, str(ROOT / "scripts/create_stage_handoff.py"),
        "--stage", "release", "--status", "in_progress",
        "--objective", "Resolve reviewed release debt, run exact-board release gates, and export the reproducible manufacturing package.",
        "--board", str(published_board), "--candidate-receipt", str(candidate_path), "--skip-pipeline",
    ]
    for name, count in sorted(debt.items()):
        command += ["--open-item", f"Resolve or independently review exact-board DRC category {name} ({count} item(s)); do not regenerate the baseline."]
    command += ["--next-action", "Run the compact release-readiness preview, resolve classified debt, then execute the guarded release pipeline."]
    handoff = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if handoff.returncode != 0:
        finish_run(run, False, artifacts={"published_board": relative(published_board)})
        raise SystemExit("Checkpoint evidence was written but handoff refresh failed: " + handoff.stdout[-800:] + handoff.stderr[-400:])
    finish_run(run, True, artifacts={
        "published_board": relative(published_board),
        "stage_result": relative(run.release / "stage-result.json"),
        "candidate_receipt": relative(candidate_path),
        "checkpoint_receipt": relative(checkpoint_path),
    }, metrics={"unconnected_items": 0, "release_debt_items": sum(debt.values())})
    retention = None
    if bool((artifact_config().get("retention") or {}).get("auto_checkpoint_prune_after_stage_publish", False)):
        try:
            retention = prune_checkpoint_runs(artifact_config(), apply=True)
        except SystemExit as exc:
            retention = {"success": False, "error": str(exc), "release_unaffected": True}
    result = {
        "schema": 1, "kind": "routing_checkpoint_finalize", "success": True,
        "board_sha256": board_hash, "unconnected_items": 0,
        "release_debt": dict(sorted(debt.items())),
        "candidate_receipt": portable(candidate_path), "checkpoint_receipt": portable(checkpoint_path),
        "published_board": portable(published_board),
        "handoff_stage": "release", "handoff_status": "in_progress",
        "retention": retention,
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
