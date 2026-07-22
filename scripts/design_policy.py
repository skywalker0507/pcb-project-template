"""Compile and verify the SHA-bound executable PCB design policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build/design-policy-resolved.json"
INPUTS = {
    "requirements": ROOT / "config/requirements.json",
    "board_stack": ROOT / "config/board-stack.json",
    "manufacturing_profile": ROOT / "config/manufacturing-profile.json",
    "krt": ROOT / "config/kicad-routing-tools.json",
    "acceptance": ROOT / "config/pcb-acceptance.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def blocking_unknowns(requirements: dict[str, Any]) -> list[str]:
    values = requirements.get("unknowns", [])
    if not isinstance(values, list):
        return ["unknowns must be a list"]
    blockers: list[str] = []
    for value in values:
        if isinstance(value, str):
            blockers.append(value)
        elif isinstance(value, dict) and value.get("blocks") not in (None, [], False):
            blockers.append(str(value.get("item", "unnamed unknown")))
    return blockers


def compile_policy() -> dict[str, Any]:
    values = {name: read_json(path, name.replace("_", " ")) for name, path in INPUTS.items()}
    requirements = values["requirements"]
    stack = values["board_stack"]
    profile = values["manufacturing_profile"]
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
    requirements_ready = requirements.get("status") == "reviewed" and not blocking_unknowns(requirements)
    routing_rules = profile.get("routing_rules") if isinstance(profile.get("routing_rules"), dict) else {}
    reviewed_values = routing_rules.get("values") if isinstance(routing_rules, dict) else {}
    routing_ready = (
        requirements_ready
        and profile.get("selected") is True
        and routing_rules.get("reviewed") is True
        and isinstance(reviewed_values, dict)
        and {"track_width", "clearance", "via_diameter", "via_drill"}.issubset(reviewed_values)
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
    capability_issues = {
        "physical": [item for item in issues if item == "requirements_not_reviewed_or_have_blocking_unknowns"],
        "candidate_routing": issues,
        "release": issues,
    }
    return {
        "schema": 1,
        "kind": "design_policy_resolved",
        "inputs": {
            name: {"path": display_path(path), "sha256": sha256(path)}
            for name, path in INPUTS.items()
        },
        "requirements": {
            "status": requirements.get("status"),
            "blocking_unknowns": blocking_unknowns(requirements),
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
            "routing_values": reviewed_values if isinstance(reviewed_values, dict) else {},
            "evidence": routing_rules.get("evidence", []) if isinstance(routing_rules, dict) else [],
        },
        "capabilities": {
            "physical": requirements_ready,
            "candidate_routing": routing_ready,
            "release": routing_ready,
        },
        "issues": issues,
        "capability_issues": capability_issues,
    }


def policy_sha256(policy: dict[str, Any]) -> str:
    canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def write_policy(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    policy = compile_policy()
    policy["policy_sha256"] = policy_sha256(policy)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return policy


def require_policy(capability: str, path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    policy = read_json(path, "Resolved design policy")
    expected = compile_policy()
    expected_hash = policy_sha256(expected)
    if policy.get("policy_sha256") != expected_hash:
        raise SystemExit("Resolved design policy is stale; run python scripts/compile_design_policy.py")
    capabilities = policy.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get(capability) is not True:
        raise SystemExit(json.dumps({
            "success": False, "error": "design_policy_capability_unavailable", "capability": capability,
            "issues": (policy.get("capability_issues") or {}).get(capability, policy.get("issues", [])), "next": "Run python scripts/stage_doctor.py; do not invent missing requirements or manufacturing values.",
        }, separators=(",", ":")))
    return policy
