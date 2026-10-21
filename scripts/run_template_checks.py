#!/usr/bin/env python3
"""Run template regression checks with full logs on disk and tiny stdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build/00-shared/template-checks/current.json"


def run_check(name: str, command: list[str], log_dir: Path) -> dict[str, object]:
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{name}.log"
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    lines = (completed.stdout + completed.stderr).splitlines()
    return {
        "name": name,
        "success": completed.returncode == 0,
        "exit_code": completed.returncode,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "log": log.relative_to(ROOT).as_posix(),
        "log_bytes": log.stat().st_size,
        "tail": lines[-1][:500] if lines else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-tests", action="store_true", help="Run static template checks only.")
    args = parser.parse_args()
    output = args.output.resolve()
    log_dir = output.parent / "logs"
    commands: list[tuple[str, list[str]]] = []
    if not args.skip_tests:
        commands.append(("unit-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]))
    commands.extend([
        ("template-engine", [sys.executable, "scripts/check_template_engine.py"]),
        ("backend-architecture", [sys.executable, "scripts/check_backend_architecture.py"]),
        ("tool-entrypoints", [sys.executable, "scripts/check_tool_entrypoints.py"]),
    ])
    started = time.perf_counter()
    checks = [run_check(name, command, log_dir) for name, command in commands]
    failed = [item for item in checks if item["success"] is not True]
    receipt = {
        "schema": 1,
        "kind": "template_regression_checks",
        "success": not failed,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "checks": checks,
        "failed_checks": [item["name"] for item in failed],
        "limits": "Template regression evidence only; this never substitutes for PCB checkpoint or release gates.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": receipt["success"],
        "check_count": len(checks),
        "failed_checks": receipt["failed_checks"],
        "duration_ms": receipt["duration_ms"],
        "output": output.relative_to(ROOT).as_posix(),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
