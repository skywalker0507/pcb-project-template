#!/usr/bin/env python3
"""Verify the project-to-template-engine version and source lock."""

from __future__ import annotations

import json
from pathlib import Path

from engine_runtime import ROOT, activate


def boundary_problems(result: dict[str, object]) -> list[str]:
    source_root_value = result.get("source_root")
    if not isinstance(source_root_value, str):
        return []
    source_root = Path(source_root_value)
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    profile = json.loads((ROOT / "config/manufacturing-profile.json").read_text(encoding="utf-8"))
    forbidden = {
        str((flow.get("project") or {}).get("name", "")).strip().lower(),
        str(profile.get("manufacturer", "")).strip().lower(),
    } - {""}
    problems: list[str] = []
    for path in [source_root / "pyproject.toml", *sorted((source_root / "src").rglob("*.py"))]:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for literal in forbidden:
            if literal in text:
                problems.append(f"project_literal_in_engine:{path.relative_to(source_root).as_posix()}:{literal}")
    return problems


def main() -> int:
    try:
        result = activate()
        problems = boundary_problems(result)
        result["boundary_pass"] = not problems
        result["boundary_problems"] = problems
        result["success"] = not problems
    except RuntimeError as exc:
        print(json.dumps({"schema": 1, "success": False, "error": str(exc)}, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
