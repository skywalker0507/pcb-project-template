"""Compile a PCB project policy from explicit, project-owned JSON inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .requirements import GATES, blocking_unknowns, problems as requirements_problems


ROUTING_REQUIRED = {"track_width", "clearance", "via_size", "via_drill"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def object_sha256(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def canonical_routing_values(values: object) -> dict[str, float]:
    if not isinstance(values, dict):
        return {}
    result = dict(values)
    if "via_diameter" in result and "via_size" not in result:
        result["via_size"] = result.pop("via_diameter")
    return {str(name): float(value) for name, value in result.items()}


def semantic_input(name: str, value: dict[str, Any]) -> object:
    if name == "krt":
        return {
            "tool_commit": (value.get("tool") or {}).get("commit"),
            "board_stack": value.get("board_stack"),
            "acceptance_policy": value.get("acceptance_policy"),
            "acceptance": value.get("acceptance"),
            "manufacturing_profile": value.get("manufacturing_profile"),
            "guardrails": value.get("guardrails"),
            "profiles": value.get("profiles"),
        }
    return value


def display_path(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def compile_policy(project_root: Path, inputs: Mapping[str, Path]) -> dict[str, Any]:
    values = {name: read_json(path, name.replace("_", " ")) for name, path in inputs.items()}
    required = {"requirements", "board_stack", "manufacturing_profile", "manufacturing_rules", "krt", "acceptance"}
    missing = sorted(required - set(values))
    if missing:
        raise SystemExit("Design policy inputs are missing: " + ", ".join(missing))
    requirements = values["requirements"]
    stack = values["board_stack"]
    profile = values["manufacturing_profile"]
    manufacturing_rules = values["manufacturing_rules"]
    krt = values["krt"]
    active = stack.get("active")
    stack_profile = (stack.get("profiles") or {}).get(active)
    if not isinstance(stack_profile, dict):
        raise SystemExit(f"Board stack has no active profile: {active!r}")
    copper = stack_profile.get("copper_layers")
    forbidden = stack_profile.get("forbidden_signal_layers", [])
    if not isinstance(copper, list) or not isinstance(forbidden, list):
        raise SystemExit("Active board stack needs copper_layers and forbidden_signal_layers lists")
    signal_layers = [str(layer) for layer in copper if layer not in forbidden]
    outer = ((krt.get("profiles") or {}).get("outer_low_risk") or {}).get("routing", {})
    krt_layers = outer.get("layers") if isinstance(outer, dict) else None
    requirement_issues = {gate: requirements_problems(requirements, True, gate=gate) for gate in GATES}
    requirements_ready = not requirement_issues["physical_start"]
    routing_rules = profile.get("routing_rules") if isinstance(profile.get("routing_rules"), dict) else {}
    reviewed_values = canonical_routing_values(routing_rules.get("values") if isinstance(routing_rules, dict) else {})
    routing_ready = (
        requirements_ready
        and profile.get("selected") is True
        and routing_rules.get("reviewed") is True
        and ROUTING_REQUIRED.issubset(reviewed_values)
        and krt_layers == signal_layers
    )
    issues: list[str] = []
    if not requirements_ready:
        issues.append("requirements_not_reviewed_or_have_blocking_unknowns")
    if profile.get("selected") is not True:
        issues.append("manufacturing_profile_not_selected")
    if routing_rules.get("reviewed") is not True:
        issues.append("manufacturing_routing_values_not_reviewed")
    if krt_layers != signal_layers:
        issues.append("krt_signal_layers_do_not_match_active_board_stack")
    if manufacturing_rules.get("schema") != 2 or not isinstance(manufacturing_rules.get("gerber_rules"), list):
        issues.append("manufacturing_rules_invalid")
    release_issues = list(dict.fromkeys([*issues, *requirement_issues["release"]]))
    capability_issues = {
        "physical": requirement_issues["physical_start"],
        "candidate_routing": list(dict.fromkeys([*requirement_issues["physical_start"], *[item for item in issues if item != "manufacturing_rules_invalid"]])),
        "release": release_issues,
        "production_release": list(dict.fromkeys([*release_issues, *requirement_issues["production_release"]])),
    }
    return {
        "schema": 2,
        "kind": "design_policy_resolved",
        "inputs": {
            name: {
                "path": display_path(path, project_root),
                "sha256": object_sha256(semantic_input(name, values[name])),
                "source_sha256": sha256(path),
            }
            for name, path in inputs.items()
        },
        "requirements": {
            "status": requirements.get("status"),
            "blocking_unknowns": blocking_unknowns(requirements),
            "blocking_unknowns_by_gate": {gate: blocking_unknowns(requirements, gate) for gate in GATES},
            "release_target": requirements.get("release_target", "production"),
            "prototype_validation": requirements.get("prototype_validation"),
            "open_unknowns": requirements.get("unknowns", []),
            "critical_nets": requirements.get("critical_nets", []),
            "placement_regions": requirements.get("placement_regions", []),
        },
        "board_stack": {
            "active": active,
            "copper_layers": copper,
            "signal_layers": signal_layers,
            "forbidden_signal_layers": forbidden,
        },
        "manufacturing": {
            "selected": profile.get("selected") is True,
            "manufacturer": profile.get("manufacturer"),
            "source": profile.get("source"),
            "routing_values": reviewed_values,
            "evidence": routing_rules.get("evidence", []),
            "order_profile": profile.get("order_profile", {}),
            "gerber_file_patterns": manufacturing_rules.get("gerber_file_patterns", {}),
            "gerber_rules": manufacturing_rules.get("gerber_rules", []),
            "special_processes": profile.get("special_processes", {}),
            "cost_review": profile.get("cost_review", {}),
        },
        "routing": {
            "krt": {
                key: reviewed_values.get(key)
                for key in ("track_width", "clearance", "via_size", "via_drill", "hole_to_hole_clearance", "board_edge_clearance")
            },
            "kicad_default_netclass": {"clearance": reviewed_values.get("clearance")},
        },
        "capabilities": {
            "physical": requirements_ready,
            "candidate_routing": routing_ready,
            "release": routing_ready and not release_issues,
            "production_release": routing_ready and not capability_issues["production_release"],
        },
        "issues": release_issues,
        "capability_issues": capability_issues,
    }


def policy_sha256(policy: dict[str, Any]) -> str:
    stable = json.loads(json.dumps(policy))
    for value in (stable.get("inputs") or {}).values():
        if isinstance(value, dict):
            value.pop("source_sha256", None)
    canonical = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()
