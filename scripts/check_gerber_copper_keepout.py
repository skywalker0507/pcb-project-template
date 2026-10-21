#!/usr/bin/env python3
"""Compatibility adapter for the registry-driven Gerber production gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from engine_runtime import activate

activate()

from pcb_template_engine.manufacturing import boxes_intersect, gerber_bbox  # noqa: E402,F401


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    command = [
        sys.executable,
        str(ROOT / "scripts/check_manufacturing_registry.py"),
        "--phase",
        "gerber",
        *sys.argv[1:],
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
