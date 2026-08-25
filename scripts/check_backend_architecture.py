#!/usr/bin/env python3
"""Validate separation and production defaults across PCB backend roles."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electrical_backend import read_config as read_electrical, selected_backend as selected_electrical, validate_backend as validate_electrical
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
    electrical_status = validate_electrical(electrical_config, electrical_name, electrical)
    physical_config = read_physical()
    physical_name, physical = selected_physical(physical_config)
    _, kicad = selected_physical(physical_config, "kicad")
    manufacturing = load("config/manufacturing-backend.json")
    flow = load("config/pcb-flow.json")
    engine = load("config/template-engine.json")
    acceptance = load("config/pcb-acceptance.json")
    problems: list[str] = []
    if flow.get("template_engine") != "config/template-engine.json":
        problems.append("project_flow_must_bind_the_template_engine_contract")
    if engine.get("package") != "pcb_template_engine" or engine.get("api_version") != 1:
        problems.append("template_engine_contract_is_not_supported")
    if electrical_name != "atopile" or electrical.get("status") != "primary":
        problems.append("production_electrical_backend_must_be_primary_atopile")
    if "circuit_json" in electrical_config.get("contract", {}).get("required_artifacts", []):
        problems.append("common_electrical_contract_contains_backend_private_circuit_json")
    if not electrical_status.get("entry"):
        problems.append("atopile_backend_entry_is_missing")
    if electrical_config.get("contract", {}).get("freeze_bundle_root") != "build/04-electrical-freeze/release":
        problems.append("electrical_freeze_bundle_must_use_release_lane")
    if physical_name != "kicad" or physical.get("role") != "primary":
        problems.append("primary_physical_backend_must_be_kicad")
    if kicad.get("role") != "primary":
        problems.append("kicad_must_be_the_primary_physical_backend")
    physical_contract = physical_config.get("contract") if isinstance(physical_config.get("contract"), dict) else {}
    if electrical_config.get("contract", {}).get("physical_manifest_schema") != 2:
        problems.append("physical_manifest_must_use_schema_2")
    if physical_contract.get("final_electrical_parity_checker") != "scripts/check_final_electrical_parity.py":
        problems.append("physical_backend_is_missing_final_electrical_parity")
    routing = architecture.get("routing_engine") if isinstance(architecture.get("routing_engine"), dict) else {}
    if routing.get("active") != "kicad_routing_tools" or routing.get("independent_of_gui_backend") is not True:
        problems.append("krt_must_be_the_gui_independent_primary_routing_engine")
    validation = architecture.get("validation_backend") if isinstance(architecture.get("validation_backend"), dict) else {}
    if "kicad_cli" not in set(validation.get("required", [])):
        problems.append("kicad_cli_validation_must_be_required")
    if manufacturing.get("active") != "jlcpcb":
        problems.append("manufacturing_backend_must_be_jlcpcb")
    jlcpcb = manufacturing.get("backends", {}).get("jlcpcb", {}) if isinstance(manufacturing.get("backends"), dict) else {}
    if "final_electrical_parity" not in jlcpcb.get("required_gates", []):
        problems.append("manufacturing_backend_is_missing_final_electrical_parity")
    gate_receipts = flow.get("manufacturing", {}).get("gate_receipts", {}) if isinstance(flow.get("manufacturing"), dict) else {}
    if "/release/" not in str(gate_receipts.get("electrical_freeze", "")).replace("\\", "/"):
        problems.append("manufacturing_flow_must_consume_a_release_lane_freeze")
    required_release = {
        "electrical_freeze_pass", "final_electrical_parity_pass", "jlc_dfm_pass", "dfm_order_profile_match",
        "manufacturing_registry_pass", "gerber_rules_pass",
        "critical_footprint_rule_areas_pass",
        "physical_integrity_pass",
        "special_manufacturing_processes_confirmed",
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
        "template_engine": engine.get("version"),
        "problems": problems,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
