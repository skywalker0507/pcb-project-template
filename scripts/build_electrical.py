#!/usr/bin/env python3
"""Run the selected electrical backend through the common candidate contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from electrical_backend import DEFAULT_CONFIG, ROOT, read_config, selected_backend, validate_backend, workspace_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", help="Configured backend name; defaults to config active")
    parser.add_argument("--build", default="default")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate backend wiring without invoking a build")
    args = parser.parse_args()
    if not args.build.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise SystemExit("Build name must contain only letters, numbers, dot, dash, or underscore")

    config = read_config(DEFAULT_CONFIG)
    name, backend = selected_backend(config, args.backend)
    status = validate_backend(config, name, backend)
    if args.check:
        print(json.dumps({"success": True, "checked": status}, separators=(",", ":")))
        return 0

    run = None
    if args.output is None and args.receipt is None:
        run = create_run("electrical", "electrical-build")
    default_output = (
        run.tmp / "generated" / name / f"{args.build}-{status['source_sha256'][:12].lower()}"
        if run else ROOT / "build/03-electrical/tmp/compat" / name / f"{args.build}-{status['source_sha256'][:12].lower()}"
    )
    output = (args.output or default_output).resolve()
    try:
        output.relative_to(ROOT / "build")
    except ValueError as exc:
        raise SystemExit("Electrical candidate output must stay under build/") from exc
    receipt = (args.receipt or (run.evidence / "electrical-build.json" if run else output / "electrical-build.json")).resolve()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    log = (run.tmp / "electrical-build.log" if run else output / "electrical-build.log").resolve()
    driver = workspace_path(str(backend["driver"]), "Backend driver")
    command = [
        sys.executable, str(driver), "--build", args.build,
        "--output", str(output), "--receipt", str(receipt),
    ]
    process = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if process.returncode:
        if run:
            finish_run(run, False, artifacts={"log": relative(log)}, metrics={"exit_code": process.returncode})
        print(json.dumps({"success": False, "backend": name, "exit_code": process.returncode, "log": relative(log)}, separators=(",", ":")))
        return process.returncode
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Backend completed without a valid receipt: {receipt}: {exc}") from exc
    if payload.get("success") is not True or payload.get("backend") != name:
        raise SystemExit("Backend receipt does not prove a successful build for the selected backend")
    payload["contract"] = status
    receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if run:
        finish_run(
            run, True,
            artifacts={"receipt": relative(receipt), "log": relative(log)},
            metrics={"source_sha256": status["source_sha256"], "backend": name},
        )
    print(json.dumps({
        "success": True,
        "backend": name,
        "receipt": relative(receipt),
        "candidate_board": payload.get("candidate_board"),
        "log": relative(log),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
