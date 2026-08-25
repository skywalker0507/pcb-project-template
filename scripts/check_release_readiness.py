#!/usr/bin/env python3
"""Aggregate exact-board release blockers without running the full pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from engine_runtime import activate

activate()

from pcb_template_engine.manufacturing import evaluate_pre_export  # noqa: E402
from design_policy import (  # noqa: E402
    DEFAULT_OUTPUT as DESIGN_POLICY_OUTPUT,
    MANUFACTURING_REGISTRY_ARTIFACT,
    compile_policy,
    policy_sha256,
)
from release_inputs import (  # noqa: E402
    DEFAULT_INDEX,
    dependencies_fresh,
    dependency,
    load_json,
    relative,
    resolve_path,
    resolve_release_input,
    sha256,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build/07-release/tmp/release-readiness/current.json"
DRC_OUTPUT = ROOT / "build/07-release/tmp/release-readiness/drc-summary.json"


def add_blocker(
    blockers: list[dict[str, Any]], code: str, domain: str, action: str,
    *, detail: object = None, command: str | None = None, external: bool = False,
    required_input: str | None = None,
) -> None:
    item: dict[str, Any] = {"code": code, "domain": domain, "action": action}
    if detail not in (None, [], {}):
        item["detail"] = detail
    if command:
        item["command"] = command
    if external:
        item["external"] = True
    if required_input:
        item["required_input"] = required_input
    blockers.append(item)


def run_drc_summary(board: Path) -> dict[str, Any]:
    command = [
        sys.executable, str(ROOT / "scripts/summarize_kicad_drc.py"),
        "--mode", "pre-route", "--board", str(board), "--output", str(DRC_OUTPUT),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    value = load_json(DRC_OUTPUT)
    if completed.returncode or value is None or value.get("board_sha256") != sha256(board):
        raise SystemExit("Release-readiness DRC summary failed: " + (completed.stdout + completed.stderr)[-800:])
    return value


def receipt_matches(
    value: dict[str, Any] | None, *, kind: str, board_hash: str,
    pass_field: str, policy_hash: str | None = None, registry_hash: str | None = None,
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if value is None:
        return False, ["missing"]
    if value.get("kind") != kind:
        issues.append("kind")
    if value.get("success") is not True or value.get(pass_field) is not True:
        issues.append(pass_field)
    bound_hash = value.get("board_sha256", value.get("final_board_sha256"))
    if bound_hash != board_hash:
        issues.append("board_sha256")
    if policy_hash is not None and value.get("policy_sha256") != policy_hash:
        issues.append("policy_sha256")
    if registry_hash is not None and value.get("registry_sha256") != registry_hash:
        issues.append("registry_sha256")
    return not issues, issues


def routing_adoption(board: Path, board_hash: str) -> tuple[bool, Path]:
    pointer = ROOT / "build/06-routing/CURRENT.json"
    value = load_json(pointer)
    adopted = bool(
        value is not None
        and resolve_path(value.get("artifact")) == board.resolve()
        and value.get("artifact_sha256") == board_hash
    )
    return adopted, pointer


def first_action(blockers: list[dict[str, Any]]) -> tuple[str, str | None, list[str]]:
    if not blockers:
        return "run_guarded_release", "py -3.14 scripts/pcbctl.py release", []
    first = blockers[0]
    required: list[str] = []
    for item in blockers:
        value = str(item.get("required_input") or item["code"])
        if (item.get("external") or item.get("required_input")) and value not in required:
            required.append(value)
    required = required[:4]
    return str(first["action"]), first.get("command"), required


def build_receipt(board: Path) -> dict[str, Any]:
    board = board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    board_hash = sha256(board)
    flow_path = ROOT / "config/pcb-flow.json"
    flow = load_json(flow_path)
    if flow is None:
        raise SystemExit("config/pcb-flow.json is unavailable or invalid")
    blockers: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    used_paths: list[Path] = [
        board, flow_path, ROOT / "config/manufacturing-profile.json",
        ROOT / "config/manufacturing-rules.json", ROOT / "config/pcb-acceptance.json",
        ROOT / "config/release-debt-policy.json", ROOT / "config/toolchain-policy.json",
        Path(__file__).resolve(), ROOT / "scripts/release_inputs.py",
        ROOT / "scripts/summarize_kicad_drc.py",
    ]
    review_packet_path, review_packet, _ = resolve_release_input(flow, "release_review_packet")
    current_review_packet = bool(
        review_packet is not None and review_packet.get("success") is True
        and review_packet.get("ready_for_assessment") is True
        and review_packet.get("board_sha256") == board_hash
        and dependencies_fresh(review_packet.get("producer_dependencies"))
    )
    if review_packet_path is not None and review_packet_path.is_file():
        used_paths.append(review_packet_path)
    review_detail = {
        "packet": relative(review_packet_path) if current_review_packet and review_packet_path else None,
        "visual_bundle": ((review_packet or {}).get("artifacts") or {}).get("visual_bundle") if current_review_packet else None,
        "assessment_template": ((review_packet or {}).get("artifacts") or {}).get("assessment_template") if current_review_packet else None,
    }
    review_action = "complete_release_visual_assessment" if current_review_packet else "prepare_release_review_packet"
    review_command = None if current_review_packet else f'py -3.14 scripts/pcbctl.py verify prepare-review --board "{relative(board)}"'

    adopted, routing_pointer = routing_adoption(board, board_hash)
    if routing_pointer.is_file():
        used_paths.append(routing_pointer)
    checks["routing_artifact_adopted"] = adopted
    if not adopted:
        add_blocker(blockers, "routing_artifact_not_adopted", "authority", "adopt_exact_routing_artifact")

    expected_policy = compile_policy()
    expected_policy_hash = policy_sha256(expected_policy)
    resolved = load_json(DESIGN_POLICY_OUTPUT)
    registry = load_json(MANUFACTURING_REGISTRY_ARTIFACT)
    policy_current = bool(resolved and resolved.get("policy_sha256") == expected_policy_hash)
    registry_current = bool(
        registry and registry.get("policy_sha256") == expected_policy_hash
        and isinstance(registry.get("registry_sha256"), str)
    )
    checks["design_policy_current"] = policy_current
    checks["manufacturing_registry_current"] = registry_current
    if DESIGN_POLICY_OUTPUT.is_file():
        used_paths.append(DESIGN_POLICY_OUTPUT)
    if MANUFACTURING_REGISTRY_ARTIFACT.is_file():
        used_paths.append(MANUFACTURING_REGISTRY_ARTIFACT)
    if not policy_current or not registry_current:
        add_blocker(
            blockers, "compiled_policy_or_registry_stale", "policy", "compile_design_policy",
            command="py -3.14 scripts/compile_design_policy.py",
        )
    release_capable = bool(expected_policy.get("capabilities", {}).get("release"))
    checks["release_capability"] = release_capable
    if not release_capable:
        add_blocker(
            blockers, "release_policy_incomplete", "policy", "resolve_reviewed_policy_inputs",
            detail=(expected_policy.get("capability_issues") or {}).get("release", [])[:6],
        )

    drc = run_drc_summary(board)
    drc_path = resolve_path(drc.get("drc_json"))
    if drc_path is not None and drc_path.is_file():
        used_paths.append(drc_path)
    pre = drc.get("pre_route") or {}
    unconnected = int((drc.get("unconnected") or {}).get("count", -1))
    categories = dict(pre.get("categories") or {})
    copper_fatal = {
        name: count for name, count in categories.items()
        if name in {"shorting_items", "clearance", "tracks_crossing", "track_dangling", "via_dangling", "hole_clearance", "copper_edge_clearance"}
    }
    checks["drc"] = {
        "cache_hit": drc.get("drc_cache_hit"), "unconnected": unconnected,
        "release_debt_count": pre.get("release_debt_count"), "categories": categories,
    }
    if unconnected:
        add_blocker(blockers, "unconnected_items", "connectivity", "return_to_routing", detail=unconnected)
    if copper_fatal:
        add_blocker(blockers, "fatal_copper_drc", "drc", "fix_copper_drc", detail=copper_fatal)
    baseline_path = ROOT / "config/pcb-release-baseline.json"
    baseline = load_json(baseline_path)
    if baseline_path.is_file():
        used_paths.append(baseline_path)
    debt_reviewed = bool(not categories or (baseline and baseline.get("reviewed_board_sha256") == board_hash))
    checks["exact_drc_debt_reviewed"] = debt_reviewed
    if categories and not copper_fatal and not debt_reviewed:
        add_blocker(
            blockers, "exact_drc_review_required", "drc", review_action,
            detail={"categories": categories, "drc_report": relative(drc_path) if drc_path else None},
            command=review_command,
            required_input="completed_visual_assessment" if current_review_packet else None,
        )

    registry_hash = str(registry.get("registry_sha256")) if registry_current and registry else None
    special = evaluate_pre_export(registry) if registry_current and registry else None
    checks["special_processes"] = {
        "pass": bool(special and special.get("success") is True),
        "pending": list((special or {}).get("pending", [])),
    }
    if special and special.get("success") is not True:
        rules_by_id = {
            str(item.get("id")): item.get("requirements")
            for item in (registry or {}).get("rules", []) if isinstance(item, dict)
        }
        add_blocker(
            blockers, "special_process_confirmation_required", "manufacturing",
            "confirm_manufacturing_special_processes",
            detail=[{
                "process": process,
                "allowed_outcomes": (rules_by_id.get(str(process)) or {}).get("allowed_confirmed_outcomes", []),
                "command": f"py -3.14 scripts/pcbctl.py release confirm-process --process {process} --outcome <reviewed-outcome> --evidence <manufacturer-evidence-file>",
            } for process in special.get("pending", [])],
            external=True,
        )

    if DEFAULT_INDEX.is_file():
        used_paths.append(DEFAULT_INDEX)
    input_specs = {
        "final_electrical_parity": ("final_electrical_parity", "final_electrical_parity_pass", False),
        "physical_review": ("physical_review", "review_pass", True),
        "jlc_dfm": ("manufacturing_dfm", "dfm_pass", True),
    }
    evidence: dict[str, Any] = {}
    for name, (kind, pass_field, policy_bound) in input_specs.items():
        path, value, source = resolve_release_input(flow, name)
        if path is not None and path.is_file():
            used_paths.append(path)
        current, issues = receipt_matches(
            value, kind=kind, board_hash=board_hash, pass_field=pass_field,
            policy_hash=expected_policy_hash if policy_bound else None,
            registry_hash=registry_hash if name == "jlc_dfm" else None,
        )
        evidence[name] = {
            "pass": current, "source": source,
            "path": relative(path) if path is not None else None, "issues": issues,
        }
        if current:
            continue
        if name == "final_electrical_parity":
            add_blocker(
                blockers, "final_electrical_parity_missing_or_stale", "electrical",
                "refresh_final_electrical_parity", detail=issues,
                command=f'py -3.14 scripts/pcbctl.py verify parity --board "{relative(board)}"',
            )
        elif name == "physical_review":
            add_blocker(
                blockers, "physical_review_missing_or_stale", "review", review_action,
                detail=review_detail if current_review_packet else issues,
                command=review_command,
                required_input="completed_visual_assessment" if current_review_packet else None,
            )
        else:
            mismatches = list(((value or {}).get("registry_gate") or {}).get("mismatches", []))
            add_blocker(
                blockers, "manufacturing_dfm_missing_stale_or_mismatched", "manufacturing",
                "rerun_dfm_with_reviewed_order_profile", detail=mismatches or issues, external=True,
            )
    checks["release_evidence"] = evidence

    priority = {
        "routing_artifact_not_adopted": 0,
        "compiled_policy_or_registry_stale": 10,
        "release_policy_incomplete": 11,
        "unconnected_items": 20,
        "fatal_copper_drc": 21,
        "exact_drc_review_required": 30,
        "special_process_confirmation_required": 25 if current_review_packet else 40,
        "final_electrical_parity_missing_or_stale": 50,
        "physical_review_missing_or_stale": 60,
        "manufacturing_dfm_missing_stale_or_mismatched": 70,
    }
    blockers.sort(key=lambda item: (priority.get(str(item["code"]), 99), str(item["code"])))
    next_action, next_command, required_inputs = first_action(blockers)
    if not blockers:
        next_command = f'py -3.14 scripts/pcbctl.py release --board "{relative(board)}"'
    unique_dependencies: dict[str, dict[str, str]] = {}
    for path in used_paths:
        if path.is_file():
            item = dependency(path)
            unique_dependencies[item["path"]] = item
    return {
        "schema": 1,
        "kind": "release_readiness",
        "success": not blockers,
        "release_ready": not blockers,
        "board_sha256": board_hash,
        "policy_sha256": expected_policy_hash,
        "registry_sha256": registry_hash,
        "blockers": blockers,
        "blocker_count": len(blockers),
        "next_action": next_action,
        "next_command": next_command,
        "required_inputs": required_inputs,
        "checks": checks,
        "dependencies": {path: item["sha256"] for path, item in unique_dependencies.items()},
        "limits": "Readiness aggregates current blockers and reuses cached DRC; only the guarded release pipeline can approve production output.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_receipt(args.board)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    dependencies = result.pop("dependencies")
    dependency_output = output.with_name(output.stem + ".dependencies.json")
    dependency_output.write_text(json.dumps({
        "schema": 1, "kind": "release_readiness_dependencies",
        "dependencies": dependencies,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["dependency_manifest"] = relative(dependency_output)
    result["dependency_manifest_sha256"] = sha256(dependency_output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": result["success"], "release_ready": result["release_ready"],
        "board_sha256": result["board_sha256"], "blocker_count": result["blocker_count"],
        "blockers": [item["code"] for item in result["blockers"]],
        "next_action": result["next_action"], "next_command": result["next_command"],
        "output": str(output),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
