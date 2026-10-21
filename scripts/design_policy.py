"""Project adapter for the template engine's SHA-bound design policy compiler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine_runtime import activate

activate()

from pcb_template_engine.policy import (  # noqa: E402
    blocking_unknowns,
    canonical_routing_values,
    compile_policy as engine_compile_policy,
    object_sha256,
    policy_sha256,
    read_json,
    semantic_input,
    sha256,
)
from pcb_template_engine.manufacturing import compile_registry  # noqa: E402
from pcb_template_engine import API_VERSION, __version__  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build/00-shared/evidence/design-policy/design-policy-resolved.json"
KRT_ARTIFACT = ROOT / "build/00-shared/evidence/design-policy/design-policy-krt.json"
DFM_ARTIFACT = ROOT / "build/00-shared/evidence/design-policy/design-policy-dfm.json"
KICAD_ARTIFACT = ROOT / "build/00-shared/evidence/design-policy/design-policy-kicad-rules.json"
MANUFACTURING_REGISTRY_ARTIFACT = ROOT / "build/00-shared/evidence/design-policy/manufacturing-registry.json"
INPUTS = {
    "requirements": ROOT / "config/requirements.json",
    "board_stack": ROOT / "config/board-stack.json",
    "manufacturing_profile": ROOT / "config/manufacturing-profile.json",
    "manufacturing_rules": ROOT / "config/manufacturing-rules.json",
    "krt": ROOT / "config/kicad-routing-tools.json",
    "acceptance": ROOT / "config/pcb-acceptance.json",
}


def compile_policy() -> dict[str, Any]:
    return engine_compile_policy(ROOT, INPUTS)


def parser_contract() -> dict[str, Any]:
    toolchain = read_json(ROOT / "config/toolchain-policy.json", "toolchain policy")
    parser = toolchain.get("gerber_parser")
    if not isinstance(parser, dict):
        raise SystemExit("Toolchain policy has no gerber_parser contract")
    return {
        "package": parser.get("package"),
        "version": parser.get("version"),
        "requirements": parser.get("requirements"),
    }


def write_policy(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    policy = compile_policy()
    policy["policy_sha256"] = policy_sha256(policy)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts = {
        KRT_ARTIFACT: {"schema": 1, "policy_sha256": policy["policy_sha256"], "routing": policy["routing"]["krt"]},
        DFM_ARTIFACT: {"schema": 2, "policy_sha256": policy["policy_sha256"], "rules": policy["routing"]["krt"]},
        KICAD_ARTIFACT: {"schema": 1, "policy_sha256": policy["policy_sha256"], "default_netclass": policy["routing"]["kicad_default_netclass"]},
        MANUFACTURING_REGISTRY_ARTIFACT: compile_registry(
            policy,
            engine_version=__version__,
            engine_api_version=API_VERSION,
            parser=parser_contract(),
        ),
    }
    for artifact, value in artifacts.items():
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return policy


def require_policy(capability: str, path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    policy = read_json(path, "Resolved design policy")
    expected = compile_policy()
    expected_hash = policy_sha256(expected)
    if policy.get("policy_sha256") != expected_hash:
        raise SystemExit("Resolved design policy is stale; run py -3.14 scripts/compile_design_policy.py")
    capabilities = policy.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get(capability) is not True:
        raise SystemExit(json.dumps({
            "success": False,
            "error": "design_policy_capability_unavailable",
            "capability": capability,
            "issues": (policy.get("capability_issues") or {}).get(capability, policy.get("issues", [])),
            "next": "Run py -3.14 scripts/stage_doctor.py; do not invent missing requirements or manufacturing values.",
        }, separators=(",", ":")))
    return policy
