#!/usr/bin/env python3
"""Create a writable KiCad physical candidate from an immutable electrical freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from electrical_freeze_contract import DEFAULT_FREEZE_ROOT, load_release_freeze
from physical_backend import read_config, selected_backend
from stage_policy import current_stage
from electrical_backend import footprint_libraries, footprint_table, read_config as read_electrical, selected_backend as selected_electrical, source_sha256
from check_kicad_footprint_equivalence import libraries
from validate_requirements import problems as requirements_problems


ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = ROOT / "config/pcb-flow.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def latest_freeze_receipt(freeze_root: Path = DEFAULT_FREEZE_ROOT) -> Path:
    receipts = sorted(freeze_root.resolve().glob("*/electrical-freeze.json"), reverse=True)
    if not receipts:
        raise SystemExit(f"No electrical freeze receipt found below {freeze_root}")
    passing: list[Path] = []
    for receipt in receipts:
        try:
            load_release_freeze(receipt, freeze_root)
        except SystemExit:
            continue
        passing.append(receipt)
    if not passing:
        raise SystemExit(f"No passing immutable electrical freeze found below {freeze_root}")
    return passing[0]


def default_output() -> Path:
    flow = json.loads(FLOW_PATH.read_text(encoding="utf-8"))
    project = str(flow.get("project", {}).get("name", "pcb-project")).strip() or "pcb-project"
    stem = f"{project}-physical-rev-a"
    return ROOT / "elec" / "layout" / stem / f"{stem}.kicad_pcb"


def minimal_project(path: Path) -> None:
    path.write_text(json.dumps({
        "board": {}, "cvpcb": {}, "erc": {}, "libraries": {},
        "meta": {"filename": path.name, "version": 1}, "net_settings": {},
    }, indent=2) + "\n", encoding="utf-8")


def ensure_reviewed_fp_table(project_dir: Path, source_root: Path | None = None) -> tuple[Path, bool]:
    """Resolve configured resources and reject stale existing physical tables."""
    table = project_dir / "fp-lib-table"
    if source_root is None:
        _, backend = selected_electrical(read_electrical(ROOT / "config/electrical-backend.json"))
        source_root = (ROOT / backend["source_root"]).resolve()
    expected = footprint_libraries(source_root)
    if not expected:
        raise SystemExit(f"No authoritative local footprint libraries found under {source_root}")
    if table.is_file():
        actual = libraries(table, project_dir)
        stale = [name for name, path in expected.items() if actual.get(name) != path]
        if stale:
            raise SystemExit(f"Physical footprint table has stale or missing libraries: {', '.join(stale[:8])}. Review the table against authoritative source_root {source_root} before proceeding.")
        return table, False
    table.write_text(footprint_table(project_dir, source_root), encoding="utf-8")
    return table, True


def activate_board(board: Path) -> None:
    flow = json.loads(FLOW_PATH.read_text(encoding="utf-8"))
    flow["project"]["board"] = relative(board)
    FLOW_PATH.write_text(json.dumps(flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--electrical-freeze", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--freeze-root", type=Path, default=DEFAULT_FREEZE_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()

    stage = current_stage()
    if stage.get("stage") != "physical":
        raise SystemExit("KiCad physical candidates may be created only during the physical stage")
    backend_name, backend = selected_backend(read_config())
    if backend_name != "kicad" or backend.get("role") != "primary":
        raise SystemExit("The active primary physical backend must be kicad")

    try:
        requirements = json.loads((ROOT / "config/requirements.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Requirements are unavailable or invalid: {exc}") from exc
    if not isinstance(requirements, dict):
        raise SystemExit("Requirements must be an object")
    issues = requirements_problems(requirements, True, gate="physical_start")
    if issues:
        raise SystemExit("Requirements are not ready for physical start: " + "; ".join(issues[:8]))

    freeze_root = args.freeze_root.resolve()
    freeze_receipt = (args.electrical_freeze or latest_freeze_receipt(freeze_root)).resolve()
    freeze, artifacts = load_release_freeze(freeze_receipt, freeze_root)
    output = (args.output or default_output()).resolve()
    layout_root = (ROOT / "elec/layout").resolve()
    try:
        output.relative_to(layout_root)
    except ValueError as exc:
        raise SystemExit(f"Physical candidate must stay below {layout_root}") from exc
    if output.suffix != ".kicad_pcb":
        raise SystemExit("Physical candidate output must use the .kicad_pcb suffix")
    project = output.with_suffix(".kicad_pro")
    origin = output.with_suffix(".physical-origin.json")
    existing = [path for path in (output, project, origin) if path.exists()]
    if existing:
        raise SystemExit("Refusing to overwrite an existing physical candidate: " + ", ".join(map(str, existing)))

    frozen_source = freeze_receipt.parent / "provenance/source"
    if not frozen_source.is_dir() or source_sha256(frozen_source) != freeze.get("source_sha256"):
        raise SystemExit("Immutable freeze source resources are missing or changed")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Resolve/validate libraries before creating a board or a run. A stale
    # physical table must not leave a partial candidate that blocks a retry.
    fp_table, fp_table_created = ensure_reviewed_fp_table(output.parent, frozen_source)
    run = create_run("physical", "kicad-physical-candidate", args.run_id)
    shutil.copy2(artifacts["frozen_board"], output)
    frozen_copy_sha = sha256(output)
    minimal_project(project)
    kicad_cli = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
    subprocess.run([str(kicad_cli), "pcb", "upgrade", "--force", str(output)], cwd=ROOT, check=True)
    manifest = run.evidence / "kicad-physical-candidate-manifest.json"
    parity = run.evidence / "kicad-physical-candidate-parity.json"
    subprocess.run([
        sys.executable, "scripts/extract_kicad_parity_manifest.py",
        "--board", str(output), "--output", str(manifest),
    ], cwd=ROOT, check=True)
    subprocess.run([
        sys.executable, "scripts/check_easyeda_import_parity.py",
        "--source", str(artifacts["frozen_manifest"]),
        "--imported", str(manifest), "--output", str(parity),
    ], cwd=ROOT, check=True)
    integrity = run.evidence / "kicad-physical-integrity.json"
    footprint_equivalence = run.evidence / "footprint-equivalence.json"
    integrity_result = subprocess.run([
        str(kicad_cli.with_name("python.exe")), "scripts/check_kicad_physical_integrity.py",
        "--board", str(output), "--phase", "origin",
        "--output", str(integrity), "--footprint-output", str(footprint_equivalence),
    ], cwd=ROOT, check=False)
    if integrity_result.returncode:
        for created in (output, project):
            created.unlink(missing_ok=True)
        if fp_table_created:
            fp_table.unlink(missing_ok=True)
        finish_run(run, False, artifacts={
            "integrity": relative(integrity) if integrity.is_file() else None,
            "footprint_equivalence": relative(footprint_equivalence) if footprint_equivalence.is_file() else None,
        }, metrics={"failure": "physical_integrity"})
        raise SystemExit("Physical-origin integrity failed; inspect the compact receipt before placement")
    receipt = {
        "schema": 1,
        "kind": "kicad_physical_candidate",
        "success": True,
        "physical_backend": "kicad",
        "electrical_freeze": relative(freeze_receipt),
        "electrical_freeze_sha256": sha256(freeze_receipt),
        "frozen_board_sha256": freeze["frozen_board_sha256"],
        "candidate_initial_sha256": frozen_copy_sha,
        "candidate_sha256": sha256(output),
        "format_upgraded_by_kicad_cli": True,
        "parity_receipt": relative(parity),
        "physical_integrity_receipt": relative(integrity),
        "candidate_board": relative(output),
        "candidate_project": relative(project),
        "invariants": [
            "The immutable electrical freeze was not modified.",
            "Connectivity changes must return to reviewed atopile source and create a new freeze.",
            "This receipt proves candidate origin only; it is not placement, routing, DRC, or release approval.",
        ],
    }
    origin.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evidence = run.evidence / "kicad-physical-candidate.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.activate:
        activate_board(output)
    finish_run(run, True, artifacts={
        "receipt": relative(evidence), "candidate": relative(output), "project": relative(project),
        "manifest": relative(manifest), "parity": relative(parity),
        "integrity": relative(integrity), "footprint_equivalence": relative(footprint_equivalence),
    }, metrics={"candidate_sha256": receipt["candidate_sha256"]})
    print(json.dumps({**receipt, "receipt": relative(evidence), "activated": args.activate}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
