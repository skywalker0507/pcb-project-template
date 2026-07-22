#!/usr/bin/env python3
"""Emit a compact, read-first session receipt without opening the whole PCB."""

from __future__ import annotations

import json
from pathlib import Path

from stage_policy import ROOT, current_stage
from validate_requirements import problems


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    stage = current_stage()
    checklist = load(ROOT / "config/stage-checklist.json")
    acceptance = load(ROOT / "config/pcb-acceptance.json")
    stack = load(ROOT / "config/board-stack.json")
    requirements = load(ROOT / "config/requirements.json")
    stage_rules = checklist.get("stages", {}).get(stage.get("stage"), {})
    result = {
        "schema": 1,
        "stage": stage.get("stage"),
        "status": stage.get("status"),
        "objective": stage.get("objective"),
        "open_items": stage.get("open_items", [])[:8],
        "next_actions": stage.get("next_actions", [])[:8],
        "forbidden": stage_rules.get("forbidden", []),
        "required_evidence": stage_rules.get("required_evidence", []),
        "board_stack": stack.get("active"),
        "release_requires": (acceptance.get("release") or {}).get("requires", []),
        "requirements_issues": problems(requirements)[:8],
        "read_only_default": True,
        "instruction": "Do not read a full .kicad_pcb by default; use compact receipts or query_pcb_context.py slices.",
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
