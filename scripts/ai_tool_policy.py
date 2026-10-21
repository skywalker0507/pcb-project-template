"""Machine-readable AI tool boundaries for the current PCB stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stage_policy import ROOT, current_stage


DEFAULT_POLICY = ROOT / "config/ai-tool-policy.json"


def read_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"AI tool policy is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SystemExit("Unsupported AI tool policy")
    return value


def allowed_tools(stage: str | None = None, path: Path = DEFAULT_POLICY) -> list[str]:
    stage = stage or str(current_stage().get("stage"))
    values = (read_policy(path).get("stages") or {}).get(stage, [])
    return [str(value) for value in values]


def require_tool(tool: str, *, stage: str | None = None, path: Path = DEFAULT_POLICY, allow_optional: bool = False) -> dict[str, Any]:
    policy = read_policy(path)
    selected_stage = stage or str(current_stage().get("stage"))
    allowed = [str(value) for value in (policy.get("stages") or {}).get(selected_stage, [])]
    optional_stages = [str(value) for value in (policy.get("optional_tools") or {}).get(tool, [])]
    optional_allowed = allow_optional and selected_stage in optional_stages
    if tool not in allowed and not optional_allowed:
        raise SystemExit(json.dumps({
            "success": False,
            "error": "ai_tool_forbidden_for_stage",
            "tool": tool,
            "stage": selected_stage,
            "allowed_tools": allowed,
            "optional_tools_require_explicit_entrypoint": sorted(
                name for name, stages in (policy.get("optional_tools") or {}).items() if selected_stage in stages
            ),
            "next": "Run scripts/stage_doctor.py and use only the stage-scoped tool profile.",
        }, separators=(",", ":")))
    detail = policy.get(tool)
    return detail if isinstance(detail, dict) else {}
