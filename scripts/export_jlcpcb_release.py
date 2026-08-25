#!/usr/bin/env python3
"""Deterministically export core JLCPCB Gerber, drill, BOM and CPL files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcb_policy import configured_board, load_acceptance, require_no_prohibited_evidence, selected_manufacturing_profile
from design_policy import DEFAULT_OUTPUT as DEFAULT_POLICY, require_policy
from stage_policy import require_operation
from check_jlcpcb_release import package_report


ROOT = Path(__file__).resolve().parents[1]
KICAD_BIN = Path(r"C:\Program Files\KiCad\10.0\bin")
STACK_PATH = ROOT / "config/board-stack.json"
MANUFACTURING_PROFILE_PATH = ROOT / "config/manufacturing-profile.json"
ACCEPTANCE_PATH = ROOT / "config/pcb-acceptance.json"
DEFAULT_REVIEW = ROOT / "build/07-release/evidence/manual/physical-review.json"
DEFAULT_MANUFACTURING_GATE = ROOT / "build/07-release/evidence/manual/manufacturing-gate.json"
DEFAULT_ASSEMBLY_PARTS = ROOT / "config/jlcpcb-parts.json"
DEFAULT_MANUFACTURING_REGISTRY = ROOT / "build/00-shared/evidence/design-policy/manufacturing-registry.json"
DEFAULT_REGISTRY_PRE_EXPORT = ROOT / "build/07-release/evidence/manual/manufacturing-registry-pre-export.json"
DEFAULT_GERBER_RULES = ROOT / "build/07-release/evidence/manual/manufacturing-registry-gerber.json"
DEFAULT_FOOTPRINT_EQUIVALENCE = ROOT / "build/07-release/evidence/manual/footprint-equivalence.json"
DEFAULT_PHYSICAL_INTEGRITY = ROOT / "build/07-release/evidence/manual/physical-integrity.json"


def run(log, *args: object) -> None:
    result = subprocess.run(
        [str(arg) for arg in args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log.write("$ " + " ".join(str(arg) for arg in args) + "\n")
    log.write(result.stdout)
    log.flush()
    if result.returncode:
        tail = "\n".join(result.stdout.splitlines()[-20:])
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}\n{tail}")


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def require_passing_audit(board: Path, audit_path: Path) -> dict[str, object]:
    if not audit_path.is_file():
        raise SystemExit(f"Release audit receipt not found: {audit_path}")
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Release audit receipt is invalid JSON: {audit_path}") from exc
    board_hash = hashlib.sha256(board.read_bytes()).hexdigest().upper()
    if audit.get("board_sha256") != board_hash:
        raise SystemExit("Release audit does not match this board SHA-256; rerun the PCB pipeline")
    if audit.get("release_pass") is not True:
        raise SystemExit("Release audit did not pass; refusing manufacturing export")
    return audit


def require_passing_manufacturing_gate(board: Path, gate_path: Path) -> dict[str, object]:
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Manufacturing gate receipt is unavailable or invalid: {gate_path}: {exc}") from exc
    board_hash = hashlib.sha256(board.read_bytes()).hexdigest().upper()
    if gate.get("kind") != "manufacturing_gate" or gate.get("manufacturing_gate_pass") is not True:
        raise SystemExit("Physical/JLC manufacturing gates did not pass")
    if gate.get("board_sha256") != board_hash:
        raise SystemExit("Manufacturing gate receipt does not match this board SHA-256")
    return gate


def require_passing_footprint_equivalence(board: Path, receipt_path: Path) -> dict[str, object]:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Footprint equivalence receipt is unavailable or invalid: {receipt_path}: {exc}") from exc
    board_hash = hashlib.sha256(board.read_bytes()).hexdigest().upper()
    if receipt.get("kind") != "kicad_footprint_equivalence" or receipt.get("success") is not True:
        raise SystemExit("Critical footprint rule-area equivalence did not pass")
    if receipt.get("board_sha256") != board_hash:
        raise SystemExit("Footprint equivalence receipt does not match this board SHA-256")
    footprints = receipt.get("footprints")
    if not isinstance(footprints, list) or not footprints or not all(
        isinstance(item, dict) and item.get("critical_rule_area_pass") is True for item in footprints
    ):
        raise SystemExit("Footprint equivalence lacks passing critical rule-area evidence")
    return receipt


def require_passing_physical_integrity(board: Path, receipt_path: Path) -> dict[str, object]:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Physical integrity receipt is unavailable or invalid: {receipt_path}: {exc}") from exc
    board_hash = hashlib.sha256(board.read_bytes()).hexdigest().upper()
    if (
        receipt.get("kind") != "kicad_physical_integrity"
        or receipt.get("success") is not True
        or receipt.get("phase") != "release"
        or receipt.get("board_sha256") != board_hash
    ):
        raise SystemExit("Release physical integrity did not pass for this exact board")
    return receipt


def configured_layers(board: Path, stack_path: Path) -> str:
    """Return reviewed export layers, refusing a stack/profile mismatch."""
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
    active = stack.get("active")
    profile = stack.get("profiles", {}).get(active)
    if not isinstance(profile, dict):
        raise SystemExit(f"Invalid active board stack profile: {active!r}")
    copper = profile.get("copper_layers")
    gerber = profile.get("gerber_layers")
    if not isinstance(copper, list) or not isinstance(gerber, list):
        raise SystemExit("Board stack profile needs copper_layers and gerber_layers lists")
    board_text = board.read_text(encoding="utf-8", errors="replace")
    missing = [
        layer for layer in copper
        if not re.search(rf'"{re.escape(layer)}"\s+(?:signal|power)\b', board_text)
    ]
    if missing:
        raise SystemExit(
            "Board stack/profile mismatch; missing signal layers: " + ", ".join(missing)
        )
    return ",".join(gerber)


def require_manufacturing_profile(path: Path) -> dict[str, object]:
    return selected_manufacturing_profile(path)


def require_registry_pre_export(
    gate_path: Path,
    registry_path: Path,
    policy: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Manufacturing registry or pre-export receipt is unavailable or invalid: {exc}") from exc
    if not isinstance(gate, dict) or not isinstance(registry, dict):
        raise SystemExit("Manufacturing registry and pre-export receipt must be JSON objects")
    if (
        gate.get("kind") != "manufacturing_registry_pre_export_gate"
        or gate.get("success") is not True
        or gate.get("special_manufacturing_processes_confirmed") is not True
    ):
        raise SystemExit("Manufacturing registry pre-export rules did not pass")
    if gate.get("registry_sha256") != registry.get("registry_sha256"):
        raise SystemExit("Pre-export receipt does not match the manufacturing registry")
    if gate.get("policy_sha256") != policy.get("policy_sha256") or registry.get("policy_sha256") != policy.get("policy_sha256"):
        raise SystemExit("Manufacturing registry evidence does not match the current design policy")
    return gate, registry


def require_passing_physical_review(board: Path, review_path: Path, policy: dict[str, object]) -> dict[str, object]:
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Physical review receipt is unavailable or invalid: {review_path}: {exc}") from exc
    board_hash = hashlib.sha256(board.read_bytes()).hexdigest().upper()
    if review.get("review_pass") is not True or review.get("board_sha256") != board_hash:
        raise SystemExit("Physical review did not pass for this exact board SHA-256")
    if review.get("policy_sha256") != policy.get("policy_sha256"):
        raise SystemExit("Physical review does not match the current resolved design policy")
    if review.get("visual_review_pass") is not True or not review.get("evidence", {}).get("visual"):
        raise SystemExit("Physical review lacks a passing SHA-bound PCB visual review")
    return review


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--board",
        type=Path,
        default=configured_board(ROOT),
    )
    parser.add_argument("--board-stack", type=Path, default=STACK_PATH)
    parser.add_argument("--manufacturing-profile", type=Path, default=MANUFACTURING_PROFILE_PATH)
    parser.add_argument("--acceptance", type=Path, default=ACCEPTANCE_PATH)
    parser.add_argument("--design-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--physical-review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--manufacturing-gates", type=Path, default=DEFAULT_MANUFACTURING_GATE)
    parser.add_argument("--assembly-parts", type=Path, default=DEFAULT_ASSEMBLY_PARTS)
    parser.add_argument("--manufacturing-registry", type=Path, default=DEFAULT_MANUFACTURING_REGISTRY)
    parser.add_argument("--registry-pre-export", type=Path, default=DEFAULT_REGISTRY_PRE_EXPORT)
    parser.add_argument("--gerber-rules-output", type=Path, default=DEFAULT_GERBER_RULES)
    parser.add_argument("--footprint-equivalence", type=Path, default=DEFAULT_FOOTPRINT_EQUIVALENCE)
    parser.add_argument("--physical-integrity", type=Path, default=DEFAULT_PHYSICAL_INTEGRITY)
    parser.add_argument(
        "--audit-json",
        type=Path,
        default=ROOT / "build/07-release/evidence/manual/final-routing-audit.json",
        help="Passing audit receipt for this exact board.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "manufacturing/jlcpcb/project-rev-a",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=ROOT / "build/07-release/tmp/manual/jlcpcb-export.log",
    )
    args = parser.parse_args()
    require_operation("export")

    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    release_rules = load_acceptance(args.acceptance.resolve(), "release", {
        "release_pass", "board_sha256_match", "drc_zero_unconnected", "manufacturing_profile_selected",
        "review_pass", "review_receipt_matches_policy", "visual_review_pass",
        "electrical_freeze_pass", "jlc_dfm_pass", "dfm_order_profile_match",
        "final_electrical_parity_pass", "physical_integrity_pass", "critical_footprint_rule_areas_pass", "manufacturing_registry_pass", "gerber_rules_pass",
        "special_manufacturing_processes_confirmed",
    })
    require_no_prohibited_evidence(release_rules, {"matching_release_audit"})
    design_policy = require_policy("release", args.design_policy.resolve())
    audit = require_passing_audit(board, args.audit_json.resolve())
    manufacturing_gate = require_passing_manufacturing_gate(board, args.manufacturing_gates.resolve())
    footprint_equivalence = require_passing_footprint_equivalence(board, args.footprint_equivalence.resolve())
    physical_integrity = require_passing_physical_integrity(board, args.physical_integrity.resolve())
    review = require_passing_physical_review(board, args.physical_review.resolve(), design_policy)
    layers = configured_layers(board, args.board_stack.resolve())
    manufacturing_profile = require_manufacturing_profile(args.manufacturing_profile.resolve())
    registry_pre_export, manufacturing_registry = require_registry_pre_export(
        args.registry_pre_export.resolve(), args.manufacturing_registry.resolve(), design_policy,
    )
    output = args.output.resolve()
    expected_parent = (ROOT / "manufacturing/jlcpcb").resolve()
    if expected_parent not in output.parents:
        raise SystemExit(f"Refusing output outside {expected_parent}: {output}")

    output.mkdir(parents=True, exist_ok=True)
    staging = output / f".export-staging-{os.getpid()}"
    if staging.exists():
        raise SystemExit(f"Staging path already exists: {staging}")
    gerber = staging / "gerber"
    assembly = staging / "assembly"
    gerber.mkdir(parents=True)
    assembly.mkdir(parents=True)
    args.log.parent.mkdir(parents=True, exist_ok=True)

    cli = KICAD_BIN / "kicad-cli.exe"
    python = KICAD_BIN / "python.exe"
    try:
        with args.log.open("w", encoding="utf-8", newline="\n") as log:
            run(
                log, cli, "pcb", "export", "gerbers",
                "--output", gerber, "--layers", layers,
                "--subtract-soldermask", "--check-zones", board,
            )
            run(
                log, cli, "pcb", "export", "drill",
                "--output", gerber, "--format", "excellon",
                "--drill-origin", "absolute", "--excellon-zeros-format", "decimal",
                "--excellon-oval-format", "alternate", "--excellon-units", "mm",
                "--excellon-separate-th", "--generate-map", "--map-format", "pdf",
                board,
            )
            position = assembly / "default-all-smd-pos.csv"
            run(
                log, cli, "pcb", "export", "pos",
                "--output", position, "--side", "both", "--format", "csv",
                "--units", "mm", "--exclude-dnp", board,
            )
            run(
                log, python, ROOT / "scripts/generate-jlcpcb-assembly.py",
                "--parts-source", args.assembly_parts.resolve(),
                "--board-source", board, "--pos-source", position,
                "--output-dir", assembly,
            )

        validation_path = assembly / "assembly-validation.json"
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            mismatch = validation["bom_cpl_designators_match"]
            missing = mismatch["missing_from_cpl"]
            extra = mismatch["extra_in_cpl"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Assembly validation is missing or malformed: {validation_path}: {exc}"
            ) from exc
        if missing or extra:
            raise RuntimeError(f"BOM/CPL designator mismatch: missing={missing}, extra={extra}")

        staged_archive = staging / f"{output.name}-gerber.zip"
        with zipfile.ZipFile(staged_archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(gerber.iterdir()):
                if file.is_file():
                    bundle.write(file, file.name)

        with args.log.open("a", encoding="utf-8", newline="\n") as log:
            run(
                log, sys.executable, ROOT / "scripts/check_manufacturing_registry.py",
                "--phase", "gerber",
                "--registry", args.manufacturing_registry.resolve(),
                "--design-policy", args.design_policy.resolve(),
                "--gerber-dir", gerber,
                "--board", board,
                "--gerber-zip", staged_archive,
                "--output", args.gerber_rules_output.resolve(),
            )
        try:
            gerber_gate = json.loads(args.gerber_rules_output.resolve().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Manufacturing registry Gerber receipt is invalid: {exc}") from exc
        if (
            gerber_gate.get("gerber_rules_pass") is not True
            or gerber_gate.get("manufacturing_registry_pass") is not True
            or gerber_gate.get("board_sha256") != audit["board_sha256"]
            or gerber_gate.get("registry_sha256") != manufacturing_registry.get("registry_sha256")
        ):
            raise RuntimeError("Staged Gerbers did not pass the current manufacturing registry for this exact board")

        preflight = package_report(staging, args.board_stack.resolve(), args.gerber_rules_output.resolve())
        if preflight.get("success") is not True:
            raise RuntimeError("Staged JLCPCB package failed deterministic preflight: " + ", ".join(preflight.get("failures", [])))

        targets = [
            (gerber, output / "gerber"),
            (assembly, output / "assembly"),
            (staged_archive, output / f"{output.name}-gerber.zip"),
        ]
        backups: list[tuple[Path, Path]] = []
        committed: list[Path] = []
        try:
            for staged, target in targets:
                backup = output / f".{target.name}.backup-{os.getpid()}"
                if target.exists():
                    os.replace(target, backup)
                    backups.append((target, backup))
                os.replace(staged, target)
                committed.append(target)
        except Exception:
            for target in reversed(committed):
                remove_path(target)
            for target, backup in reversed(backups):
                os.replace(backup, target)
            raise
        else:
            for _, backup in backups:
                remove_path(backup)
    finally:
        remove_path(staging)

    archive = output / f"{output.name}-gerber.zip"
    committed_gerber = output / "gerber"
    print(
        json.dumps(
            {
                "gerber_files": len(
                    [path for path in committed_gerber.iterdir() if path.is_file()]
                ),
                "bom_groups": validation["bom_groups"],
                "assembly_designators": validation["assembly_designators"],
                "gerber_zip": str(archive),
                "log": str(args.log.resolve()),
                "board_sha256": audit["board_sha256"],
                "audit_json": str(args.audit_json.resolve()),
                "manufacturing_gate": str(args.manufacturing_gates.resolve()),
                "layers": layers.split(","),
                "manufacturing_profile": str(args.manufacturing_profile.resolve()),
                "manufacturer": manufacturing_profile["manufacturer"],
                "design_policy_sha256": design_policy["policy_sha256"],
                "physical_review": str(args.physical_review.resolve()),
                "review_pass": review["review_pass"],
                "manufacturing_registry": str(args.manufacturing_registry.resolve()),
                "manufacturing_registry_sha256": manufacturing_registry["registry_sha256"],
                "registry_pre_export": str(args.registry_pre_export.resolve()),
                "special_manufacturing_processes_confirmed": registry_pre_export["special_manufacturing_processes_confirmed"],
                "gerber_rules_gate": str(args.gerber_rules_output.resolve()),
                "manufacturing_registry_pass": gerber_gate["manufacturing_registry_pass"],
                "gerber_rules_pass": gerber_gate["gerber_rules_pass"],
                "gerber_keepout_pass": gerber_gate["gerber_keepout_pass"],
                "footprint_equivalence": str(args.footprint_equivalence.resolve()),
                "critical_footprint_rule_areas_pass": all(item["critical_rule_area_pass"] for item in footprint_equivalence["footprints"]),
                "physical_integrity": str(args.physical_integrity.resolve()),
                "physical_integrity_pass": physical_integrity["physical_integrity_pass"],
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
