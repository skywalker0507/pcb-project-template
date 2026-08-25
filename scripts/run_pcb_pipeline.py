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
from artifact_store import config as artifact_config, create_run, finish_run, publish_stage_artifact, relative
from ai_tool_policy import allowed_tools
from create_houtini_job import create_job
from electrical_backend import read_config as read_electrical_config, selected_backend as selected_electrical_backend
from pcb_policy import load_acceptance, require_no_prohibited_evidence
from release_inputs import adopted_routing_board as resolve_adopted_routing_board, fresh_release_readiness, resolve_release_input
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


def adopted_routing_board() -> Path:
    return resolve_adopted_routing_board(ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/pcb-flow.json")
    parser.add_argument("--build", action="store_true", help="Run the selected isolated electrical backend first.")
    parser.add_argument("--export", action="store_true", help="Regenerate JLCPCB core outputs after checks pass.")
    parser.add_argument("--tier", choices=("checkpoint", "release"), default="checkpoint", help="Checkpoint runs board DRC/connectivity only; release also refreshes policy and project gates.")
    parser.add_argument(
        "--board",
        type=Path,
        help="Exact KiCad board to verify; defaults to project.board. Use this for isolated routing checkpoints.",
    )
    parser.add_argument(
        "--baseline-board",
        type=Path,
        help="Passing predecessor for checkpoint DRC/connectivity delta; not valid for release/export.",
    )
    parser.add_argument("--run-id", help="Optional stable artifact run id; defaults to a UTC timestamp and operation.")
    parser.add_argument("--log-dir", type=Path, help="Compatibility override; defaults inside the run tmp lane.")
    args = parser.parse_args()
    if args.build and args.export:
        raise SystemExit("A source build creates an unreviewed candidate; review it before a separate export attempt")
    if args.export:
        args.tier = "release"
    if args.baseline_board and (args.build or args.export or args.tier != "checkpoint"):
        raise SystemExit("--baseline-board is only valid for checkpoint verification")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    project = config["project"]
    selected_board = (
        args.board.resolve() if args.board
        else adopted_routing_board() if args.export
        else (ROOT / project["board"]).resolve()
    )
    preflight_board = selected_board
    if args.export:
        readiness = fresh_release_readiness(preflight_board)
        if readiness is None:
            print(json.dumps({
                "success": False, "error": "release_readiness_missing_or_stale",
                "next": f'py -3.14 scripts/pcbctl.py verify readiness --board "{preflight_board}"',
            }, separators=(",", ":")))
            return 2
        if readiness.get("release_ready") is not True:
            print(json.dumps({
                "success": False, "error": "release_readiness_blocked",
                "blockers": [item.get("code") for item in readiness.get("blockers", []) if isinstance(item, dict)],
                "next_action": readiness.get("next_action"), "next_command": readiness.get("next_command"),
            }, separators=(",", ":")))
            return 2
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
    manufacturing_registry_validation = run.evidence / "manufacturing-registry-validation.json"
    gerber_rules_gate = run.evidence / "manufacturing-registry-gerber.json"
    footprint_equivalence = run.evidence / "footprint-equivalence.json"
    physical_integrity = run.evidence / "physical-integrity.json"
    special_process_gate = run.evidence / "manufacturing-registry-pre-export.json"

    acceptance_path = ROOT / config.get("acceptance", "config/pcb-acceptance.json")
    release_rules = load_acceptance(acceptance_path, "release", {
        "release_pass", "board_sha256_match", "drc_zero_unconnected", "manufacturing_profile_selected",
        "electrical_freeze_pass", "jlc_dfm_pass", "dfm_order_profile_match",
        "final_electrical_parity_pass", "physical_integrity_pass", "critical_footprint_rule_areas_pass", "manufacturing_registry_pass", "gerber_rules_pass",
        "special_manufacturing_processes_confirmed",
    })
    require_no_prohibited_evidence(release_rules, {"matching_release_audit"})
    board_path = selected_board
    if not args.build and not board_path.is_file():
        raise SystemExit(f"Board not found: {board_path}")
    board = str(board_path)
    baseline_board_path = args.baseline_board.resolve() if args.baseline_board else None
    if baseline_board_path and not baseline_board_path.is_file():
        raise SystemExit(f"Baseline board not found: {baseline_board_path}")

    commands: list[tuple[str, list[str]]] = []
    resolved_release_inputs: dict[str, dict[str, str | None]] = {}
    if args.build or args.tier == "release":
        commands.append(("compile-design-policy", [sys.executable, "scripts/compile_design_policy.py"]))
        commands.append(("manufacturing-registry", [
            sys.executable, "scripts/check_manufacturing_registry.py",
            "--phase", "validate",
            "--output", str(manufacturing_registry_validation),
        ]))
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
        if args.tier == "checkpoint" and baseline_board_path:
            audit_command = [
                sys.executable, "scripts/krt_candidate_audit.py",
                "--input", str(baseline_board_path),
                "--candidate", board,
                "--work-dir", str(run.tmp / "checkpoint-drc"),
                "--cache-dir", str(run.tmp / "drc-cache"),
                "--report", str(evidence_audit),
            ]
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
        commands.append(("kicad-audit", audit_command))
        if args.tier == "release":
            commands.append(("physical-integrity", [
                sys.executable, "scripts/check_kicad_physical_integrity.py",
                "--board", board, "--phase", "release",
                "--output", str(physical_integrity),
                "--footprint-output", str(footprint_equivalence),
            ]))
            commands.append((
                "pcb-context",
                [
                    sys.executable, "scripts/extract_pcb_context.py",
                    "--board", board,
                    "--policy", str(ROOT / context_config["policy"]),
                    "--output", str(context_output),
                ],
            ))
        if args.export:
            physical_review, _, physical_review_source = resolve_release_input(config, "physical_review")
            export_log = run.tmp / "logs" / "jlcpcb-export.log"
            gate_receipts = manufacturing_config["gate_receipts"]
            final_parity, _, final_parity_source = resolve_release_input(config, "final_electrical_parity")
            jlc_dfm, _, jlc_dfm_source = resolve_release_input(config, "jlc_dfm")
            selected = {
                "physical_review": (physical_review, physical_review_source),
                "final_electrical_parity": (final_parity, final_parity_source),
                "jlc_dfm": (jlc_dfm, jlc_dfm_source),
            }
            missing_inputs = [name for name, (path, _) in selected.items() if path is None]
            if missing_inputs:
                raise SystemExit("Release inputs are not configured: " + ", ".join(missing_inputs))
            resolved_release_inputs = {
                name: {"path": relative(path), "source": source}
                for name, (path, source) in selected.items() if path is not None
            }
            manufacturing_gate_command = [
                sys.executable, "scripts/check_manufacturing_gates.py",
                "--board", board,
                "--electrical-freeze", str(ROOT / gate_receipts["electrical_freeze"]),
                "--final-electrical-parity", str(final_parity),
                "--jlc-dfm", str(jlc_dfm),
                "--output", str(manufacturing_gate),
            ]
            if gate_receipts.get("easyeda_import_parity"):
                manufacturing_gate_command.extend(["--import-parity", str(ROOT / gate_receipts["easyeda_import_parity"])])
            if gate_receipts.get("easyeda_drc"):
                manufacturing_gate_command.extend(["--easyeda-drc", str(ROOT / gate_receipts["easyeda_drc"])])
            commands.extend([
                (
                    "manufacturing-registry-pre-export",
                    [
                        sys.executable, "scripts/check_manufacturing_registry.py",
                        "--phase", "pre_export",
                        "--output", str(special_process_gate),
                    ],
                ),
                (
                    "manufacturing-gates",
                    manufacturing_gate_command,
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
                        "--registry-pre-export", str(special_process_gate),
                        "--gerber-rules-output", str(gerber_rules_gate),
                        "--footprint-equivalence", str(footprint_equivalence),
                        "--physical-integrity", str(physical_integrity),
                    ],
                ),
                (
                    "jlcpcb-package-check",
                    [
                        sys.executable, "scripts/check_jlcpcb_release.py",
                        "--package", str(ROOT / manufacturing_config["output"]),
                        "--board-stack", str(ROOT / config["board_stack"]),
                        "--gerber-rules", str(gerber_rules_gate),
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
    gate_result = next((item["data"] for item in steps if item["name"] == "manufacturing-gates"), None)
    package_result = next((item["data"] for item in steps if item["name"] == "jlcpcb-package-check"), None)
    failed = next((item for item in steps if not item["success"]), None)
    success = len(steps) == len(commands) and all(bool(item["success"]) for item in steps)
    checkpoint_delta = args.tier == "checkpoint" and baseline_board_path is not None
    kicad_audit_pass = (
        audit.get("success") is True and audit.get("baseline_pass") is True
        if checkpoint_delta and isinstance(audit, dict)
        else audit.get("release_pass") is True if isinstance(audit, dict)
        else None
    )
    release_pass = bool(
        args.export and success and kicad_audit_pass
        and isinstance(gate_result, dict) and gate_result.get("manufacturing_gate_pass") is True
        and isinstance(package_result, dict) and package_result.get("success") is True
    )
    delegation = None
    if failed and "houtini" in allowed_tools():
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
        "requested": {
            "build": args.build,
            "export": args.export,
            "tier": args.tier,
            "board": relative(board_path) if not args.build else None,
            "baseline_board": relative(baseline_board_path) if baseline_board_path else None,
        },
        "electrical_candidate": electrical,
        "candidate_pending_review": args.build and not failed,
        "acceptance": {"path": relative(acceptance_path), "release_requires": release_rules.get("requires", [])},
        "resolved_release_inputs": resolved_release_inputs,
        "board_sha256": audit.get("board_sha256") if isinstance(audit, dict) else None,
        "in1_track_segments": audit.get("in1_non_ground_segments") if isinstance(audit, dict) else None,
        "drc_categories": (
            audit.get("new_violation_types", {}) if checkpoint_delta
            else audit.get("drc_categories")
        ) if isinstance(audit, dict) else None,
        "kicad_audit_pass": kicad_audit_pass,
        "release_pass": release_pass,
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
            "manufacturing_registry_validation": relative(manufacturing_registry_validation) if manufacturing_registry_validation.is_file() else None,
            "gerber_rules_gate": relative(gerber_rules_gate) if gerber_rules_gate.is_file() else None,
            "footprint_equivalence": relative(footprint_equivalence) if footprint_equivalence.is_file() else None,
            "physical_integrity": relative(physical_integrity) if physical_integrity.is_file() else None,
            "manufacturing_registry_pre_export": relative(special_process_gate) if special_process_gate.is_file() else None,
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
    if release_pass and isinstance(audit, dict):
        release_artifacts["routing_audit"] = copy_release_evidence(evidence_audit, run.release / evidence_audit.name)
        release_artifacts["drc_report"] = copy_release_evidence(evidence_drc, run.release / evidence_drc.name)
        if package_audit.is_file():
            release_artifacts["package_audit"] = copy_release_evidence(package_audit, run.release / package_audit.name)
        if manufacturing_gate.is_file():
            release_artifacts["manufacturing_gate"] = copy_release_evidence(manufacturing_gate, run.release / manufacturing_gate.name)
        if manufacturing_registry_validation.is_file():
            release_artifacts["manufacturing_registry_validation"] = copy_release_evidence(manufacturing_registry_validation, run.release / manufacturing_registry_validation.name)
        if gerber_rules_gate.is_file():
            release_artifacts["gerber_rules_gate"] = copy_release_evidence(gerber_rules_gate, run.release / gerber_rules_gate.name)
        if footprint_equivalence.is_file():
            release_artifacts["footprint_equivalence"] = copy_release_evidence(footprint_equivalence, run.release / footprint_equivalence.name)
        if physical_integrity.is_file():
            release_artifacts["physical_integrity"] = copy_release_evidence(physical_integrity, run.release / physical_integrity.name)
        if special_process_gate.is_file():
            release_artifacts["manufacturing_registry_pre_export"] = copy_release_evidence(special_process_gate, run.release / special_process_gate.name)
        if physical_review is None:
            raise SystemExit("Passing physical review input disappeared during release publication")
        release_artifacts["physical_review"] = copy_release_evidence(physical_review, run.release / physical_review.name)
        package_reference = run.release / "package-reference.json"
        package_reference.write_text(json.dumps({
            "schema": 1,
            "board_sha256": audit.get("board_sha256"),
            "release_pass": release_pass,
            "package": str((ROOT / manufacturing_config["output"]).resolve()),
            "note": "The immutable manufacturing snapshot remains under manufacturing/releases after create_release_snapshot.py.",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        release_artifacts["package_reference"] = relative(package_reference)
        published = publish_stage_artifact(
            run, board_path, "released.kicad_pcb",
            evidence={
                "routing_audit": release_artifacts["routing_audit"],
                "physical_review": release_artifacts["physical_review"],
                **({"manufacturing_gate": release_artifacts["manufacturing_gate"]} if "manufacturing_gate" in release_artifacts else {}),
                **({"manufacturing_registry_validation": release_artifacts["manufacturing_registry_validation"]} if "manufacturing_registry_validation" in release_artifacts else {}),
                **({"gerber_rules_gate": release_artifacts["gerber_rules_gate"]} if "gerber_rules_gate" in release_artifacts else {}),
                **({"footprint_equivalence": release_artifacts["footprint_equivalence"]} if "footprint_equivalence" in release_artifacts else {}),
                **({"physical_integrity": release_artifacts["physical_integrity"]} if "physical_integrity" in release_artifacts else {}),
                **({"manufacturing_registry_pre_export": release_artifacts["manufacturing_registry_pre_export"]} if "manufacturing_registry_pre_export" in release_artifacts else {}),
            },
        )
        release_artifacts["board"] = str(published["artifact"])
        release_artifacts["stage_result"] = relative(run.release / "stage-result.json")

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
