#!/usr/bin/env python3
"""Run independent KiCad DRC and render checks on an isolated electrical candidate.

This script never imports a candidate into the reviewed KiCad layout and never
creates manufacturing evidence. A non-zero exit status means the candidate
still needs resolution before a human may consider importing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        return 127, str(exc)
    return result.returncode, result.stdout


def configured_kicad_cli() -> str:
    try:
        policy = json.loads((ROOT / "config/toolchain-policy.json").read_text(encoding="utf-8"))
        configured = policy.get("kicad", {}).get("cli")
        if isinstance(configured, str) and configured:
            return configured
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return "kicad-cli"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True, help="Electrical build receipt")
    parser.add_argument("--output", type=Path, required=True, help="New directory for DRC, render, and review receipt")
    parser.add_argument("--kicad-cli", default=configured_kicad_cli())
    args = parser.parse_args()

    receipt_path = args.receipt.resolve()
    output = args.output.resolve()
    result: dict[str, object] = {
        "schema": 1,
        "kind": "electrical_candidate_review",
        "success": False,
        "review_pass": False,
        "release_ready": False,
        "invariant": "Candidate review never imports a board or creates manufacturing evidence.",
        "build_receipt": str(receipt_path),
    }
    try:
        build = json.loads(receipt_path.read_text(encoding="utf-8"))
        board_value = build["candidate_board"]
        if not isinstance(board_value, str):
            raise ValueError("candidate_board is absent")
        board = Path(board_value).resolve()
        board.relative_to(ROOT / "build")
        if not board.is_file():
            raise ValueError("candidate_board does not exist")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result["error"] = f"invalid_build_receipt: {exc}"
        write_json(output / "candidate-review.json", result)
        print(json.dumps(result, separators=(",", ":")))
        return 1
    if output.exists() and any(output.iterdir()):
        result["error"] = "review_output_exists"
        result["next"] = "Choose a new review directory; do not overwrite candidate evidence."
        write_json(output / "candidate-review.json", result)
        print(json.dumps(result, separators=(",", ":")))
        return 1
    output.mkdir(parents=True, exist_ok=True)

    drc_report = output / "candidate-drc.json"
    render = output / "candidate-top.png"
    drc_code, drc_log = run([args.kicad_cli, "pcb", "drc", "--format", "json", "--output", str(drc_report), str(board)])
    render_code, render_log = run([
        args.kicad_cli, "pcb", "render", "--output", str(render), "--width", "1200", "--height", "800", "--side", "top", str(board),
    ])
    result.update({
        "candidate_board": str(board),
        "candidate_board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
        "electrical_backend": build.get("backend"),
        "drc_report": str(drc_report),
        "render": str(render),
        "commands": {"drc_exit_code": drc_code, "render_exit_code": render_code},
        "logs": {"drc_tail": drc_log.splitlines()[-12:], "render_tail": render_log.splitlines()[-12:]},
    })
    try:
        drc = json.loads(drc_report.read_text(encoding="utf-8"))
        violations = drc.get("violations", []) if isinstance(drc, dict) else []
        unconnected = drc.get("unconnected_items", []) if isinstance(drc, dict) else []
        if not isinstance(violations, list) or not isinstance(unconnected, list):
            raise ValueError("KiCad DRC output has unexpected shape")
        drc_summary = {
            "violations": len(violations),
            "unconnected": len(unconnected),
            "violation_types": sorted({str(item.get("type", "unknown")) for item in violations if isinstance(item, dict)}),
        }
        result["drc"] = drc_summary
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result["error"] = f"unreadable_drc_report: {exc}"
        write_json(output / "candidate-review.json", result)
        print(json.dumps(result, separators=(",", ":")))
        return 1
    review_pass = drc_code == 0 and render_code == 0 and drc_summary["violations"] == 0 and drc_summary["unconnected"] == 0
    result["success"] = drc_code == 0 and render_code == 0
    result["review_pass"] = review_pass
    if not review_pass:
        result["next"] = "Resolve every candidate DRC issue and inspect the render before deliberate KiCad import; do not alter a release baseline."
    write_json(output / "candidate-review.json", result)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if review_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
