#!/usr/bin/env python3
"""Verify that PCB tools are reached through reviewed project wrappers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config/tool-entrypoints.json"


def audit(root: Path = ROOT, policy_path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    entrypoints = policy.get("entrypoints") or {}
    resolved: dict[str, str] = {}
    for name, value in entrypoints.items():
        path = (root / str(value)).resolve()
        resolved[str(name)] = path.relative_to(root.resolve()).as_posix()
        if not path.is_file():
            problems.append(f"missing_entrypoint:{name}:{value}")

    freerouting_value = str(entrypoints.get("freerouting", ""))
    freerouting_path = (root / freerouting_value).resolve()
    wrapper_text = freerouting_path.read_text(encoding="utf-8") if freerouting_path.is_file() else ""
    for flag in (policy.get("freerouting") or {}).get("required_flags", []):
        if str(flag) not in wrapper_text:
            problems.append(f"freerouting_wrapper_missing_required_flag:{flag}")

    if (policy.get("freerouting") or {}).get("forbid_direct_java_jar_outside_entrypoint", True):
        for script in sorted((root / "scripts").glob("*.py")):
            if script.resolve() == freerouting_path or script.name == "check_tool_entrypoints.py":
                continue
            text = script.read_text(encoding="utf-8", errors="replace").lower()
            if '"-jar"' in text and ("freerouting" in text or '"java"' in text):
                problems.append(f"direct_freerouting_invocation:{script.relative_to(root).as_posix()}")

    optional = policy.get("optional_capabilities") or {}
    if "freerouting" not in (optional.get("alternate_router_lab") or []):
        problems.append("freerouting_must_be_registered_as_lab_only_alternate_router")
    default_stage_tools = {
        tool for tools in (policy.get("stage_capabilities") or {}).values() for tool in tools
    }
    if "freerouting" in default_stage_tools:
        problems.append("freerouting_must_not_enter_default_stage_capabilities")
    return {
        "schema": 1,
        "success": not problems,
        "entrypoints": resolved,
        "freerouting": {
            "entrypoint": freerouting_value,
            "required_flags": (policy.get("freerouting") or {}).get("required_flags", []),
            "direct_invocations_outside_wrapper": [item for item in problems if item.startswith("direct_freerouting_invocation:")],
        },
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--verbose", action="store_true", help="Print the complete audited entrypoint map.")
    args = parser.parse_args()
    result = audit(args.project_root.resolve(), args.policy.resolve())
    output = result if args.verbose else {
        "success": result.get("success"),
        "entrypoint_count": len(result.get("entrypoints") or {}),
        "problem_count": len(result.get("problems") or []),
        "problems": result.get("problems") or [],
    }
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
