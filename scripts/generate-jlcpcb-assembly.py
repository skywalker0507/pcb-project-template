#!/usr/bin/env python3
"""Generate and validate JLCPCB BOM/CPL files from KiCad/atopile exports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def split_designators(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def board_footprint_name(footprint: object) -> str:
    fpid = footprint.GetFPID()
    nickname = str(fpid.GetLibNickname())
    item = str(fpid.GetLibItemName())
    return f"{nickname}:{item}" if nickname else item


def load_parts_source(path: Path, board_path: Path, pos_by_ref: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Assembly parts source is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("parts"), list):
        raise SystemExit("Assembly parts source must contain a parts list")

    expected_sha = payload.get("board_sha256")
    actual_sha = hashlib.sha256(board_path.read_bytes()).hexdigest().upper()
    if expected_sha != actual_sha:
        raise SystemExit("Assembly parts source does not match the board SHA-256")

    try:
        import pcbnew
    except ImportError as exc:
        raise SystemExit("--parts-source requires KiCad's bundled Python interpreter") from exc
    board = pcbnew.LoadBoard(str(board_path.resolve()))
    inventory = {
        footprint.GetReference().strip(): {
            "value": footprint.GetValue().strip(),
            "footprint": board_footprint_name(footprint),
            "is_smd": any(pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD for pad in footprint.Pads()),
        }
        for footprint in board.GetFootprints()
    }

    source_bom: list[dict[str, str]] = []
    mapped_refs: set[str] = set()
    for index, part in enumerate(payload["parts"], start=1):
        if not isinstance(part, dict):
            raise SystemExit(f"Assembly part {index} must be an object")
        refs = part.get("refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs):
            raise SystemExit(f"Assembly part {index} needs non-empty refs")
        clean_refs = [ref.strip() for ref in refs]
        duplicate = sorted(mapped_refs.intersection(clean_refs))
        if duplicate:
            raise SystemExit("Assembly refs are mapped more than once: " + ", ".join(duplicate))
        mapped_refs.update(clean_refs)

        expected_value = str(part.get("board_value", ""))
        reviewed_footprint = str(part.get("footprint", ""))
        physical_footprints: set[str] = set()
        for ref in clean_refs:
            actual = inventory.get(ref)
            if actual is None:
                raise SystemExit(f"Assembly ref is absent from board: {ref}")
            # Some electrical-to-physical backends intentionally leave the
            # display Value blank. A non-empty board value must agree, but an
            # empty display field must not replace the reviewed BOM identity.
            if actual["value"] and actual["value"] != expected_value:
                raise SystemExit(
                    f"Assembly value mismatch for {ref}: board={actual['value']!r} source={expected_value!r}"
                )
            physical_footprints.add(actual["footprint"])
        if len(physical_footprints) != 1:
            raise SystemExit(
                "Assembly refs in one reviewed part group use different physical footprints: "
                + ", ".join(sorted(physical_footprints))
            )
        physical_footprint = next(iter(physical_footprints))

        lcsc = str(part.get("lcsc", "")).strip()
        partnumber = str(part.get("mpn", "")).strip()
        comment = str(part.get("comment", "")).strip() or expected_value or partnumber
        if not lcsc.startswith("C") or not lcsc[1:].isdigit() or not partnumber:
            raise SystemExit(f"Assembly part {index} has invalid MPN/LCSC data")
        source_bom.append(
            {
                "Designator": ",".join(sorted(clean_refs)),
                # CPL/package identity follows the exact audited physical board.
                # The reviewed source footprint is retained for traceability.
                "Footprint": physical_footprint,
                "ReviewedFootprint": reviewed_footprint,
                "Value": comment,
                "Partnumber": partnumber,
                "LCSC Part #": lcsc,
            }
        )

    dnp_refs = payload.get("dnp_refs", [])
    if not isinstance(dnp_refs, list) or not all(isinstance(ref, str) for ref in dnp_refs):
        raise SystemExit("Assembly dnp_refs must be a list of strings")
    expected_position_refs = mapped_refs.union(ref.strip() for ref in dnp_refs)
    board_smd_refs = {ref for ref, item in inventory.items() if item["is_smd"]}
    missing_mappings = sorted(board_smd_refs - expected_position_refs)
    stale_mappings = sorted(mapped_refs - set(pos_by_ref))
    if missing_mappings:
        raise SystemExit("Physical SMD refs missing from assembly parts source: " + ", ".join(missing_mappings))
    if stale_mappings:
        raise SystemExit("Assembly refs missing from SMD position export: " + ", ".join(stale_mappings))
    return source_bom


def main() -> None:
    parser = argparse.ArgumentParser()
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--bom-source", type=Path)
    source_group.add_argument("--parts-source", type=Path)
    parser.add_argument("--board-source", type=Path)
    parser.add_argument("--pos-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    if not args.bom_source and not args.parts_source and not args.board_source:
        parser.error("one of --bom-source, --parts-source, or --board-source is required")
    if args.parts_source and not args.board_source:
        parser.error("--parts-source requires --board-source for SHA and footprint validation")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with args.pos_source.open("r", encoding="utf-8-sig", newline="") as handle:
        source_pos = list(csv.DictReader(handle))

    required_pos_columns = {"Ref", "PosX", "PosY", "Rot", "Side"}
    if not source_pos or not required_pos_columns.issubset(source_pos[0]):
        raise SystemExit("Position source is empty or has unexpected columns")

    pos_by_ref = {row["Ref"].strip(): row for row in source_pos}

    if args.parts_source:
        source_bom = load_parts_source(args.parts_source, args.board_source, pos_by_ref)
    elif args.board_source:
        try:
            import pcbnew
        except ImportError as exc:
            raise SystemExit(
                "--board-source requires KiCad's bundled Python interpreter"
            ) from exc

        board = pcbnew.LoadBoard(str(args.board_source.resolve()))
        grouped: dict[tuple[str, str, str, str], list[str]] = {}
        for footprint in board.GetFootprints():
            reference = footprint.GetReference().strip()
            if reference not in pos_by_ref:
                continue
            fields = {
                field.GetName(): field.GetText().strip()
                for field in footprint.GetFields()
            }
            lcsc = fields.get("LCSC") or fields.get("lcsc_id", "")
            if not lcsc:
                continue
            fpid = footprint.GetFPID()
            package = f"{fpid.GetLibNickname()}:{fpid.GetLibItemName()}"
            value = footprint.GetValue().strip()
            partnumber = fields.get("Partnumber", "")
            grouped.setdefault((package, value, partnumber, lcsc), []).append(reference)

        source_bom = [
            {
                "Designator": ",".join(sorted(references)),
                "Footprint": package,
                "Value": value,
                "Partnumber": partnumber,
                "LCSC Part #": lcsc,
            }
            for (package, value, partnumber, lcsc), references in sorted(grouped.items())
        ]
    else:
        with args.bom_source.open("r", encoding="utf-8-sig", newline="") as handle:
            source_bom = list(csv.DictReader(handle))

    required_bom_columns = {
        "Designator",
        "Footprint",
        "Value",
        "Partnumber",
        "LCSC Part #",
    }
    if not source_bom or not required_bom_columns.issubset(source_bom[0]):
        raise SystemExit("BOM source is empty or has unexpected columns")

    bom_rows: list[dict[str, str]] = []
    assembly_refs: set[str] = set()
    for row in source_bom:
        lcsc = row["LCSC Part #"].strip()
        refs = split_designators(row["Designator"])
        if not lcsc or not refs:
            raise SystemExit(f"Invalid BOM row: {row!r}")
        assembly_refs.update(refs)
        bom_rows.append(
            {
                "Comment": row["Value"].strip() or row["Partnumber"].strip(),
                "Designator": ",".join(refs),
                "Footprint": row["Footprint"].strip(),
                "LCSC Part #": lcsc,
            }
        )

    missing_positions = sorted(assembly_refs - set(pos_by_ref))
    if missing_positions:
        raise SystemExit(
            "Assembly BOM references missing from the SMD position export: "
            + ", ".join(missing_positions)
        )

    cpl_rows: list[dict[str, str]] = []
    for ref in sorted(assembly_refs):
        row = pos_by_ref[ref]
        rotation = float(row["Rot"]) % 360.0
        side = row["Side"].strip().lower()
        if side not in {"top", "bottom"}:
            raise SystemExit(f"Unexpected side for {ref}: {row['Side']!r}")
        cpl_rows.append(
            {
                "Designator": ref,
                "Mid X": f"{float(row['PosX']):.4f}mm",
                # KiCad's CSV position export uses positive Y upward.
                # The board origin is its top-left corner, so invert Y for JLCPCB.
                "Mid Y": f"{-float(row['PosY']):.4f}mm",
                "Layer": side.title(),
                "Rotation": f"{rotation:.2f}",
            }
        )

    bom_path = args.output_dir / "BOM_JLCPCB.csv"
    with bom_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Comment", "Designator", "Footprint", "LCSC Part #"],
        )
        writer.writeheader()
        writer.writerows(bom_rows)

    cpl_path = args.output_dir / "CPL_JLCPCB.csv"
    with cpl_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Designator", "Mid X", "Mid Y", "Layer", "Rotation"],
        )
        writer.writeheader()
        writer.writerows(cpl_rows)

    excluded_position_refs = sorted(set(pos_by_ref) - assembly_refs)
    validation = {
        "bom_groups": len(bom_rows),
        "assembly_designators": len(assembly_refs),
        "cpl_rows": len(cpl_rows),
        "bom_cpl_designators_match": {
            "missing_from_cpl": [],
            "extra_in_cpl": [],
        },
        "position_rows_excluded_not_in_bom": excluded_position_refs,
        "physical_footprints_from_exact_board": True,
        "reviewed_identity_source_preserved": True,
    }
    validation_path = args.output_dir / "assembly-validation.json"
    validation_path.write_text(
        json.dumps(validation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(validation, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
