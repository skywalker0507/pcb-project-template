#!/usr/bin/env python3
"""Prove that final physical editing preserved the immutable electrical freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_easyeda_import_parity import compare_electrical, file_sha256, load_manifest
from electrical_freeze_contract import DEFAULT_FREEZE_ROOT, load_release_freeze


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--electrical-freeze", type=Path, required=True)
    parser.add_argument("--final-manifest", type=Path, required=True)
    parser.add_argument("--board", type=Path, required=True, help="Final KiCad interchange board represented by final-manifest")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-root", type=Path, default=DEFAULT_FREEZE_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    freeze_path = args.electrical_freeze.resolve()
    freeze, frozen = load_release_freeze(freeze_path, args.freeze_root)
    frozen_manifest_path = frozen["frozen_manifest"]
    final_manifest_path = args.final_manifest.resolve()
    board = args.board.resolve()
    if not frozen_manifest_path.is_file() or not final_manifest_path.is_file() or not board.is_file():
        raise SystemExit("Frozen manifest, final manifest, or final board is unavailable")
    board_hash = hashlib.sha256(board.read_bytes()).hexdigest().upper()
    frozen_manifest, final_manifest = load_manifest(frozen_manifest_path), load_manifest(final_manifest_path)
    if frozen_manifest.get("board_sha256") != freeze.get("frozen_board_sha256"):
        raise SystemExit("Frozen electrical manifest does not match the frozen board SHA-256")
    if final_manifest.get("board_sha256") != board_hash:
        raise SystemExit("Final electrical manifest does not match the final board SHA-256")
    result = compare_electrical(frozen_manifest, final_manifest)
    payload = {
        "schema": 1,
        "kind": "final_electrical_parity",
        "success": result["electrical_parity_pass"],
        "final_electrical_parity_pass": result["electrical_parity_pass"],
        **result,
        "electrical_freeze_sha256": file_sha256(freeze_path),
        "frozen_manifest_sha256": file_sha256(frozen_manifest_path),
        "final_manifest_sha256": file_sha256(final_manifest_path),
        "frozen_board_sha256": freeze.get("frozen_board_sha256"),
        "final_board_sha256": board_hash,
        "limits": "This gate compares electrical identity/topology only; placement, routing geometry, DRC, and DFM remain separate gates.",
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
