#!/usr/bin/env python3
"""Generate an isolated Gerber/BOM/CPL bundle for external JLCPCB DFM only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from check_jlcpcb_release import package_report
from export_jlcpcb_release import configured_layers
from manufacturing_work_packet import (
    create_work_packet,
    load_object,
    write_observation_skeleton,
    write_quote_skeleton,
)
from release_inputs import register_release_input


ROOT = Path(__file__).resolve().parents[1]
KICAD_CLI = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
DEFAULT_ASSEMBLY_PARTS = ROOT / "config/jlcpcb-parts.json"
DEFAULT_MANUFACTURING_PROFILE = ROOT / "config/manufacturing-profile.json"


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


def write_deterministic_zip(source: Path, archive: Path) -> None:
    """Archive files without carrying host mtimes or platform-specific metadata."""
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9,
    ) as bundle:
        for path in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def normalize_kicad_export_metadata(folder: Path) -> None:
    """Remove volatile KiCad creation timestamps without changing manufacturing geometry."""
    fixed_iso = "1980-01-01T00:00:00+00:00"
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".gbrjob":
            value = json.loads(path.read_text(encoding="utf-8"))
            header = value.get("Header")
            if not isinstance(header, dict) or "CreationDate" not in header:
                raise SystemExit(f"KiCad Gerber job lacks Header.CreationDate: {path}")
            header["CreationDate"] = fixed_iso
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue
        text = path.read_text(encoding="utf-8")
        normalized = re.sub(
            r"(?m)^(G04 Created by KiCad .* date ).*(\*)$",
            r"\g<1>1980-01-01 00:00:00\2",
            text,
        )
        normalized = re.sub(
            r"(?m)^(%TF\.CreationDate,).*(\*%)$",
            rf"\g<1>{fixed_iso}\2",
            normalized,
        )
        normalized = re.sub(
            r"(?m)^(; DRILL file KiCad .* date ).*$",
            r"\g<1>1980-01-01T00:00:00",
            normalized,
        )
        normalized = re.sub(
            r"(?m)^(; #@! TF\.CreationDate,).*$",
            rf"\g<1>{fixed_iso}",
            normalized,
        )
        path.write_text(normalized, encoding="utf-8", newline="\n")


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


def reviewed_order_parameters(profile_path: Path) -> dict[str, object]:
    profile = load_object(profile_path, "Manufacturing profile")
    order_profile = profile.get("order_profile")
    if not isinstance(order_profile, dict) or order_profile.get("status") != "reviewed":
        raise SystemExit("Manufacturing profile needs a reviewed order_profile")
    fabrication = order_profile.get("fabrication")
    assembly = order_profile.get("assembly")
    if not isinstance(fabrication, dict) or not isinstance(assembly, dict):
        raise SystemExit("Reviewed order_profile needs fabrication and assembly objects")
    finish = fabrication.get("surface_finish")
    mask = fabrication.get("solder_mask_color")
    service = assembly.get("service")
    modules = assembly.get("required_dfm_modules")
    if not all(isinstance(value, str) and value for value in (finish, mask, service)):
        raise SystemExit("Reviewed order_profile is missing finish, mask, or assembly service")
    if not isinstance(modules, list) or not all(isinstance(item, str) and item for item in modules):
        raise SystemExit("Reviewed order_profile required_dfm_modules is invalid")
    return {
        "surface_finish": finish,
        "solder_mask_color": str(mask).lower(),
        "assembly_service": service,
        "required_dfm_modules": sorted(set(modules)),
    }


def reviewed_vendor_and_currency(profile_path: Path) -> tuple[str, str]:
    profile = load_object(profile_path, "Manufacturing profile")
    manufacturer = profile.get("manufacturer")
    cost_review = profile.get("cost_review")
    currency = cost_review.get("currency") if isinstance(cost_review, dict) else None
    if not isinstance(manufacturer, str) or not manufacturer.strip():
        raise SystemExit("Manufacturing profile needs a manufacturer")
    if not isinstance(currency, str) or not currency.strip():
        raise SystemExit("Manufacturing profile cost_review needs a currency")
    return manufacturer.strip().lower(), currency.strip().upper()


def stdout_index(payload: dict[str, object]) -> dict[str, object]:
    sanity = payload.get("package_sanity")
    sanity_checks = sanity.get("checks") if isinstance(sanity, dict) else {}
    return {
        "success": payload.get("success"),
        "release_approved": False,
        "board_sha256": payload.get("board_sha256"),
        "gerber_zip_sha256": payload.get("gerber_zip_sha256"),
        "gerber_file_count": payload.get("gerber_file_count"),
        "bom_cpl_designators_match": payload.get("bom_cpl_designators_match"),
        "package_failure_count": len(sanity.get("failures", [])) if isinstance(sanity, dict) else None,
        "gerber_rules_pass": payload.get("gerber_rules_pass"),
        "work_packet_content_sha256": payload.get("work_packet_content_sha256"),
        "work_packet_manifest": payload.get("work_packet_manifest"),
        "dfm_observation": payload.get("dfm_observation"),
        "quote_observation": payload.get("quote_observation"),
        "assembly_row_count": sanity_checks.get("assembly_row_count") if isinstance(sanity_checks, dict) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--audit-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--board-stack", type=Path, default=ROOT / "config/board-stack.json")
    parser.add_argument("--assembly-parts", type=Path, default=DEFAULT_ASSEMBLY_PARTS)
    parser.add_argument("--manufacturing-profile", type=Path, default=DEFAULT_MANUFACTURING_PROFILE)
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

    normalize_kicad_export_metadata(gerber)
    archive = output / "jlcpcb-dfm-candidate-gerber.zip"
    write_deterministic_zip(gerber, archive)
    vendor, quote_currency = reviewed_vendor_and_currency(args.manufacturing_profile.resolve())
    work_packet_path, work_packet = create_work_packet(
        output,
        board=board,
        audit=args.audit_json.resolve(),
        gerber_zip=archive,
        bom=assembly / "BOM_JLCPCB.csv",
        cpl=assembly / "CPL_JLCPCB.csv",
        vendor=vendor,
        quote_currency=quote_currency,
        order_parameters=reviewed_order_parameters(args.manufacturing_profile.resolve()),
    )
    observation_path = write_observation_skeleton(output, work_packet)
    quote_observation_path = write_quote_skeleton(output, work_packet)
    current_path = allowed_root / "jlc-dfm-work-packet-CURRENT.json"
    try:
        current_manifest = work_packet_path.relative_to(ROOT).as_posix()
        current_observation = observation_path.relative_to(ROOT).as_posix()
        current_quote_observation = quote_observation_path.relative_to(ROOT).as_posix()
    except ValueError:
        current_manifest = str(work_packet_path)
        current_observation = str(observation_path)
        current_quote_observation = str(quote_observation_path)
    current_payload = {
        "schema": 1,
        "kind": "manufacturing_work_packet_pointer",
        "manifest": current_manifest,
        "manifest_sha256": sha256(work_packet_path),
        "content_sha256": work_packet["content_sha256"],
        "observation": current_observation,
        "quote_observation": current_quote_observation,
        "board_sha256": board_hash,
    }
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
        "work_packet_manifest": str(work_packet_path),
        "work_packet_manifest_sha256": sha256(work_packet_path),
        "work_packet_content_sha256": work_packet["content_sha256"],
        "work_packet_current": None,
        "dfm_observation": str(observation_path),
        "quote_observation": str(quote_observation_path),
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
    if payload["success"] is True:
        current_path.write_text(
            json.dumps(current_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        payload["work_packet_current"] = str(current_path)
    args.receipt.resolve().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if payload["success"] is True:
        register_release_input("jlcpcb_dfm_candidate", args.receipt.resolve(), payload)
    print(json.dumps(stdout_index(payload), ensure_ascii=False, separators=(",", ":")))
    if gate.returncode or sanity["success"] is not True:
        print(gate.stdout[-1000:] + gate.stderr[-500:], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
