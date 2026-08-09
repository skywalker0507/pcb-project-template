#!/usr/bin/env python3
"""Build atopile in WSL without allowing it to overwrite the routed KiCad PCB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def windows_to_wsl(path: Path) -> str:
    match = re.fullmatch(r"([A-Za-z]):\\(.*)", str(path.resolve()))
    if not match:
        raise SystemExit(f"Cannot convert path to WSL form: {path}")
    drive, tail = match.groups()
    return f"/mnt/{drive.lower()}/{tail.replace(os.sep, '/')}"


def classify_failure(log: str) -> dict[str, object] | None:
    rules = [
        ("component_data_unreachable", r"Fetching component data failed|Name or service not known", "Restore component-data access or provide a reviewed offline component cache; do not change electrical sources merely to bypass picking."),
        ("package_compiler_incompatible", r"trait not found|Incompatible interface types", "Review the exact upstream package/compiler compatibility issue; keep any local migration patch separately reviewed."),
        ("dependency_resolution", r"Failed to resolve.*dependency|ConnectError", "Check the local dependency cache and immutable package revision before retrying."),
        ("source_validation", r"SyntaxError|TypeError|Ercfault", "Inspect the cited .ato source location and resolve the electrical validation error."),
    ]
    for name, pattern, next_action in rules:
        if re.search(pattern, log, re.IGNORECASE):
            matches = [line.strip() for line in log.splitlines() if re.search(pattern, line, re.IGNORECASE)]
            return {"class": name, "evidence": matches[-3:], "next": next_action}
    if "Build failed" in log:
        return {"class": "unclassified_build_failure", "evidence": log.splitlines()[-8:], "next": "Inspect only the bounded build log tail, then add a deterministic classifier if this recurs."}
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", default="default")
    parser.add_argument("--board", type=Path)
    parser.add_argument(
        "--output", type=Path,
        help="Optional isolated candidate directory for a successful generated board.",
    )
    parser.add_argument(
        "--receipt", type=Path,
        help="Optional common electrical-backend receipt path.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=ROOT / "build/03-electrical/tmp/legacy-atopile/ato-build-isolated.log",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.build):
        raise SystemExit("Invalid build name")

    board = (
        args.board if args.board and args.board.is_absolute()
        else ROOT / args.board if args.board
        else ROOT / f"elec/layout/{args.build}/{args.build}.kicad_pcb"
    ).resolve()
    guard = board.with_suffix(f".codex-build-guard-{os.getpid()}.kicad_pcb.bak")
    stale_guards = sorted(board.parent.glob(f"{board.stem}.codex-build-guard-*.kicad_pcb.bak"))
    if not board.exists() and len(stale_guards) == 1:
        os.replace(stale_guards[0], board)
        print(f"RECOVERED_STALE_GUARD={stale_guards[0]}")
        stale_guards = []
    if stale_guards:
        raise SystemExit(
            "Stale build guards require manual inspection: "
            + ", ".join(str(path) for path in stale_guards)
        )
    if not board.is_file():
        raise SystemExit(f"Routed board not found: {board}")
    if guard.exists():
        raise SystemExit(f"Guard path already exists: {guard}")
    candidate_board = None
    if args.output:
        output = args.output.resolve()
        if output.exists() and any(output.iterdir()):
            raise SystemExit(f"Candidate output already exists and is not empty: {output}")
        output.mkdir(parents=True, exist_ok=True)
        candidate_board = output / "candidate.kicad_pcb"

    before = sha256(board)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    os.replace(board, guard)
    exit_code = 1
    try:
        command = (
            f"cd {windows_to_wsl(ROOT)} && "
            f"ato build --build {args.build}"
        )
        process = subprocess.Popen(
            ["wsl.exe", "bash", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        with args.log.open("w", encoding="utf-8", newline="\n") as log:
            for line in process.stdout:
                if args.verbose:
                    print(line, end="")
                log.write(line)
        exit_code = process.wait()
    finally:
        # The protected board restoration runs even if candidate capture fails.
        # A failed copy must never leave the manual KiCad board behind a guard.
        try:
            if board.exists():
                if exit_code == 0 and candidate_board:
                    if candidate_board.exists():
                        raise RuntimeError(f"Refusing to overwrite existing candidate board: {candidate_board}")
                    shutil.copy2(board, candidate_board)
                if args.verbose:
                    print(f"DISCARDING_GENERATED_BOARD={board}")
                board.unlink()
        finally:
            if not guard.exists():
                raise RuntimeError("Routed-board guard disappeared during build")
            os.replace(guard, board)

    after = sha256(board)
    if after != before:
        raise RuntimeError(f"Routed board changed: {before} -> {after}")
    log_text = args.log.read_text(encoding="utf-8", errors="replace")
    build_ids = re.findall(r"\bBuild ([0-9a-f]{8,})\b|\[([0-9a-f]{8,})\]", log_text)
    build_id = next((left or right for left, right in reversed(build_ids)), None)
    payload = {
        "schema": 1,
        "kind": "electrical_build",
        "backend": "atopile",
        "success": exit_code == 0,
        "exit_code": exit_code,
        "build": args.build,
        "build_id": build_id,
        "log": str(args.log.resolve()),
        "candidate_board": str(candidate_board.resolve()) if exit_code == 0 and candidate_board else None,
        "restored_board_sha256": after.upper(),
        "failure": classify_failure(log_text) if exit_code else None,
        "isolation": "Generated candidate copied before restoring the protected KiCad board.",
    }
    if args.receipt:
        args.receipt.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.receipt.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
