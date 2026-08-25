#!/usr/bin/env python3
"""Refresh exact-board electrical parity as one bounded verify operation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from artifact_store import create_run, finish_run, relative


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--electrical-freeze", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    configured = (flow.get("manufacturing") or {}).get("gate_receipts", {}).get("electrical_freeze")
    freeze = args.electrical_freeze.resolve() if args.electrical_freeze else (ROOT / str(configured)).resolve()
    if not freeze.is_file():
        raise SystemExit("Electrical freeze receipt is unavailable")
    run = create_run("release", "final-electrical-parity", args.run_id)
    manifest = run.tmp / "final-board-electrical-manifest.json"
    output = run.evidence / "final-electrical-parity.json"
    log = run.tmp / "parity.log"
    commands = [
        [sys.executable, str(ROOT / "scripts/extract_kicad_parity_manifest.py"), "--board", str(board), "--output", str(manifest)],
        [sys.executable, str(ROOT / "scripts/check_final_electrical_parity.py"), "--electrical-freeze", str(freeze), "--final-manifest", str(manifest), "--board", str(board), "--output", str(output)],
    ]
    lines: list[str] = []
    exit_code = 0
    for command in commands:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        lines.append(completed.stdout)
        lines.append(completed.stderr)
        if completed.returncode:
            exit_code = completed.returncode
            break
    log.write_text("".join(lines), encoding="utf-8")
    payload = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {}
    success = bool(exit_code == 0 and payload.get("final_electrical_parity_pass") is True)
    finish_run(
        run, success,
        artifacts={
            "final_electrical_parity": relative(output) if output.is_file() else None,
            "final_manifest": relative(manifest) if manifest.is_file() else None,
            "log": relative(log),
        },
        metrics={"board_sha256": payload.get("final_board_sha256")},
    )
    print(json.dumps({
        "success": success,
        "board_sha256": payload.get("final_board_sha256"),
        "output": relative(output) if output.is_file() else None,
        "log": relative(log),
    }, separators=(",", ":")))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
