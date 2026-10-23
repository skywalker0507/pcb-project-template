#!/usr/bin/env python3
"""Prove that a routed KiCad candidate preserved every footprint placement."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


KICAD_PYTHON = kicad_python()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    ensure_host_kicad_python()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, candidate, output = args.input.resolve(), args.candidate.resolve(), args.output.resolve()
    if not source.is_file() or not candidate.is_file():
        raise SystemExit("Input and candidate boards must exist")
    ensure_kicad_python()
    import pcbnew

    def placements(path: Path) -> dict[str, dict[str, object]]:
        board = pcbnew.LoadBoard(str(path))
        return {
            str(item.GetReference()): {
                "x_nm": item.GetPosition().x,
                "y_nm": item.GetPosition().y,
                "rotation_deg": round(float(item.GetOrientationDegrees()), 6),
                "layer": str(item.GetLayerName()),
                "locked": bool(item.IsLocked()),
            }
            for item in board.GetFootprints()
        }

    before, after = placements(source), placements(candidate)
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = [
        {"reference": ref, "input": before[ref], "candidate": after[ref]}
        for ref in sorted(set(before) & set(after))
        if before[ref] != after[ref]
    ]
    success = not missing and not added and not changed
    receipt = {
        "schema": 1,
        "kind": "kicad_placement_immutability",
        "success": success,
        "input": str(source),
        "input_sha256": sha256(source),
        "candidate": str(candidate),
        "candidate_sha256": sha256(candidate),
        "footprint_count": {"input": len(before), "candidate": len(after)},
        "locked_count": {
            "input": sum(bool(item["locked"]) for item in before.values()),
            "candidate": sum(bool(item["locked"]) for item in after.values()),
        },
        "missing": missing,
        "added": added,
        "changed": changed,
        "limits": "Footprint position, rotation, side and lock-state comparison only; copper, DRC and electrical parity are separate gates.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": success,
        "input_sha256": receipt["input_sha256"],
        "candidate_sha256": receipt["candidate_sha256"],
        "footprint_count": receipt["footprint_count"],
        "locked_count": receipt["locked_count"],
        "changed_count": len(changed),
        "output": str(output),
    }, separators=(",", ":")))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
