#!/usr/bin/env python3
"""Create a compact, verifiable handoff for the next Codex stage/session."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcb_policy import relative_to
from electrical_backend import read_config, selected_backend
from physical_backend import read_config as read_physical_config, selected_backend as selected_physical_backend


ROOT = Path(__file__).resolve().parents[1]


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def tree_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                item for item in path.rglob("*")
                if item.is_file() and not {"node_modules", "dist", ".git"}.intersection(item.relative_to(ROOT).parts)
            )
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper()


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run_pipeline(config: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_pcb_pipeline.py",
            "--config",
            str(config),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    for line in reversed(result.stdout.splitlines()):
        try:
            summary = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(summary, dict):
            if result.returncode != 0:
                summary["success"] = False
            return summary
    raise RuntimeError("PCB pipeline returned no JSON summary")


def receipt_open_items(receipt: dict[str, object], limit: int) -> list[str]:
    items: list[str] = []
    failed = receipt.get("failed_step")
    if isinstance(failed, dict):
        items.append(f"Pipeline failed at {failed.get('name', 'unknown')}: inspect its bounded log/receipt.")
    audit_path = (receipt.get("artifacts") or {}).get("audit_json") if isinstance(receipt.get("artifacts"), dict) else None
    if isinstance(audit_path, str) and Path(audit_path).is_file():
        try:
            audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            audit = {}
        categories = audit.get("drc_categories", {}) if isinstance(audit, dict) else {}
        if isinstance(categories, dict):
            for name, count in sorted(categories.items()):
                if count:
                    items.append(f"Resolve DRC category {name} ({count} item(s)); do not regenerate the baseline.")
        for key in ("unexpected_violations", "unconnected_signatures"):
            values = audit.get(key, []) if isinstance(audit, dict) else []
            if isinstance(values, list):
                items.extend(f"Review {key}: {value}" for value in values[:limit])
    return items[:limit] or ["Pipeline receipt has no actionable audit detail; inspect its failed-step log."]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--open-item", action="append", default=[])
    parser.add_argument("--next-action", action="append", default=[])
    parser.add_argument("--blocking-condition")
    parser.add_argument("--blocking-observations", type=int, default=0)
    parser.add_argument("--safe-alternatives-exhausted", action="store_true")
    parser.add_argument("--why-user-input-required")
    parser.add_argument("--board", type=Path, help="Board represented by this handoff; defaults to the configured active board.")
    parser.add_argument(
        "--candidate-receipt", action="append", type=Path, default=[],
        help="Audited isolated-candidate receipt(s) that must match --board.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/pcb-flow.json",
    )
    parser.add_argument("--skip-pipeline", action="store_true")
    parser.add_argument(
        "--from-pipeline-receipt", type=Path,
        help="Use an existing run_pcb_pipeline JSON receipt instead of rerunning the pipeline.",
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    backend_config = read_config()
    backend_name, backend = selected_backend(backend_config)
    physical_backend_name, _ = selected_physical_backend(read_physical_config())
    checklist_path = ROOT / config.get("stage_checklist", "config/stage-checklist.json")
    checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    stage_rules = checklist.get("stages", {}).get(args.stage)
    if not isinstance(stage_rules, dict):
        raise SystemExit(f"Unknown stage {args.stage!r}; use a key from {checklist_path}")
    stage_number = stage_rules.get("number")
    stage_label = f"{int(stage_number):02d}-{args.stage}" if isinstance(stage_number, int) else args.stage
    limit = int(config["handoff"].get("max_open_items", 8))
    if len(args.open_item) > limit or len(args.next_action) > limit:
        raise SystemExit(f"Handoff lists are limited to {limit} items")
    blocked_statuses = {"blocked", "blocked_external_dependency"}
    if args.status in blocked_statuses:
        if not args.blocking_condition or not args.why_user_input_required:
            raise SystemExit("Blocked handoff requires --blocking-condition and --why-user-input-required")
        if args.blocking_observations < 3:
            raise SystemExit("Blocked handoff requires --blocking-observations of at least 3")
        if not args.safe_alternatives_exhausted:
            raise SystemExit("Blocked handoff requires --safe-alternatives-exhausted")
    elif any((args.blocking_condition, args.blocking_observations, args.safe_alternatives_exhausted, args.why_user_input_required)):
        raise SystemExit("Blocking evidence is only valid for a blocked handoff status")

    if args.from_pipeline_receipt:
        try:
            pipeline = json.loads(args.from_pipeline_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Invalid pipeline receipt: {args.from_pipeline_receipt}: {exc}") from exc
        if not isinstance(pipeline, dict):
            raise SystemExit("Pipeline receipt must contain a JSON object")
        pipeline["receipt_source"] = portable_path(args.from_pipeline_receipt)
        if pipeline.get("success") is not False:
            raise SystemExit("--from-pipeline-receipt requires a failed pipeline receipt")
        if not args.open_item:
            args.open_item = receipt_open_items(pipeline, limit)
        if not args.next_action:
            args.next_action = ["Resolve the recorded failure; do not rerun unrelated stages."]
    else:
        pipeline = None if args.skip_pipeline else run_pipeline(args.config)
    board_path = (args.board if args.board else ROOT / config["project"]["board"]).resolve()
    if not board_path.is_file():
        raise SystemExit(f"Handoff board not found: {board_path}")
    board_sha256 = hashlib.sha256(board_path.read_bytes()).hexdigest().upper()
    candidate_receipts = []
    for receipt_path in args.candidate_receipt:
        try:
            receipt = json.loads(receipt_path.resolve().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Invalid candidate receipt: {receipt_path}: {exc}") from exc
        if receipt.get("kind") != "routing_candidate" or receipt.get("candidate_sha256") != board_sha256:
            raise SystemExit("Candidate receipt does not match the handoff board")
        candidate_receipts.append(portable_path(receipt_path))
    output = ROOT / config["handoff"]["output"]
    dirty_lines = git("status", "--short").splitlines()
    handoff = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": config["project"]["name"],
        "stage": args.stage,
        "stage_number": stage_number,
        "stage_label": stage_label,
        "status": args.status,
        "objective": args.objective,
        "stage_rules": {
            "required_evidence": stage_rules.get("required_evidence", []),
            "forbidden": stage_rules.get("forbidden", []),
        },
        "stage_files": stage_rules.get("files", []),
        "electrical_backend": backend_name,
        "physical_backend": physical_backend_name,
        "routing_engine": "kicad_routing_tools",
        "validation_backends": ["easyeda_pro_drc", "kicad_cli"],
        "manufacturing_backend": "jlcpcb",
        "source_sha256": tree_hash([
            ROOT / "config/electrical-backend.json",
            ROOT / str(backend["source_root"]),
        ]),
        "board": portable_path(board_path),
        "board_sha256": board_sha256,
        "candidate_receipts": candidate_receipts,
        "git": {
            "commit": git("rev-parse", "HEAD"),
            "dirty": bool(dirty_lines),
            "dirty_path_count": len(dirty_lines),
        },
        "pipeline": pipeline,
        "open_items": args.open_item,
        "next_actions": args.next_action,
        "read_first": [
            relative_to(ROOT, output),
        ],
        "authoritative_docs": [
            relative_to(ROOT, ROOT / path)
            for path in config["handoff"].get("authoritative_docs", [])
        ],
    }
    if args.status in blocked_statuses:
        handoff["blocking_evidence"] = {
            "condition": args.blocking_condition,
            "observations": args.blocking_observations,
            "safe_alternatives_exhausted": args.safe_alternatives_exhausted,
            "why_user_input_required": args.why_user_input_required,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown = output.with_suffix(".md")
    pipeline_status = (
        "not run"
        if pipeline is None
        else "PASS" if pipeline.get("success") else "FAIL"
    )
    markdown.write_text(
        "\n".join(
            [
                f"# {config['project']['name']} — Stage {stage_label}",
                "",
                f"- Status: {args.status}",
                f"- Objective: {args.objective}",
                f"- Pipeline: {pipeline_status}",
                f"- Board SHA-256: `{handoff['board_sha256']}`",
                f"- Source SHA-256: `{handoff['source_sha256']}`",
                "",
                "## Open items",
                *([f"- {item}" for item in args.open_item] or ["- None"]),
                "",
                "## Next actions",
                *([f"- {item}" for item in args.next_action] or ["- None"]),
                "",
                f"Machine-readable handoff: `{portable_path(output)}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "handoff": str(output.resolve()),
                "markdown": str(markdown.resolve()),
                "bytes": output.stat().st_size,
                "pipeline_success": pipeline.get("success") if pipeline else None,
            },
            separators=(",", ":"),
        )
    )
    return 0 if pipeline is None or pipeline.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
