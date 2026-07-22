#!/usr/bin/env python3
"""Deterministically export core JLCPCB Gerber, drill, BOM and CPL files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcb_policy import load_acceptance, require_no_prohibited_evidence, selected_manufacturing_profile
from design_policy import DEFAULT_OUTPUT as DEFAULT_POLICY, require_policy
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]
KICAD_BIN = Path(r"C:\Program Files\KiCad\10.0\bin")
STACK_PATH = ROOT / "config/board-stack.json"
MANUFACTURING_PROFILE_PATH = ROOT / "config/manufacturing-profile.json"
ACCEPTANCE_PATH = ROOT / "config/pcb-acceptance.json"
DEFAULT_REVIEW = ROOT / "build/reports/physical-review.json"


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
    missing = [layer for layer in copper if f'"{layer}" signal' not in board_text]
    if missing:
        raise SystemExit(
            "Board stack/profile mismatch; missing signal layers: " + ", ".join(missing)
        )
    return ",".join(gerber)


def require_manufacturing_profile(path: Path) -> dict[str, object]:
    return selected_manufacturing_profile(path)


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
    return review


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--board",
        type=Path,
        default=ROOT / "elec/layout/default/default.kicad_pcb",
    )
    parser.add_argument("--board-stack", type=Path, default=STACK_PATH)
    parser.add_argument("--manufacturing-profile", type=Path, default=MANUFACTURING_PROFILE_PATH)
    parser.add_argument("--acceptance", type=Path, default=ACCEPTANCE_PATH)
    parser.add_argument("--design-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--physical-review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument(
        "--audit-json",
        type=Path,
        default=ROOT / "build/reports/final-routing-audit.json",
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
        default=ROOT / "build/logs/jlcpcb-export.log",
    )
    args = parser.parse_args()
    require_operation("export")

    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    release_rules = load_acceptance(args.acceptance.resolve(), "release", {
        "release_pass", "board_sha256_match", "drc_zero_unconnected", "manufacturing_profile_selected",
        "review_pass", "review_receipt_matches_policy",
    })
    require_no_prohibited_evidence(release_rules, {"matching_release_audit"})
    design_policy = require_policy("release", args.design_policy.resolve())
    audit = require_passing_audit(board, args.audit_json.resolve())
    review = require_passing_physical_review(board, args.physical_review.resolve(), design_policy)
    layers = configured_layers(board, args.board_stack.resolve())
    manufacturing_profile = require_manufacturing_profile(args.manufacturing_profile.resolve())
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
                "--units", "mm", "--smd-only", "--exclude-dnp", board,
            )
            run(
                log, python, ROOT / "scripts/generate-jlcpcb-assembly.py",
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
                "layers": layers.split(","),
                "manufacturing_profile": str(args.manufacturing_profile.resolve()),
                "manufacturer": manufacturing_profile["manufacturer"],
                "design_policy_sha256": design_policy["policy_sha256"],
                "physical_review": str(args.physical_review.resolve()),
                "review_pass": review["review_pass"],
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
