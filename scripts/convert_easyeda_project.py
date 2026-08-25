#!/usr/bin/env python3
"""Convert an immutable KiCad freeze to EPro with the official JLCEDA converter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from artifact_store import create_run, finish_run, relative, sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONVERTER = Path(r"C:\Program Files\lceda-pro-format-converter")
ADAPTER = ROOT / "scripts/easyeda_format_converter.js"


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Expected a JSON object: {path}")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/easyeda-integration.json")
    parser.add_argument("--converter-root", type=Path, default=DEFAULT_CONVERTER)
    parser.add_argument("--run-id")
    args = parser.parse_args()

    freeze_path = args.freeze.resolve()
    freeze = load_json(freeze_path)
    if freeze.get("kind") != "electrical_freeze" or freeze.get("freeze_pass") is not True:
        raise SystemExit("Input is not a passing electrical freeze receipt")
    source = (ROOT / str(freeze.get("frozen_board", ""))).resolve()
    expected = str(freeze.get("frozen_board_sha256", "")).upper()
    if not source.is_file() or not expected or digest(source) != expected:
        raise SystemExit("Immutable frozen board is missing or does not match its recorded SHA-256")

    integration = load_json(args.config.resolve())
    policy = integration.get("format_converter") or {}
    converter_root = args.converter_root.resolve()
    package = converter_root / "resources/app/package.json"
    engine_package = converter_root / "resources/node_modules/chamelon/package.json"
    executable = converter_root / "lceda-pro-format-converter.exe"
    if not package.is_file() or not engine_package.is_file() or not executable.is_file():
        raise SystemExit(f"Official JLCEDA format converter is not installed at {converter_root}")
    installed = str(load_json(package).get("version", ""))
    required = str(policy.get("required_version", ""))
    if required and installed != required:
        raise SystemExit(f"JLCEDA converter version mismatch: required {required}, installed {installed}")

    run = create_run("physical", "easyeda-official-convert", args.run_id)
    output = run.tmp / "frozen-official.epro"
    environment = dict(os.environ)
    environment["ELECTRON_RUN_AS_NODE"] = "1"
    command = [
        str(executable), str(ADAPTER),
        "--input", str(source),
        "--output", str(output),
        "--converter-root", str(converter_root),
        "--project-name", "frozen-official",
    ]
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=180,
    )
    if completed.returncode != 0:
        finish_run(run, False, metrics={"error": (completed.stderr or completed.stdout)[-500:]})
        raise SystemExit(f"Official JLCEDA conversion failed: {(completed.stderr or completed.stdout).strip()}")
    try:
        converter_receipt = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        finish_run(run, False, metrics={"error": "invalid_converter_receipt"})
        raise SystemExit("Official converter adapter returned no compact receipt") from exc
    if converter_receipt.get("success") is not True or not output.is_file():
        raise SystemExit("Official converter did not create a passing EPro candidate")

    receipt = {
        "schema": 1,
        "kind": "easyeda_official_import_candidate",
        "success": True,
        "source_freeze": relative(freeze_path),
        "source_board": relative(source),
        "source_board_sha256": expected,
        "candidate": relative(output),
        "candidate_sha256": sha256(output),
        "converter": {
            "product": "JLCEDA Format Converter",
            "version": installed,
            "executable_sha256": digest(executable),
            "engine_sha256": digest(converter_root / "resources/node_modules/chamelon/convert-node.cjs"),
        },
        "conversion_counts": {
            key: converter_receipt.get(key)
            for key in ("projectCount", "pcbCount", "footprintCount", "deviceCount", "entryCount")
        },
        "next_gate": "Import the EPro candidate, then capture it through the official API Bridge and prove strict parity.",
        "limits": "Converter success is a candidate only; it is not EasyEDA import parity or release approval.",
    }
    receipt_path = run.evidence / "easyeda-official-import-candidate.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(
        run, True,
        artifacts={"candidate": relative(output), "receipt": relative(receipt_path)},
        metrics=receipt["conversion_counts"],
    )
    print(json.dumps(receipt, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
