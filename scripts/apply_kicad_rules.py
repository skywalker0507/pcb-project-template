#!/usr/bin/env python3
"""Explicitly apply reviewed resolved routing values to KiCad Default netclass."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from design_policy import DEFAULT_OUTPUT, require_policy
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, default=ROOT / "elec/layout/default/default.kicad_pcb")
    parser.add_argument("--policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=ROOT / "build/06-routing/evidence/manual/kicad-rules-apply.json")
    args = parser.parse_args()
    require_operation("apply_rules")
    policy = require_policy("candidate_routing", args.policy.resolve())
    project = args.board.resolve().with_suffix(".kicad_pro")
    try:
        data = json.loads(project.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"KiCad project is unavailable or invalid: {project}: {exc}") from exc
    routing = policy["routing"]["krt"]
    settings = data.setdefault("net_settings", {})
    classes = settings.setdefault("classes", [])
    if isinstance(classes, dict):
        default = classes.setdefault("Default", {"name": "Default"})
    elif isinstance(classes, list):
        default = next((item for item in classes if isinstance(item, dict) and item.get("name") == "Default"), None)
        if default is None:
            default = {"name": "Default"}
            classes.append(default)
    else:
        raise SystemExit("KiCad net_settings.classes must be an object or list")
    for source, target in (("clearance", "clearance"), ("track_width", "track_width"), ("via_size", "via_diameter"), ("via_drill", "via_drill")):
        value = routing.get(source)
        if value is None:
            raise SystemExit(f"Resolved policy lacks {source}; do not apply partial KiCad rules")
        default[target] = value
    project.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    receipt = {"success": True, "project": str(project), "project_sha256": sha256(project), "policy_sha256": policy["policy_sha256"], "default_netclass": {key: default[key] for key in ("clearance", "track_width", "via_diameter", "via_drill")}}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
