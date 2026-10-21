#!/usr/bin/env python3
"""Compatibility adapter for registry pre-export manufacturing rules."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from engine_runtime import activate

activate()

from pcb_template_engine.manufacturing import evaluate_pre_export  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def validate_special_processes(profile: dict[str, Any]) -> dict[str, Any]:
    """Legacy library API retained for callers while production consumes the registry."""
    configured = profile.get("special_processes", {})
    if not isinstance(configured, dict):
        raise SystemExit("manufacturing-profile special_processes must be an object")
    registry = {
        "rules": [
            {
                "id": str(process_id),
                "phase": "pre_export",
                "kind": "special_process_confirmation",
                "requirements": raw,
            }
            for process_id, raw in sorted(configured.items())
        ]
    }
    receipt = evaluate_pre_export(registry)
    return {
        "schema": 1,
        "kind": "special_manufacturing_process_gate",
        "success": receipt["success"],
        "special_manufacturing_processes_confirmed": receipt["special_manufacturing_processes_confirmed"],
        "process_count": receipt["process_count"],
        "pending": receipt["pending"],
        "processes": receipt["rules"],
    }


def main() -> int:
    command = [
        sys.executable,
        str(ROOT / "scripts/check_manufacturing_registry.py"),
        "--phase",
        "pre_export",
        *sys.argv[1:],
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
