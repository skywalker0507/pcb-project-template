#!/usr/bin/env python3
"""Validate cross-file revision, part, GPIO, and impedance consistency."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "requirements": "config/requirements.json",
    "decisions": "config/product-decisions.json",
    "design": "config/design-data.json",
    "selected": "config/selected-parts.json",
    "assembly": "config/jlcpcb-parts.json",
}


def load_json(root: Path, relative: str) -> dict[str, object]:
    path = root / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative} must contain a JSON object")
    return value


def part_map(data: dict[str, object]) -> tuple[dict[str, str], list[str]]:
    mapping: dict[str, str] = {}
    issues: list[str] = []
    parts = data.get("parts", [])
    if not isinstance(parts, list):
        return mapping, ["parts must be a list"]
    for item in parts:
        if not isinstance(item, dict):
            continue
        mpn = item.get("mpn")
        refs = item.get("refs")
        if not isinstance(mpn, str) or not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, str):
                continue
            if ref in mapping:
                issues.append(f"duplicate part reference {ref}")
            mapping[ref] = mpn
    return mapping, issues


def impedance_problems(root: Path, controlled: object) -> list[str]:
    """Validate declared channels against their evidence, without project geometry."""
    if not isinstance(controlled, dict):
        return ["controlled_impedance must be an object (empty for no controlled channels)"]
    issues = []
    def positive(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    for name, channel in controlled.items():
        prefix = f"controlled impedance {name}"
        if not isinstance(channel, dict):
            issues.append(f"{prefix}: declaration must be an object")
            continue
        evidence_value = channel.get("evidence")
        if not isinstance(evidence_value, str) or not evidence_value:
            issues.append(f"{prefix}: evidence path is missing")
            continue
        try:
            (root / evidence_value).resolve().relative_to(root.resolve())
            evidence = load_json(root, evidence_value)
            stack = evidence.get("stackup", {})
            section = evidence.get(channel.get("evidence_section", f"{name}_differential"), {})
            if not isinstance(stack, dict) or not isinstance(section, dict):
                raise ValueError("stackup and channel evidence must be objects")
            if not channel.get("stack_template") or stack.get("template") != channel["stack_template"]:
                issues.append(f"{prefix}: stack template does not match the declaration")
            if not positive(channel.get("target_ohm")) or section.get("target_ohm") != channel.get("target_ohm"):
                issues.append(f"{prefix}: target impedance does not match the declaration")
            physical = stack.get("physical_layers", [])
            if (evidence.get("schema") != 2 or not isinstance(physical, list) or not physical
                    or any(not isinstance(item, dict) or not item.get("name") or not positive(item.get("thickness_mm")) for item in physical)):
                issues.append(f"{prefix}: complete manufacturer physical stackup is missing")
            else:
                copper_count = sum(item.get("kind") == "copper" for item in physical)
                if not copper_count or stack.get("layers") != copper_count:
                    issues.append(f"{prefix}: copper layer count does not match the physical stackup")
                expected_layers = channel.get("physical_layers")
                if expected_layers is not None and [item["name"] for item in physical] != expected_layers:
                    issues.append(f"{prefix}: physical layer sequence does not match the declaration")
            geometry = section.get("pcb_geometry_mm")
            if not isinstance(geometry, dict) or not geometry or any(not positive(value) for value in geometry.values()):
                issues.append(f"{prefix}: positive reviewed PCB geometry is missing")
                geometry = {}
            expected_geometry = channel.get("geometry_mm", {})
            if not isinstance(expected_geometry, dict) or any(geometry.get(key) != value for key, value in expected_geometry.items()):
                issues.append(f"{prefix}: geometry does not match the project declaration")
            verification = section.get("rounded_geometry_verification_response", {})
            if not isinstance(verification, dict) or verification.get("dResultValid") != 1 or not positive(verification.get("dImpedance")):
                issues.append(f"{prefix}: calculator verification is not valid")
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            issues.append(f"{prefix}: cannot load/validate evidence: {exc}")
    return issues


def consistency_problems(root: Path = ROOT) -> list[str]:
    issues: list[str] = []
    try:
        records = {name: load_json(root, path) for name, path in FILES.items()}
        spec_text = (root / "HARDWARE_SPEC.md").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"cannot load consistency inputs: {exc}"]

    revisions = {name: value.get("revision") for name, value in records.items()}
    spec_match = re.search(r"^# .*?—\s*(Rev\s+[^\s]+)", spec_text, re.MULTILINE)
    revisions["hardware_spec"] = spec_match.group(1) if spec_match else None
    expected = revisions["requirements"]
    if not isinstance(expected, str) or not expected.strip():
        issues.append("requirements revision is missing")
    for name, revision in revisions.items():
        if revision != expected:
            issues.append(f"revision mismatch: {name}={revision!r}, expected={expected!r}")
    flow_path = root / "config/pcb-flow.json"
    if flow_path.is_file():
        try:
            project = load_json(root, "config/pcb-flow.json").get("project", {}).get("name")
            for name, record in records.items():
                if record.get("project") is not None and record["project"] != project:
                    issues.append(f"project identity mismatch: {name}={record['project']!r}, expected={project!r}")
        except (OSError, ValueError, AttributeError) as exc:
            issues.append(f"cannot validate project identity: {exc}")

    requirements_controller = records["requirements"].get("controller")
    design_controller = records["design"].get("controller")
    required_reserved = set(requirements_controller.get("reserved_gpios", [])) if isinstance(requirements_controller, dict) else set()
    design_reserved = set(design_controller.get("reserved", [])) if isinstance(design_controller, dict) else set()
    if required_reserved != design_reserved:
        issues.append(
            "reserved GPIO mismatch: requirements="
            f"{sorted(required_reserved)}, design={sorted(design_reserved)}"
        )

    selected, selected_issues = part_map(records["selected"])
    assembly, assembly_issues = part_map(records["assembly"])
    issues.extend(f"selected-parts: {item}" for item in selected_issues)
    issues.extend(f"jlcpcb-parts: {item}" for item in assembly_issues)
    for ref in sorted(set(selected) & set(assembly)):
        if selected[ref] != assembly[ref]:
            issues.append(
                f"MPN mismatch for {ref}: selected={selected[ref]!r}, assembly={assembly[ref]!r}"
            )

    exempt = records["requirements"].get("assembly_exempt_refs", [])
    exempt_refs = set(exempt) if isinstance(exempt, list) else set()
    mentioned_refs = set(
        re.findall(r"\b(?:C|D|F|J|L|LED|Q|R|SW|U)\d+\b", spec_text)
    )
    missing_assembly = sorted(mentioned_refs - exempt_refs - set(assembly))
    if missing_assembly:
        issues.append(f"spec references missing from assembly mapping: {missing_assembly}")

    issues.extend(impedance_problems(root, records["requirements"].get("controlled_impedance")))

    selected_status = records["selected"].get("status")
    if selected_status not in {"electrical_review", "reviewed", "release_review"}:
        issues.append("selected-parts status is not a reviewed state")
    assembly_status = records["assembly"].get("status")
    board_sha = records["assembly"].get("board_sha256")
    if assembly_status == "electrical_review" and board_sha is not None:
        issues.append("electrical-review assembly mapping must not retain a pre-freeze board SHA")
    elif assembly_status == "release_review":
        if not isinstance(board_sha, str) or not re.fullmatch(r"[0-9A-Fa-f]{64}", board_sha):
            issues.append("release-review assembly mapping requires a 64-character board SHA-256")
    elif assembly_status != "electrical_review":
        issues.append("jlcpcb-parts status is not electrical_review or release_review")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="Write the deterministic JSON receipt to this path.")
    args = parser.parse_args()
    root = args.project_root.resolve()
    issues = consistency_problems(root)
    input_paths = [root / "HARDWARE_SPEC.md", *(root / relative for relative in FILES.values())]
    try:
        controlled = load_json(root, FILES["requirements"]).get("controlled_impedance", {})
        for channel in controlled.values() if isinstance(controlled, dict) else []:
            if isinstance(channel, dict) and isinstance(channel.get("evidence"), str):
                path = (root / channel["evidence"]).resolve()
                path.relative_to(root.resolve())
                if path.is_file() and path not in input_paths:
                    input_paths.append(path)
    except (OSError, ValueError):
        pass  # The validation issues above already describe malformed inputs.
    payload = {
        "schema": 1,
        "kind": "spec_consistency_validation",
        "success": not issues,
        "issues": issues[:8],
        "inputs": [
            {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper(),
            }
            for path in input_paths if path.is_file()
        ],
    }
    if args.output:
        args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.output.resolve().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
