#!/usr/bin/env python3
"""Compile the single SHA-bound policy consumed by guarded PCB operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from design_policy import DEFAULT_OUTPUT, DFM_ARTIFACT, KICAD_ARTIFACT, KRT_ARTIFACT, write_policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    policy = write_policy(args.output.resolve())
    print(json.dumps({"success": True, "output": str(args.output.resolve()), "policy_sha256": policy["policy_sha256"], "capabilities": policy["capabilities"], "issues": policy["issues"], "artifacts": {"krt": str(KRT_ARTIFACT), "dfm": str(DFM_ARTIFACT), "kicad": str(KICAD_ARTIFACT)}}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
