#!/usr/bin/env python3
"""Generate and validate JLCPCB BOM/CPL files from KiCad/atopile exports."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def split_designators(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--bom-source", type=Path)
    source_group.add_argument("--board-source", type=Path)
    parser.add_argument("--pos-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    with args.pos_source.open("r", encoding="utf-8-sig", newline="") as handle:
        source_pos = list(csv.DictReader(handle))

    required_pos_columns = {"Ref", "PosX", "PosY", "Rot", "Side"}
    if not source_pos or not required_pos_columns.issubset(source_pos[0]):
        raise SystemExit("Position source is empty or has unexpected columns")

    pos_by_ref = {row["Ref"].strip(): row for row in source_pos}

    if args.board_source:
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
    }
    validation_path = args.output_dir / "assembly-validation.json"
    validation_path.write_text(
        json.dumps(validation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(validation, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
