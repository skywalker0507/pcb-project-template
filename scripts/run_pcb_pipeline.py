#!/usr/bin/env python3
"""Run the repeatable PCB checks with one compact JSON result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcb_policy import load_acceptance, require_no_prohibited_evidence
from create_houtini_job import create_job


ROOT = Path(__file__).resolve().parents[1]


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
    log = log_dir / f"{name}.log"
    log.write_text(result.stdout, encoding="utf-8")
    data = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            data = candidate
            break
    return {
        "name": name,
        "success": result.returncode == 0,
        "exit_code": result.returncode,
        "log": str(log.resolve()),
        "data": data,
        "tail": result.stdout.splitlines()[-1] if result.stdout.splitlines() else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/pcb-flow.json",
    )
    parser.add_argument("--build", action="store_true", help="Run the selected isolated electrical backend first.")
    parser.add_argument("--export", action="store_true", help="Regenerate JLCPCB core outputs after checks pass.")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=ROOT / "build/logs/pcb-pipeline",
    )
    args = parser.parse_args()
    if args.build and args.export:
        raise SystemExit("A source build creates an unreviewed candidate; import/review it before a separate export attempt")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    project = config["project"]
    audit_config = config["audit"]
    context_config = config["context"]
    manufacturing_config = config["manufacturing"]
    acceptance_path = ROOT / config.get("acceptance", "config/pcb-acceptance.json")
    release_rules = load_acceptance(acceptance_path, "release", {
        "release_pass", "board_sha256_match", "drc_zero_unconnected", "manufacturing_profile_selected",
    })
    require_no_prohibited_evidence(release_rules, {"matching_release_audit"})
    board = str(ROOT / project["board"])

    commands: list[tuple[str, list[str]]] = []
    commands.append(("compile-design-policy", [sys.executable, "scripts/compile_design_policy.py"]))
    if args.build:
        commands.append(
            (
                "electrical-build",
                [
                    sys.executable,
                    "scripts/build_electrical.py",
                    "--build",
                    project["build_target"],
                ],
            )
        )
    else:
        for script in config.get("checks", []):
            commands.append((Path(script).stem, [sys.executable, script]))
        audit_command = [
            sys.executable,
            "scripts/audit_kicad_release.py",
            "--board",
            board,
            "--baseline",
            str(ROOT / audit_config["baseline"]),
            "--routing-paths",
            str(ROOT / audit_config["routing_paths"]),
            "--audit-json",
            str(ROOT / audit_config["audit_json"]),
            "--drc-report",
            str(ROOT / audit_config["drc_report"]),
        ]
        if audit_config.get("require_in1_ground", False):
            audit_command.append("--require-in1-ground")
        commands.extend(
            [
                ("kicad-audit", audit_command),
                (
                    "pcb-context",
                    [
                        sys.executable,
                        "scripts/extract_pcb_context.py",
                        "--board",
                        board,
                        "--policy",
                        str(ROOT / context_config["policy"]),
                        "--output",
                        str(ROOT / context_config["output"]),
                    ],
                ),
            ]
        )
        if args.export:
            commands.append(
                (
                    "jlcpcb-export",
                    [
                        sys.executable,
                        "scripts/export_jlcpcb_release.py",
                        "--board",
                        board,
                        "--output",
                        str(ROOT / manufacturing_config["output"]),
                        "--log",
                        str(ROOT / manufacturing_config["log"]),
                        "--acceptance",
                        str(acceptance_path),
                    ],
                )
            )

    steps = []
    for name, command in commands:
        step = run_step(name, command, args.log_dir)
        steps.append(step)
        if not step["success"]:
            break

    audit = next((item["data"] for item in steps if item["name"] == "kicad-audit"), None)
    context = next((item["data"] for item in steps if item["name"] == "pcb-context"), None)
    electrical = next((item["data"] for item in steps if item["name"] == "electrical-build"), None)
    failed = next((item for item in steps if not item["success"]), None)
    delegation = None
    if failed:
        delegation = create_job(
            [Path(str(failed["log"]))], "pipeline_failure",
            f"The deterministic pipeline stopped at {failed['name']} with exit code {failed['exit_code']}.",
            job_name=f"pipeline-{failed['name']}",
        )
    summary = {
        "success": len(steps) == len(commands) and all(item["success"] for item in steps),
        "requested": {"build": args.build, "export": args.export},
        "electrical_candidate": electrical,
        "candidate_pending_review": args.build and not failed,
        "acceptance": {"path": str(acceptance_path), "release_requires": release_rules.get("requires", [])},
        "board_sha256": audit.get("board_sha256") if isinstance(audit, dict) else None,
        "in1_track_segments": audit.get("in1_non_ground_segments") if isinstance(audit, dict) else None,
        "drc_categories": audit.get("drc_categories") if isinstance(audit, dict) else None,
        "routing_pairs": audit.get("aggregate_routing_pairs") if isinstance(audit, dict) else None,
        "passed_steps": [item["name"] for item in steps if item["success"]],
        "failed_step": (
            {
                "name": failed["name"],
                "exit_code": failed["exit_code"],
                "log": failed["log"],
                "tail": failed["tail"],
            }
            if failed
            else None
        ),
        "artifacts": {
            "electrical_receipt": electrical.get("receipt") if isinstance(electrical, dict) else None,
            "audit_json": audit.get("audit_json") if isinstance(audit, dict) else None,
            "drc_report": audit.get("drc_report") if isinstance(audit, dict) else None,
            "pcb_context": context.get("output") if isinstance(context, dict) else None,
            "logs": str(args.log_dir.resolve()),
        },
        "delegation": delegation,
    }
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
