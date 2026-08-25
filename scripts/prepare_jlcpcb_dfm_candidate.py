#!/usr/bin/env python3
"""Generate an isolated Gerber/BOM/CPL bundle for external JLCPCB DFM only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

from check_jlcpcb_release import package_report
from export_jlcpcb_release import configured_layers
from release_inputs import register_release_input


ROOT = Path(__file__).resolve().parents[1]
KICAD_CLI = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
DEFAULT_ASSEMBLY_PARTS = ROOT / "config/jlcpcb-parts.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def require_passing_dfm_candidate_audit(board: Path, audit_path: Path) -> dict[str, object]:
    """Accept release evidence or a strict board-bound checkpoint for a non-release DFM bundle."""
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"DFM candidate audit is unavailable or invalid: {audit_path}: {exc}") from exc
    board_hash = sha256(board)
    if audit.get("board_sha256") == board_hash and audit.get("release_pass") is True:
        return audit
    counts = audit.get("candidate_counts")
    checkpoint_pass = bool(
        audit.get("candidate_sha256") == board_hash
        and audit.get("success") is True
        and audit.get("baseline_pass") is True
        and audit.get("unconnected_delta") == 0
        and audit.get("new_violations") == []
        and isinstance(counts, dict)
        and counts.get("unconnected_items") == 0
    )
    if not checkpoint_pass:
        raise SystemExit(
            "DFM candidate audit must be a passing release audit or an exact-board "
            "checkpoint with zero unconnected items and no new DRC violations"
        )
    return audit


def run(log, *argv: object) -> None:
    completed = subprocess.run(
        [str(item) for item in argv], cwd=ROOT, text=True,
        stdout=log, stderr=subprocess.STDOUT, check=False,
    )
    if completed.returncode:
        raise SystemExit(f"KiCad DFM candidate export failed with exit code {completed.returncode}")


def bind_assembly_parts_source(source: Path, destination: Path, board_sha256: str) -> dict[str, object]:
    """Create an auditable board-bound snapshot without mutating reviewed mappings."""
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Assembly parts source is unavailable or invalid: {source}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("parts"), list):
        raise SystemExit("Assembly parts source must contain a parts list")
    bound = dict(payload)
    bound["board_sha256"] = board_sha256
    bound["derived_from"] = str(source.resolve())
    bound["derived_from_sha256"] = sha256(source)
    destination.write_text(json.dumps(bound, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return bound


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--audit-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--board-stack", type=Path, default=ROOT / "config/board-stack.json")
    parser.add_argument("--assembly-parts", type=Path, default=DEFAULT_ASSEMBLY_PARTS)
    args = parser.parse_args()

    board = args.board.resolve()
    require_passing_dfm_candidate_audit(board, args.audit_json.resolve())
    output = args.output_dir.resolve()
    allowed_root = (ROOT / "build/07-release/tmp").resolve()
    if allowed_root not in output.parents:
        raise SystemExit(f"DFM candidate must remain under {allowed_root}")
    if output.exists():
        raise SystemExit(f"Refusing to overwrite an existing DFM candidate: {output}")

    layers = configured_layers(board, args.board_stack.resolve())
    board_hash = sha256(board)
    gerber = output / "gerber"
    assembly = output / "assembly"
    gerber.mkdir(parents=True)
    assembly.mkdir(parents=True)
    bound_parts = assembly / "assembly-parts-bound.json"
    bind_assembly_parts_source(args.assembly_parts.resolve(), bound_parts, board_hash)
    log_path = output / "kicad-export.log"
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        run(
            log, KICAD_CLI, "pcb", "export", "gerbers",
            "--output", gerber, "--layers", layers,
            "--subtract-soldermask", "--check-zones", board,
        )
        run(
            log, KICAD_CLI, "pcb", "export", "drill",
            "--output", gerber, "--format", "excellon",
            "--drill-origin", "absolute", "--excellon-zeros-format", "decimal",
            "--excellon-oval-format", "alternate", "--excellon-units", "mm",
            "--excellon-separate-th", board,
        )
        position = assembly / "default-all-smd-pos.csv"
        run(
            log, KICAD_CLI, "pcb", "export", "pos",
            "--output", position, "--side", "both", "--format", "csv",
            # Export all footprints because vendor libraries do not always set
            # the footprint-level SMD attribute. The assembly generator derives
            # the true SMT set from physical pad types and reviewed mappings.
            "--units", "mm", "--exclude-dnp", board,
        )
        run(
            log, KICAD_PYTHON, ROOT / "scripts/generate-jlcpcb-assembly.py",
            "--parts-source", bound_parts,
            "--board-source", board, "--pos-source", position,
            "--output-dir", assembly,
        )

    archive = output / "jlcpcb-dfm-candidate-gerber.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(gerber.iterdir()):
            if path.is_file():
                bundle.write(path, path.name)
    payload = {
        "schema": 2,
        "kind": "jlcpcb_dfm_candidate",
        "success": True,
        "release_approved": False,
        "board": str(board),
        "board_sha256": board_hash,
        "audit_json": str(args.audit_json.resolve()),
        "gerber_zip": str(archive),
        "gerber_zip_sha256": sha256(archive),
        "gerber_file_count": len([item for item in gerber.iterdir() if item.is_file()]),
        "bom": str(assembly / "BOM_JLCPCB.csv"),
        "bom_sha256": sha256(assembly / "BOM_JLCPCB.csv"),
        "cpl": str(assembly / "CPL_JLCPCB.csv"),
        "cpl_sha256": sha256(assembly / "CPL_JLCPCB.csv"),
        "assembly_validation": str(assembly / "assembly-validation.json"),
        "assembly_parts_snapshot": str(bound_parts),
        "assembly_parts_snapshot_sha256": sha256(bound_parts),
        "silkscreen_soldermask_subtracted": True,
        "limits": "PCB/SMT DFM upload input only. BOM/CPL are unapproved candidate evidence, not release files.",
    }
    args.receipt.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.receipt.resolve().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    gate_path = args.receipt.resolve().with_name(args.receipt.stem + "-manufacturing-registry-gerber.json")
    gate = subprocess.run([
        sys.executable, str(ROOT / "scripts/check_manufacturing_registry.py"),
        "--phase", "gerber",
        "--gerber-dir", str(gerber),
        "--dfm-candidate-receipt", str(args.receipt.resolve()),
        "--output", str(gate_path),
    ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    payload["manufacturing_registry_gerber_gate"] = str(gate_path)
    payload["gerber_rules_pass"] = gate.returncode == 0
    payload["gerber_keepout_pass"] = gate.returncode == 0
    sanity = package_report(output, args.board_stack.resolve(), gate_path)
    payload["package_sanity"] = sanity
    payload["bom_cpl_designators_match"] = sanity["checks"]["bom_cpl_designators_match"]
    payload["success"] = gate.returncode == 0 and sanity["success"] is True
    args.receipt.resolve().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    register_release_input("jlcpcb_dfm_candidate", args.receipt.resolve(), payload)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if gate.returncode or sanity["success"] is not True:
        print(gate.stdout[-1000:] + gate.stderr[-500:], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
