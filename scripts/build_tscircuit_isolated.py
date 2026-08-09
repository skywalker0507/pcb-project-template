#!/usr/bin/env python3
"""Build a tscircuit design into an isolated KiCad candidate directory.

This script deliberately never writes elec/layout/default.  The tscircuit
package owns Circuit JSON and a generated KiCad candidate; KiCad remains the
authority for a reviewed physical board.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from electrical_backend import ROOT, read_config, selected_backend, source_sha256


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def write_receipt(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def footprint_forms(board_text: str):
    """Yield complete KiCad footprint forms without parsing an entire board AST."""
    for match in re.finditer(r"\(footprint\s+", board_text):
        depth = 0
        quoted = False
        escaped = False
        for index in range(match.start(), len(board_text)):
            char = board_text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield board_text[match.start(): index + 1]
                    break


def footprint_geometry_signature(form: str) -> tuple[str, ...]:
    """Return the copper/silkscreen geometry that defines a footprint library item.

    tscircuit embeds instance-specific properties (reference, value and UUIDs)
    in every board footprint.  Those are intentionally not part of the library
    identity: two 0402 capacitors with different values have the same land
    pattern.  Keep a conflict failure for a genuine pad or drawing mismatch.
    """
    geometry: list[str] = []
    for match in re.finditer(r"\(pad\b", form):
        depth = 0
        quoted = False
        escaped = False
        for index in range(match.start(), len(form)):
            char = form[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    pad = form[match.start(): index + 1]
                    pad = re.sub(r"\s*\(net\s+\d+\s+\"[^\"]*\"\)", "", pad)
                    pad = re.sub(r"\s*\(uuid\s+[^)]+\)", "", pad)
                    pad = re.sub(
                        r"-?\d+\.\d+",
                        lambda value: format(float(value.group()), ".6g"),
                        pad,
                    )
                    geometry.append(pad)
                    break
    return tuple(geometry)


def materialize_footprint_library(candidate_board: Path, output: Path) -> Path | None:
    """Vendor tscircuit's embedded footprint forms for KiCad DRC resolution."""
    text = candidate_board.read_text(encoding="utf-8")
    library: dict[str, str] = {}
    for form in footprint_forms(text):
        name_match = re.match(r'\(footprint\s+"tscircuit:([^"\s]+)"', form)
        if not name_match:
            continue
        name = name_match.group(1)
        normalized = re.sub(r'^(\(footprint\s+)"tscircuit:[^"]+"', rf'\1"{name}"', form, count=1)
        # Board footprints carry absolute placement; .kicad_mod library entries
        # must be normalized to their local origin or KiCad reports a mismatch.
        normalized = re.sub(
            r'(\(footprint\s+"[^"]+".*?\n\s*\(at\s+)[^)]+\)',
            r'\g<1>0 0 0)',
            normalized,
            count=1,
            flags=re.DOTALL,
        )
        existing = library.get(name)
        if existing is not None and footprint_geometry_signature(existing) != footprint_geometry_signature(normalized):
            raise SystemExit(f"tscircuit emitted conflicting embedded footprint geometry for {name}")
        library.setdefault(name, normalized)
    if not library:
        return None
    pretty = output / "tscircuit.pretty"
    pretty.mkdir(exist_ok=False)
    for name, form in library.items():
        (pretty / f"{name}.kicad_mod").write_text(form + "\n", encoding="utf-8")
    table = output / "fp-lib-table"
    table.write_text(
        "(fp_lib_table\n"
        "  (lib (name \"tscircuit\")(type \"KiCad\")(uri \"${KIPRJMOD}/tscircuit.pretty\")(options \"\")(descr \"Generated from the tscircuit candidate\"))\n"
        ")\n",
        encoding="utf-8",
    )
    return pretty


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", default="default")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--entry", type=Path, help="Optional source-root-relative test or design entry override")
    parser.add_argument("--bun", default="bun", help="Native Bun executable; never runs through WSL")
    args = parser.parse_args()

    config = read_config()
    name, backend = selected_backend(config, "tscircuit")
    source_root = (ROOT / str(backend["source_root"])).resolve()
    entry = (source_root / args.entry).resolve() if args.entry else source_root / str(backend["entry"])
    try:
        entry.relative_to(source_root)
    except ValueError as exc:
        raise SystemExit("tscircuit entry override must stay within tscircuit/") from exc
    output = args.output.resolve()
    receipt = args.receipt.resolve()
    expected = {
        "candidate_board": output / "candidate.kicad_pcb",
        "candidate_schematic": output / "candidate.kicad_sch",
        "candidate_project": output / "candidate.kicad_pro",
        "circuit_json": output / "circuit.json",
    }
    payload: dict[str, object] = {
        "schema": 1,
        "kind": "electrical_build",
        "backend": name,
        "build": args.build,
        "source_root": str(source_root.relative_to(ROOT).as_posix()),
        "entry": str(entry.relative_to(source_root).as_posix()),
        "output": str(output),
        "success": False,
        "isolation": "Generated candidates only; elec/layout/default is never an output.",
    }
    if not source_root.is_dir() or not (source_root / "package.json").is_file() or not entry.is_file():
        payload["error"] = "tscircuit_source_unconfigured"
        payload["next"] = "Define tscircuit/src/main.tsx from reviewed requirements before attempting an electrical build."
        write_receipt(receipt, payload)
        print(json.dumps(payload, separators=(",", ":")))
        return 1
    if output.exists() and any(output.iterdir()):
        payload["error"] = "candidate_output_exists"
        payload["next"] = "Choose a new candidate output directory; do not overwrite an existing electrical build receipt."
        write_receipt(receipt, payload)
        print(json.dumps(payload, separators=(",", ":")))
        return 1
    output.mkdir(parents=True, exist_ok=True)
    command = [args.bun, "run", "build", "--", "--output", str(output), "--entry", str(entry)]
    try:
        process = subprocess.run(
            command, cwd=source_root, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
    except OSError as exc:
        payload["error"] = "bun_unavailable"
        payload["detail"] = str(exc)
        write_receipt(receipt, payload)
        print(json.dumps(payload, separators=(",", ":")))
        return 1
    log = output / "tscircuit-build.log"
    log.write_text(process.stdout, encoding="utf-8")
    payload["command"] = command
    payload["log"] = str(log)
    payload["exit_code"] = process.returncode
    if process.returncode != 0:
        payload["error"] = "tscircuit_build_failed"
        payload["tail"] = process.stdout.splitlines()[-12:]
        write_receipt(receipt, payload)
        print(json.dumps(payload, separators=(",", ":")))
        return process.returncode or 1
    missing = [name for name, path in expected.items() if not path.is_file()]
    if missing:
        payload["error"] = "tscircuit_artifacts_missing"
        payload["missing"] = missing
        write_receipt(receipt, payload)
        print(json.dumps(payload, separators=(",", ":")))
        return 1
    footprint_library = materialize_footprint_library(expected["candidate_board"], output)
    payload.update({
        "success": True,
        "source_sha256": source_sha256(source_root),
        **{name: str(path) for name, path in expected.items()},
        "footprint_library": str(footprint_library) if footprint_library else None,
        "artifacts_sha256": {name: digest(path) for name, path in expected.items()},
    })
    write_receipt(receipt, payload)
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
