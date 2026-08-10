#!/usr/bin/env python3
"""Create an immutable electrical-freeze receipt from reviewed candidate evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from electrical_backend import read_config, selected_backend


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-receipt", type=Path, required=True)
    parser.add_argument("--contract-receipt", type=Path, required=True)
    parser.add_argument("--candidate-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = read_config()
    backend_name, backend = selected_backend(config)
    if backend_name != "atopile" or backend.get("status") != "primary":
        raise SystemExit("Production electrical freeze requires the configured primary atopile backend")
    build_path = args.build_receipt.resolve()
    contract_path = args.contract_receipt.resolve()
    review_path = args.candidate_review.resolve()
    build = load(build_path, "Electrical build receipt")
    contract = load(contract_path, "Electrical contract receipt")
    review = load(review_path, "Candidate review receipt")
    if build.get("success") is not True or build.get("backend") != backend_name:
        raise SystemExit("Electrical build receipt is not a successful build from the active primary backend")
    if contract.get("success") is not True or contract.get("contract_pass") is not True:
        raise SystemExit("Electrical contract validation did not pass")
    if review.get("review_pass") is not True:
        raise SystemExit("Electrical candidate review did not pass")
    board = Path(str(build.get("candidate_board", ""))).resolve()
    if not board.is_file():
        raise SystemExit("Electrical candidate board is unavailable")
    board_hash = sha256(board)
    for label, value in (("contract", contract), ("candidate review", review)):
        if Path(str(value.get("candidate_board", ""))).resolve() != board:
            raise SystemExit(f"{label} does not reference the built candidate board")
        if value.get("candidate_board_sha256") != board_hash:
            raise SystemExit(f"{label} does not match the candidate board SHA-256")
    contract_meta = build.get("contract") if isinstance(build.get("contract"), dict) else {}
    source_hash = contract_meta.get("source_sha256")
    if not isinstance(source_hash, str) or not source_hash:
        raise SystemExit("Electrical build receipt is missing the reviewed source SHA-256")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Electrical freeze receipt already exists; use a new path: {output}")
    payload = {
        "schema": 1,
        "kind": "electrical_freeze",
        "success": True,
        "freeze_pass": True,
        "backend": backend_name,
        "source_sha256": source_hash,
        "candidate_board": str(board),
        "candidate_board_sha256": board_hash,
        "build_receipt_sha256": sha256(build_path),
        "contract_receipt_sha256": sha256(contract_path),
        "candidate_review_sha256": sha256(review_path),
        "invariant": "Any electrical connectivity, reference, net-name, footprint, or exact-part change requires a new atopile build, validation, review, and freeze.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
