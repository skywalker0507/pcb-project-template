#!/usr/bin/env python3
"""Run PCB checks into one stage-scoped artifact run and print compact JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_store import config as artifact_config, create_run, finish_run, relative
from create_houtini_job import create_job
from electrical_backend import read_config as read_electrical_config, selected_backend as selected_electrical_backend
from pcb_policy import load_acceptance, require_no_prohibited_evidence
from stage_policy import ROOT, current_stage


def last_json(output: str) -> dict[str, object] | None:
    for line in reversed(output.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate
    return None


def run_step(name: str, command: list[str], log_dir: Path) -> dict[str, object]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{name}.log"
    log.write_text(result.stdout, encoding="utf-8")
    lines = result.stdout.splitlines()
    return {
        "name": name,
        "success": result.returncode == 0,
        "exit_code": result.returncode,
        "log": str(log.resolve()),
        "log_bytes": log.stat().st_size,
        "data": last_json(result.stdout),
        "tail": lines[-1][:500] if lines else "",
    }


def copy_release_evidence(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return relative(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/pcb-flow.json")
    parser.add_argument("--build", action="store_true", help="Run the selected isolated electrical backend first.")
    parser.add_argument("--export", action="store_true", help="Regenerate JLCPCB core outputs after checks pass.")
    parser.add_argument("--run-id", help="Optional stable artifact run id; defaults to a UTC timestamp and operation.")
    parser.add_argument("--log-dir", type=Path, help="Compatibility override; defaults inside the run tmp lane.")
    args = parser.parse_args()
    if args.build and args.export:
        raise SystemExit("A source build creates an unreviewed candidate; review it before a separate export attempt")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    project = config["project"]
    audit_config = config["audit"]
    context_config = config["context"]
    manufacturing_config = config["manufacturing"]
    stage = str(current_stage().get("stage"))
    operation = "electrical-build" if args.build else "release-export" if args.export else "pcb-audit"
    run = create_run(stage, operation, args.run_id)
    log_dir = args.log_dir.resolve() if args.log_dir else run.tmp / "logs"
    evidence_drc = run.evidence / "drc" / "kicad-cli-drc.rpt"
    evidence_audit = run.evidence / "final-routing-audit.json"
    context_output = run.tmp / "context" / "pcb-context.json"
    pipeline_summary = run.evidence / "pipeline-summary.json"
    package_audit = run.evidence / "jlcpcb-package-audit.json"
    manufacturing_gate = run.evidence / "manufacturing-gate.json"

    acceptance_path = ROOT / config.get("acceptance", "config/pcb-acceptance.json")
    release_rules = load_acceptance(acceptance_path, "release", {
        "release_pass", "board_sha256_match", "drc_zero_unconnected", "manufacturing_profile_selected",
        "electrical_freeze_pass", "easyeda_import_parity_pass", "easyeda_drc_pass", "jlc_dfm_pass",
        "final_electrical_parity_pass",
    })
    require_no_prohibited_evidence(release_rules, {"matching_release_audit"})
    board = str(ROOT / project["board"])

    commands: list[tuple[str, list[str]]] = [("compile-design-policy", [sys.executable, "scripts/compile_design_policy.py"])]
    for script in config.get("checks", []):
        commands.append((Path(script).stem, [sys.executable, script]))
    if args.build:
        electrical_name, _ = selected_electrical_backend(read_electrical_config())
        electrical_output = run.tmp / "generated" / electrical_name
        electrical_receipt = run.evidence / "electrical-build.json"
        commands.append((
            "electrical-build",
            [
                sys.executable, "scripts/build_electrical.py",
                "--build", project["build_target"],
                "--output", str(electrical_output),
                "--receipt", str(electrical_receipt),
            ],
        ))
    else:
        audit_command = [
            sys.executable, "scripts/audit_kicad_release.py",
            "--board", board,
            "--baseline", str(ROOT / audit_config["baseline"]),
            "--routing-paths", str(ROOT / audit_config["routing_paths"]),
            "--audit-json", str(evidence_audit),
            "--drc-report", str(evidence_drc),
        ]
        if audit_config.get("require_in1_ground", False):
            audit_command.append("--require-in1-ground")
        commands.extend([
            ("kicad-audit", audit_command),
            (
                "pcb-context",
                [
                    sys.executable, "scripts/extract_pcb_context.py",
                    "--board", board,
                    "--policy", str(ROOT / context_config["policy"]),
                    "--output", str(context_output),
                ],
            ),
        ])
        if args.export:
            physical_review = ROOT / audit_config.get("physical_review", "build/07-release/evidence/manual/physical-review.json")
            export_log = run.tmp / "logs" / "jlcpcb-export.log"
            gate_receipts = manufacturing_config["gate_receipts"]
            commands.extend([
                (
                    "manufacturing-gates",
                    [
                        sys.executable, "scripts/check_manufacturing_gates.py",
                        "--board", board,
                        "--electrical-freeze", str(ROOT / gate_receipts["electrical_freeze"]),
                        "--import-parity", str(ROOT / gate_receipts["easyeda_import_parity"]),
                        "--final-electrical-parity", str(ROOT / gate_receipts["final_electrical_parity"]),
                        "--easyeda-drc", str(ROOT / gate_receipts["easyeda_drc"]),
                        "--jlc-dfm", str(ROOT / gate_receipts["jlc_dfm"]),
                        "--output", str(manufacturing_gate),
                    ],
                ),
                (
                    "jlcpcb-export",
                    [
                        sys.executable, "scripts/export_jlcpcb_release.py",
                        "--board", board,
                        "--audit-json", str(evidence_audit),
                        "--physical-review", str(physical_review),
                        "--manufacturing-gates", str(manufacturing_gate),
                        "--output", str(ROOT / manufacturing_config["output"]),
                        "--log", str(export_log),
                        "--assembly-parts", str(ROOT / manufacturing_config["assembly_parts"]),
                        "--acceptance", str(acceptance_path),
                    ],
                ),
                (
                    "jlcpcb-package-check",
                    [
                        sys.executable, "scripts/check_jlcpcb_release.py",
                        "--package", str(ROOT / manufacturing_config["output"]),
                        "--board-stack", str(ROOT / config["board_stack"]),
                        "--output", str(package_audit),
                    ],
                ),
            ])

    steps: list[dict[str, object]] = []
    for name, command in commands:
        step = run_step(name, command, log_dir)
        steps.append(step)
        if not step["success"]:
            break

    audit = next((item["data"] for item in steps if item["name"] == "kicad-audit"), None)
    context = next((item["data"] for item in steps if item["name"] == "pcb-context"), None)
    electrical = next((item["data"] for item in steps if item["name"] == "electrical-build"), None)
    failed = next((item for item in steps if not item["success"]), None)
    success = len(steps) == len(commands) and all(bool(item["success"]) for item in steps)
    delegation = None
    if failed:
        delegation = create_job(
            [Path(str(failed["log"]))],
            "pipeline_failure",
            f"The deterministic pipeline stopped at {failed['name']} with exit code {failed['exit_code']}.",
            output_dir=run.evidence / "delegation",
            job_name=f"pipeline-{failed['name']}",
        )

    summary: dict[str, object] = {
        "schema": 2,
        "success": success,
        "run": {key: relative(Path(value)) if key in {"tmp", "evidence", "release", "manifest"} else value for key, value in run.as_dict().items()},
        "requested": {"build": args.build, "export": args.export},
        "electrical_candidate": electrical,
        "candidate_pending_review": args.build and not failed,
        "acceptance": {"path": relative(acceptance_path), "release_requires": release_rules.get("requires", [])},
        "board_sha256": audit.get("board_sha256") if isinstance(audit, dict) else None,
        "in1_track_segments": audit.get("in1_non_ground_segments") if isinstance(audit, dict) else None,
        "drc_categories": audit.get("drc_categories") if isinstance(audit, dict) else None,
        "release_pass": audit.get("release_pass") if isinstance(audit, dict) else None,
        "passed_steps": [str(item["name"]) for item in steps if item["success"]],
        "failed_step": ({
            "name": failed["name"], "exit_code": failed["exit_code"],
            "log": relative(Path(str(failed["log"]))), "tail": failed["tail"],
        } if failed else None),
        "artifacts": {
            "electrical_receipt": relative(electrical_receipt) if args.build and electrical_receipt.is_file() else None,
            "audit_json": relative(evidence_audit) if evidence_audit.is_file() else None,
            "drc_report": relative(evidence_drc) if evidence_drc.is_file() else None,
            "pcb_context_index": relative(context_output) if context_output.is_file() else None,
            "package_audit": relative(package_audit) if package_audit.is_file() else None,
            "manufacturing_gate": relative(manufacturing_gate) if manufacturing_gate.is_file() else None,
            "logs": relative(log_dir),
        },
        "context_metrics": {
            "pcb_context_bytes": context_output.stat().st_size if context_output.is_file() else 0,
            "pipeline_log_bytes": sum(int(item.get("log_bytes", 0)) for item in steps),
            "stdout_contract": "compact_json_only",
            "full_pcb_may_enter_llm_context": False,
        },
        "delegation": delegation,
    }
    pipeline_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    release_artifacts: dict[str, str] = {}
    if args.export and success and isinstance(audit, dict) and audit.get("release_pass") is True:
        release_artifacts["routing_audit"] = copy_release_evidence(evidence_audit, run.release / evidence_audit.name)
        release_artifacts["drc_report"] = copy_release_evidence(evidence_drc, run.release / evidence_drc.name)
        if package_audit.is_file():
            release_artifacts["package_audit"] = copy_release_evidence(package_audit, run.release / package_audit.name)
        if manufacturing_gate.is_file():
            release_artifacts["manufacturing_gate"] = copy_release_evidence(manufacturing_gate, run.release / manufacturing_gate.name)
        physical_review = ROOT / audit_config.get("physical_review", "build/07-release/evidence/manual/physical-review.json")
        release_artifacts["physical_review"] = copy_release_evidence(physical_review, run.release / physical_review.name)
        package_reference = run.release / "package-reference.json"
        package_reference.write_text(json.dumps({
            "schema": 1,
            "board_sha256": audit.get("board_sha256"),
            "release_pass": True,
            "package": str((ROOT / manufacturing_config["output"]).resolve()),
            "note": "The immutable manufacturing snapshot remains under manufacturing/releases after create_release_snapshot.py.",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        release_artifacts["package_reference"] = relative(package_reference)

    summary["release_artifacts"] = release_artifacts
    pipeline_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(
        run,
        success,
        artifacts={**summary["artifacts"], "pipeline_summary": relative(pipeline_summary), **release_artifacts},
        metrics=summary["context_metrics"],
    )
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
