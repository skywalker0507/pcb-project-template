#!/usr/bin/env python3
"""Validate separation and production defaults across PCB backend roles."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electrical_backend import read_config as read_electrical, selected_backend as selected_electrical
from physical_backend import read_config as read_physical, selected_backend as selected_physical


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str) -> dict[str, object]:
    path = ROOT / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Backend architecture input is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Backend architecture input must be an object: {path}")
    return value


def audit() -> dict[str, object]:
    architecture = load("config/backend-architecture.json")
    electrical_config = read_electrical()
    electrical_name, electrical = selected_electrical(electrical_config)
    physical_config = read_physical()
    physical_name, physical = selected_physical(physical_config)
    _, kicad = selected_physical(physical_config, "kicad")
    manufacturing = load("config/manufacturing-backend.json")
    acceptance = load("config/pcb-acceptance.json")
    problems: list[str] = []
    if electrical_name != "atopile" or electrical.get("status") != "primary":
        problems.append("production_electrical_backend_must_be_primary_atopile")
    if "circuit_json" in electrical_config.get("contract", {}).get("required_artifacts", []):
        problems.append("common_electrical_contract_contains_backend_private_circuit_json")
    if physical_name != "easyeda_pro" or physical.get("role") != "primary":
        problems.append("primary_physical_backend_must_be_easyeda_pro")
    if kicad.get("role") != "reference_fallback":
        problems.append("kicad_must_remain_reference_fallback")
    routing = architecture.get("routing_engine") if isinstance(architecture.get("routing_engine"), dict) else {}
    if routing.get("active") != "kicad_routing_tools" or routing.get("independent_of_gui_backend") is not True:
        problems.append("krt_must_be_the_gui_independent_primary_routing_engine")
    validation = architecture.get("validation_backend") if isinstance(architecture.get("validation_backend"), dict) else {}
    if not {"easyeda_pro_drc", "kicad_cli"}.issubset(set(validation.get("required", []))):
        problems.append("easyeda_and_kicad_validation_must_both_be_required")
    if manufacturing.get("active") != "jlcpcb":
        problems.append("manufacturing_backend_must_be_jlcpcb")
    required_release = {
        "electrical_freeze_pass", "easyeda_import_parity_pass", "easyeda_drc_pass", "jlc_dfm_pass",
    }
    release = acceptance.get("release") if isinstance(acceptance.get("release"), dict) else {}
    if not required_release.issubset(set(release.get("requires", []))):
        problems.append("release_acceptance_is_missing_backend_gates")
    return {
        "schema": 1,
        "success": not problems,
        "electrical_backend": electrical_name,
        "physical_backend": physical_name,
        "routing_engine": routing.get("active"),
        "validation_backends": validation.get("required", []),
        "manufacturing_backend": manufacturing.get("active"),
        "problems": problems,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
