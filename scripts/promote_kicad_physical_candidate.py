#!/usr/bin/env python3
"""Promote an audited isolated KiCad placement candidate to the active board."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from artifact_store import create_run, finish_run, publish_stage_artifact, relative
from stage_policy import current_stage

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = kicad_python()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def configured_board() -> Path:
    flow = load_json(ROOT / "config" / "pcb-flow.json", "PCB flow")
    project = flow.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("board"), str):
        raise SystemExit("config/pcb-flow.json does not declare the active board")
    return (ROOT / project["board"]).resolve()


def validate_unconnected_policy(
    *,
    delta: int,
    allow_preroute_increase: bool,
    new_violations: list[object],
    placement: dict[str, object] | None,
    candidate_hash: str,
    target_hash: str,
    target_copper_items: int,
    candidate_copper_items: int,
) -> dict[str, object] | None:
    """Allow one placement-only ratsnest increase under exact, zero-copper evidence."""
    if delta <= 0:
        return None
    if not allow_preroute_increase:
        raise SystemExit("Candidate increases unconnected items")
    if delta != 1:
        raise SystemExit("The pre-route placement exception is limited to one unconnected item")
    if new_violations:
        raise SystemExit("The pre-route placement exception requires zero new DRC violations")
    if target_copper_items != 0 or candidate_copper_items != 0:
        raise SystemExit("The pre-route placement exception requires zero tracks and vias on both boards")
    if not isinstance(placement, dict):
        raise SystemExit("The pre-route placement exception requires an exact placement receipt")
    if (
        placement.get("kind") != "kicad_placement"
        or placement.get("success") is not True
        or placement.get("isolated_candidate") is not True
        or placement.get("before_sha256") != target_hash
        or placement.get("board_sha256") != candidate_hash
        or not isinstance(placement.get("placements"), list)
        or not placement["placements"]
    ):
        raise SystemExit("Placement receipt is not bound to the exact zero-copper candidate transition")
    return {
        "allowed": True,
        "unconnected_delta": delta,
        "reason": "Placement-only movement changed the unrouted ratsnest; no copper or DRC violation was added.",
        "target_copper_items": target_copper_items,
        "candidate_copper_items": candidate_copper_items,
    }


def copper_item_count(path: Path) -> int:
    import pcbnew  # type: ignore

    return sum(1 for _ in pcbnew.LoadBoard(str(path)).GetTracks())


def validate_routability_and_probes(
    routability: dict[str, object], probes: list[dict[str, object]],
    candidate_hash: str, required_groups: list[str],
    required_components: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    candidate = routability.get("candidate") if isinstance(routability.get("candidate"), dict) else {}
    comparison = routability.get("comparison") if isinstance(routability.get("comparison"), dict) else {}
    placement_hash = candidate.get("placement_sha256")
    if (
        routability.get("kind") != "kicad_placement_routability"
        or routability.get("success") is not True
        or candidate.get("board_sha256") != candidate_hash
        or not isinstance(placement_hash, str)
        or comparison.get("improved_without_critical_regression") is not True
    ):
        raise SystemExit("Physical promotion requires an improved SHA-bound placement-routability comparison")
    passed_groups: set[str] = set()
    passed_components: dict[str, set[str]] = {}
    for probe in probes:
        group = probe.get("route_area", probe.get("probe_group"))
        passed = probe.get("accepted") is True
        if probe.get("placement_sha256") == placement_hash and passed and isinstance(group, str):
            component = probe.get("probe_component")
            expected = (required_components or {}).get(group, [])
            if expected:
                if isinstance(component, str) and component in expected:
                    passed_components.setdefault(group, set()).add(component)
            else:
                passed_groups.add(group)
    component_failures: dict[str, list[str]] = {}
    for group, expected in (required_components or {}).items():
        if group not in required_groups:
            continue
        missing_components = sorted(set(expected) - passed_components.get(group, set()))
        if missing_components:
            component_failures[group] = missing_components
        else:
            passed_groups.add(group)
    missing = sorted(set(required_groups) - passed_groups)
    if missing:
        details = "; ".join(
            f"{group}=[{', '.join(component_failures[group])}]"
            for group in sorted(component_failures)
        )
        suffix = f"; missing components: {details}" if details else ""
        raise SystemExit("Physical promotion is missing passing critical route probes: " + ", ".join(missing) + suffix)
    return {
        "placement_sha256": placement_hash,
        "routability_improved": True,
        "required_probe_groups": required_groups,
        "passing_probe_groups": sorted(passed_groups),
        "passing_probe_components": {
            group: sorted(values) for group, values in sorted(passed_components.items())
        },
    }


def ensure_kicad_python() -> None:
    ensure_host_kicad_python()


def main() -> int:
    ensure_kicad_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--target-sha256", required=True)
    parser.add_argument("--visual-review", type=Path, required=True)
    parser.add_argument("--drc-comparison", type=Path, required=True)
    parser.add_argument("--electrical-parity", type=Path, required=True)
    parser.add_argument("--physical-integrity", type=Path, required=True)
    parser.add_argument("--placement-receipt", type=Path)
    parser.add_argument("--routability", type=Path, required=True)
    parser.add_argument("--critical-probe", action="append", type=Path, default=[], required=True)
    parser.add_argument(
        "--allow-preroute-unconnected-increase",
        action="store_true",
        help="Allow exactly +1 only for an exact receipt-bound zero-copper placement candidate.",
    )
    parser.add_argument("--run-id")
    args = parser.parse_args()

    if current_stage().get("stage") != "physical":
        raise SystemExit("Physical candidate promotion is allowed only during the physical stage")
    candidate = args.candidate.resolve()
    target = configured_board()
    if not candidate.is_file() or not target.is_file() or candidate == target:
        raise SystemExit("Candidate and active target must be distinct existing boards")
    candidate_hash = sha256(candidate)
    target_hash = sha256(target)
    if candidate_hash != args.candidate_sha256.upper():
        raise SystemExit("Candidate SHA-256 mismatch")
    if target_hash != args.target_sha256.upper():
        raise SystemExit("Active target SHA-256 mismatch")

    visual_path = args.visual_review.resolve()
    drc_path = args.drc_comparison.resolve()
    parity_path = args.electrical_parity.resolve()
    integrity_path = args.physical_integrity.resolve()
    placement_path = args.placement_receipt.resolve() if args.placement_receipt else None
    routability_path = args.routability.resolve()
    probe_paths = [path.resolve() for path in args.critical_probe]
    visual = load_json(visual_path, "Visual review")
    drc = load_json(drc_path, "DRC comparison")
    parity = load_json(parity_path, "Electrical parity")
    integrity = load_json(integrity_path, "Physical integrity")
    placement = load_json(placement_path, "Placement receipt") if placement_path else None
    routability = load_json(routability_path, "Placement routability")
    probes = [load_json(path, "Critical route probe") for path in probe_paths]
    if (
        integrity.get("kind") != "kicad_physical_integrity"
        or integrity.get("success") is not True
        or integrity.get("phase") != "placement"
        or integrity.get("board_sha256") != candidate_hash
    ):
        raise SystemExit("Physical promotion requires a passing placement-phase integrity receipt for the exact candidate")
    routability_policy = load_json(ROOT / "config/placement-routability.json", "Placement routability policy")
    required_probe_groups = [str(value) for value in (routability_policy.get("checkpoint") or {}).get("critical_probe_groups", [])]
    component_policy = (routability_policy.get("checkpoint") or {}).get("critical_probe_components") or {}
    required_probe_components = {
        str(group): [str(value) for value in values]
        for group, values in component_policy.items()
        if isinstance(values, list)
    }
    routability_gate = validate_routability_and_probes(
        routability, probes, candidate_hash, required_probe_groups, required_probe_components,
    )
    if visual.get("review_pass") is not True or visual.get("board_sha256") != candidate_hash:
        raise SystemExit("A passing visual review bound to the candidate is required")
    if visual.get("placement_semantics_pass") is not True:
        raise SystemExit("Physical promotion requires passing placement semantics bound through visual review")
    if drc.get("candidate_sha256") != candidate_hash:
        raise SystemExit("DRC comparison is not bound to the candidate")
    new_violations = drc.get("new_violations")
    if not isinstance(new_violations, list):
        raise SystemExit("DRC comparison has no bounded new-violation list")
    new_errors = [item for item in new_violations if isinstance(item, dict) and item.get("severity") == "error"]
    if new_errors:
        raise SystemExit("Candidate adds an electrical DRC error")
    unconnected_delta = int(drc.get("unconnected_delta", 1))
    preroute_exception = validate_unconnected_policy(
        delta=unconnected_delta,
        allow_preroute_increase=args.allow_preroute_unconnected_increase,
        new_violations=new_violations,
        placement=placement,
        candidate_hash=candidate_hash,
        target_hash=target_hash,
        target_copper_items=copper_item_count(target),
        candidate_copper_items=copper_item_count(candidate),
    )
    if parity.get("final_electrical_parity_pass") is not True or parity.get("final_board_sha256") != candidate_hash:
        raise SystemExit("Passing final electrical parity bound to the candidate is required")

    run = create_run("physical", "kicad-physical-promotion", args.run_id)
    backup_dir = run.tmp / "pre-promotion-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffixes = (".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_prl")
    backups: dict[str, str] = {}
    for suffix in suffixes:
        current = target.with_suffix(suffix)
        if current.is_file():
            backup = backup_dir / current.name
            shutil.copy2(current, backup)
            backups[suffix] = relative(backup)

    try:
        for suffix in suffixes:
            source = candidate.with_suffix(suffix)
            destination = target.with_suffix(suffix)
            if source.is_file():
                shutil.copy2(source, destination)
            elif suffix in {".kicad_pcb", ".kicad_pro", ".kicad_dru"}:
                raise RuntimeError(f"Required candidate companion is missing: {source}")
    except Exception:
        for suffix, backup_value in backups.items():
            shutil.copy2(ROOT / backup_value, target.with_suffix(suffix))
        raise

    promoted_hash = sha256(target)
    if promoted_hash != candidate_hash:
        raise SystemExit("Promoted board SHA-256 differs from the reviewed candidate")
    receipt = {
        "schema": 1,
        "kind": "kicad_physical_candidate",
        "operation": "audited_candidate_promotion",
        "success": True,
        "candidate": relative(candidate),
        "candidate_sha256": candidate_hash,
        "target": relative(target),
        "target_sha256_before": target_hash,
        "target_sha256_after": promoted_hash,
        "visual_review": relative(visual_path),
        "drc_comparison": relative(drc_path),
        "electrical_parity": relative(parity_path),
        "physical_integrity": relative(integrity_path),
        "placement_receipt": relative(placement_path) if placement_path else None,
        "routability": relative(routability_path),
        "critical_probes": [relative(path) for path in probe_paths],
        "routability_gate": routability_gate,
        "new_warning_count": len(new_violations),
        "new_error_count": len(new_errors),
        "unconnected_delta": unconnected_delta,
        "preroute_unconnected_exception": preroute_exception,
        "backups": backups,
        "limits": "Placement candidate promotion only; routing, return paths, final DRC, DFM and manufacturing release remain unapproved.",
    }
    evidence = run.evidence / "physical-candidate-promotion.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    published = publish_stage_artifact(
        run, target, "placed.kicad_pcb",
        evidence={"promotion_receipt": relative(evidence), "visual_review": relative(visual_path)},
    )
    receipt["published_stage_artifact"] = published["artifact"]
    receipt["published_stage_result"] = relative(run.release / "stage-result.json")
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={
        "receipt": relative(evidence), "working_board": relative(target),
        "published_board": str(published["artifact"]),
        "stage_result": relative(run.release / "stage-result.json"),
    }, metrics={"new_warnings": len(new_violations), "unconnected_delta": unconnected_delta})
    print(json.dumps({**receipt, "receipt": relative(evidence)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
