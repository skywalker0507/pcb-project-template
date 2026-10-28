#!/usr/bin/env python3
"""Restore source footprint rule areas omitted by the atopile 0.15.8 schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from electrical_backend import ROOT, footprint_libraries, sha256, source_sha256
from check_electrical_candidate_integrity import audit
from check_kicad_footprint_equivalence import load_pcbnew


SUPPORTED_VERSION = "0.15.8"


def extract_parity(board: Path, destination: Path) -> dict:
    process = subprocess.run([
        sys.executable, str(ROOT / "scripts/extract_kicad_parity_manifest.py"),
        "--board", str(board), "--output", str(destination),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if process.returncode:
        raise ValueError("Parity extraction failed: " + (process.stdout + process.stderr)[-1200:])
    return json.loads(destination.read_text(encoding="utf-8"))


def normalize(pcbnew: object, original: Path, output: Path, source: Path, version: str, evidence: Path) -> dict:
    if not original.is_file() or output.exists() or original == output:
        raise ValueError("Normalization requires an existing compiler candidate and a new output")
    before, source_hash = sha256(original), source_sha256(source)
    inspected = audit(pcbnew, original, source)
    missing = [item for item in inspected["components"] if not item["success"]]
    allowed = all(item["failures"] == ["footprint_rule_areas_changed_or_missing"]
                  and item["candidate_rule_areas"] == 0 and item["source_rule_areas"] > 0 for item in missing)
    if not inspected["component_count"] or not inspected["read_only_inputs_unchanged"] or not allowed:
        raise ValueError("Compiler candidate has errors beyond wholly omitted source footprint rule areas")
    if missing and version != SUPPORTED_VERSION:
        raise ValueError(f"Rule-area recovery is only reviewed for atopile {SUPPORTED_VERSION}, got {version!r}")
    evidence.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    before_manifest = extract_parity(original, evidence / "compiler-parity.json")
    restored = []
    if missing:
        board = pcbnew.LoadBoard(str(original))
        if list(board.GetTracks()) or any(not zone.GetIsRuleArea() for zone in board.Zones()):
            raise ValueError("Recovery accepts only unrouted compiler candidates, never physical copper work")
        by_ref = {str(fp.GetReference()): fp for fp in board.GetFootprints()}
        libraries = footprint_libraries(source)
        for item in missing:
            embedded = by_ref[item["ref"]]
            fpid = embedded.GetFPID()
            directory = libraries[str(fpid.GetLibNickname())]
            library = pcbnew.FootprintLoad(str(directory), str(fpid.GetLibItemName()))
            library.SetParent(board)
            if library.IsFlipped() != embedded.IsFlipped():
                library.Flip(library.GetPosition(), False)
            library.SetOrientation(embedded.GetOrientation())
            library.SetPosition(embedded.GetPosition())
            for index, zone in enumerate(library.Zones()):
                if not zone.GetIsRuleArea():
                    raise ValueError("Only source rule areas may be recovered; footprint copper zones are unsupported")
                # Duplicate gives the new embedded area its own native UUID;
                # the copy constructor would reuse it across identical sockets.
                copied = pcbnew.Cast_to_ZONE(zone.Duplicate())
                copied.SetParent(embedded)
                embedded.Add(copied)
            restored.append({"ref": item["ref"], "source": item["source"],
                             "source_sha256": item["source_sha256"], "restored_rule_areas": item["source_rule_areas"]})
        pcbnew.SaveBoard(str(output), board)
    else:
        shutil.copy2(original, output)
    after_manifest = extract_parity(output, evidence / "normalized-parity.json")
    unchanged_parity = all(before_manifest[key] == after_manifest[key] for key in ("components", "nets", "board_outline", "holes"))
    checked = audit(pcbnew, output, source)
    audit_path = evidence / "normalization-integrity.json"
    audit_path.write_text(json.dumps(checked, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if sha256(original) != before or source_sha256(source) != source_hash:
        raise ValueError("Read-only compiler/source inputs changed during normalization")
    if not unchanged_parity or checked["success"] is not True:
        raise ValueError("Normalized candidate failed exact parity or source resource integrity")
    return {
        "schema": 1, "kind": "atopile_candidate_normalization", "success": True,
        "compiler_version": version, "source_sha256": source_hash,
        "compiler_candidate": str(original), "compiler_candidate_sha256": before,
        "candidate_board": str(output), "candidate_board_sha256": sha256(output),
        "kicad_version": str(pcbnew.Version()), "recovery_applied": bool(restored),
        "restored": restored, "restored_rule_area_count": sum(item["restored_rule_areas"] for item in restored),
        "exact_electrical_geometry_parity": unchanged_parity,
        "compiler_input_unchanged": True, "source_input_unchanged": True,
        "integrity_receipt": str(audit_path), "integrity_receipt_sha256": sha256(audit_path),
        "limits": "Source resource export compatibility only. No source edits, new rule values, board-level substitutes, physical adoption, or release approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--compiler-version", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    try:
        pcbnew = load_pcbnew(Path(__file__).resolve())
        output = args.output.resolve()
        output.relative_to((ROOT / "build").resolve())
        payload = normalize(pcbnew, args.input.resolve(), output, args.source_root.resolve(), args.compiler_version, args.receipt.resolve().parent / "normalization")
    except Exception as exc:
        payload = {"schema": 1, "kind": "atopile_candidate_normalization", "success": False,
                   "failure": type(exc).__name__, "evidence": str(exc).splitlines()[-6:]}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("success", "recovery_applied", "restored_rule_area_count", "failure", "evidence") if key in payload}, separators=(",", ":")))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
