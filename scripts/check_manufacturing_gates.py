#!/usr/bin/env python3
"""Validate compact EasyEDA/JLC receipts before a manufacturing export."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from electrical_freeze_contract import DEFAULT_FREEZE_ROOT, load_release_freeze


ROOT = Path(__file__).resolve().parents[1]


def board_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def receipt(path: Path, kind: str, pass_field: str, schemas: set[int] | None = None) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Required {kind} receipt is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") not in (schemas or {1}) or value.get("kind") != kind:
        raise SystemExit(f"Receipt kind mismatch for {path}; expected {kind}")
    if value.get("success") is not True or value.get(pass_field) is not True:
        raise SystemExit(f"Required gate did not pass: {kind}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--electrical-freeze", type=Path, required=True)
    parser.add_argument("--import-parity", type=Path, required=True)
    parser.add_argument("--final-electrical-parity", type=Path, required=True)
    parser.add_argument("--easyeda-drc", type=Path, required=True)
    parser.add_argument("--jlc-dfm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-root", type=Path, default=DEFAULT_FREEZE_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    board_hash = board_sha256(board)
    freeze_path = args.electrical_freeze.resolve()
    freeze, _ = load_release_freeze(freeze_path, args.freeze_root)
    parity = receipt(args.import_parity.resolve(), "easyeda_import_parity", "parity_pass")
    final_parity = receipt(args.final_electrical_parity.resolve(), "final_electrical_parity", "final_electrical_parity_pass")
    easyeda_drc = receipt(args.easyeda_drc.resolve(), "physical_drc", "drc_pass")
    jlc_dfm = receipt(args.jlc_dfm.resolve(), "manufacturing_dfm", "dfm_pass")
    if easyeda_drc.get("backend") != "easyeda_pro":
        raise SystemExit("Physical DRC receipt must come from the EasyEDA Pro backend")
    if jlc_dfm.get("backend") != "jlcpcb":
        raise SystemExit("Manufacturing DFM receipt must come from JLCPCB")
    if parity.get("source_board_sha256") != freeze.get("frozen_board_sha256"):
        raise SystemExit("EasyEDA import parity source does not match the electrically frozen candidate SHA-256")
    if parity.get("source_manifest_sha256") != freeze.get("frozen_manifest_sha256"):
        raise SystemExit("EasyEDA import parity does not consume the immutable frozen electrical manifest")
    freeze_hash = hashlib.sha256(freeze_path.read_bytes()).hexdigest().upper()
    if final_parity.get("electrical_freeze_sha256") != freeze_hash:
        raise SystemExit("Final electrical parity does not match the selected electrical freeze receipt")
    if final_parity.get("frozen_manifest_sha256") != freeze.get("frozen_manifest_sha256"):
        raise SystemExit("Final electrical parity does not use the immutable frozen electrical manifest")
    if final_parity.get("final_board_sha256") != board_hash:
        raise SystemExit("Final electrical parity does not match the release board SHA-256")
    for name, value in (("easyeda_drc", easyeda_drc), ("jlc_dfm", jlc_dfm)):
        if value.get("board_sha256") != board_hash:
            raise SystemExit(f"{name} receipt does not match the release board SHA-256")
    payload = {
        "schema": 1,
        "kind": "manufacturing_gate",
        "success": True,
        "manufacturing_gate_pass": True,
        "board_sha256": board_hash,
        "electrical_freeze_sha256": freeze_hash,
        "gates": ["electrical_freeze", "easyeda_import_parity", "final_electrical_parity", "easyeda_drc", "jlc_dfm"],
        "limits": "KiCad independent audit and BOM/PnP/Gerber package checks remain separate mandatory pipeline steps.",
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
