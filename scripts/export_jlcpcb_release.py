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


ROOT = Path(__file__).resolve().parents[1]
KICAD_BIN = Path(r"C:\Program Files\KiCad\10.0\bin")
LAYERS = ",".join(
    [
        "F.Cu",
        "In1.Cu",
        "In2.Cu",
        "B.Cu",
        "F.Paste",
        "B.Paste",
        "F.Silkscreen",
        "B.Silkscreen",
        "F.Mask",
        "B.Mask",
        "Edge.Cuts",
    ]
)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--board",
        type=Path,
        default=ROOT / "elec/layout/default/default.kicad_pcb",
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
                "--output", gerber, "--layers", LAYERS,
                "--subtract-soldermask", "--check-zones", args.board,
            )
            run(
                log, cli, "pcb", "export", "drill",
                "--output", gerber, "--format", "excellon",
                "--drill-origin", "absolute", "--excellon-zeros-format", "decimal",
                "--excellon-oval-format", "alternate", "--excellon-units", "mm",
                "--excellon-separate-th", "--generate-map", "--map-format", "pdf",
                args.board,
            )
            position = assembly / "default-all-smd-pos.csv"
            run(
                log, cli, "pcb", "export", "pos",
                "--output", position, "--side", "both", "--format", "csv",
                "--units", "mm", "--smd-only", "--exclude-dnp", args.board,
            )
            run(
                log, python, ROOT / "scripts/generate-jlcpcb-assembly.py",
                "--board-source", args.board, "--pos-source", position,
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
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
