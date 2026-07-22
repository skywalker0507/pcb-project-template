"""Machine-enforced stage boundaries for operations that alter design state."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGE_PATH = ROOT / "handoff/stages/CURRENT_STAGE.json"
ALLOWED = {
    "route": {"routing"},
    "candidate_route": {"physical", "routing"},
    "record_review": {"physical", "routing", "release"},
    "export": {"release"},
}


def current_stage(path: Path = STAGE_PATH) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Current stage is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("stage"), str):
        raise SystemExit("Current stage must contain a stage string")
    return value


def require_operation(operation: str, path: Path = STAGE_PATH) -> dict[str, object]:
    stage = current_stage(path)
    allowed = ALLOWED.get(operation, set())
    if stage["stage"] not in allowed:
        raise SystemExit(json.dumps({
            "success": False, "error": "stage_operation_forbidden", "operation": operation,
            "stage": stage["stage"], "allowed_stages": sorted(allowed),
            "next": "Run python scripts/stage_doctor.py; do not bypass the stage guard.",
        }, separators=(",", ":")))
    return stage
