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


ROOT = Path(__file__).resolve().parents[1]


def load_receipt(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Electrical freeze receipt is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 2 or value.get("kind") != "electrical_freeze" or value.get("freeze_pass") is not True:
        raise SystemExit("Final electrical parity requires a passing schema-2 electrical freeze receipt")
    return value


def resolve_frozen(path_value: object) -> Path:
    path = Path(str(path_value))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--electrical-freeze", type=Path, required=True)
    parser.add_argument("--final-manifest", type=Path, required=True)
    parser.add_argument("--board", type=Path, required=True, help="Final KiCad interchange board represented by final-manifest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze_path = args.electrical_freeze.resolve()
    freeze = load_receipt(freeze_path)
    frozen_manifest_path = resolve_frozen(freeze.get("frozen_manifest"))
    final_manifest_path = args.final_manifest.resolve()
    board = args.board.resolve()
    if not frozen_manifest_path.is_file() or not final_manifest_path.is_file() or not board.is_file():
        raise SystemExit("Frozen manifest, final manifest, or final board is unavailable")
    if file_sha256(frozen_manifest_path) != freeze.get("frozen_manifest_sha256"):
        raise SystemExit("Frozen electrical manifest does not match its immutable freeze receipt")
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
