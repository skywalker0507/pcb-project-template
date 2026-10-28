"""Shared, cumulative requirement gates; unfinished evidence stays project-owned."""

from __future__ import annotations

from typing import Any


GATES = ("electrical_freeze", "physical_start", "release", "production_release")


def applicable_gates(data: dict[str, Any], gate: str) -> set[str]:
    if gate not in GATES:
        raise ValueError(f"Unknown requirements gate: {gate}")
    index = GATES.index(gate)
    scopes = set(GATES[:index + 1])
    if gate == "release" and data.get("release_target", "production") != "prototype":
        scopes.add("production_release")
    return scopes


def unknown_issues(data: dict[str, Any]) -> list[str]:
    values = data.get("unknowns")
    if not isinstance(values, list):
        return ["unknowns must be a list"]
    issues: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        if isinstance(item, str) and item.strip():
            continue  # Legacy unscoped obligations block every gate.
        if not isinstance(item, dict):
            issues.append(f"unknowns[{index}] must be an object or a nonempty legacy string")
            continue
        identity = item.get("id", item.get("item"))
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            issues.append(f"unknowns[{index}] needs a unique nonempty id or legacy item")
        else:
            seen.add(identity)
        scopes = item.get("blocks")
        if not isinstance(scopes, list) or any(not isinstance(scope, str) or scope not in GATES for scope in scopes):
            issues.append(f"unknowns[{index}].blocks must be an explicit list of supported gates")
    return issues


def blocking_unknowns(data: dict[str, Any], gate: str | None = None) -> list[str]:
    scopes = set(GATES) if gate is None else applicable_gates(data, gate)
    malformed = unknown_issues(data)
    if malformed:
        return malformed
    result: list[str] = []
    for item in data["unknowns"]:
        if isinstance(item, str):
            result.append(item)
        elif scopes.intersection(item["blocks"]):
            result.append(str(item.get("id", item.get("item"))))
    return result


def prototype_issues(data: dict[str, Any]) -> list[str]:
    target = data.get("release_target", "production")
    if target not in ("prototype", "production"):
        return ["release_target must be prototype or production (default)"]
    if target != "prototype":
        return []
    plan = data.get("prototype_validation")
    if not isinstance(plan, dict):
        return ["prototype release requires a prototype_validation plan"]
    issues: list[str] = []
    for key in ("purpose", "restrictions"):
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            issues.append(f"prototype_validation.{key} must be nonempty")
    tests = plan.get("tests")
    if not isinstance(tests, list):
        return [*issues, "prototype_validation.tests must be a list"]
    covered: set[str] = set()
    for item in tests:
        if (not isinstance(item, dict) or not isinstance(item.get("unknown_id"), str)
                or not item["unknown_id"].strip() or item["unknown_id"] in covered
                or not isinstance(item.get("procedure"), str) or not item["procedure"].strip()):
            issues.append("prototype validation tests need unique unknown_id and nonempty procedure")
        else:
            covered.add(item["unknown_id"])
    unknowns = data.get("unknowns")
    pending = {str(item.get("id", item.get("item"))) for item in unknowns or []
               if isinstance(item, dict) and isinstance(item.get("blocks"), list)
               and "production_release" in item["blocks"]} if isinstance(unknowns, list) else set()
    if covered != pending:
        issues.append("prototype validation tests must cover exactly the pending production_release unknown ids")
    return issues


def problems(data: dict[str, Any], require_reviewed: bool = False, *, gate: str = "electrical_freeze") -> list[str]:
    applicable_gates(data, gate)  # Invalid caller scope must never become an empty gate.
    issues: list[str] = []
    for key in ("interfaces", "rails", "connectors", "constraints", "unknowns", "critical_nets", "placement_regions"):
        if not isinstance(data.get(key), list):
            issues.append(f"{key} must be a list")
    if not isinstance(data.get("mechanical"), dict):
        issues.append("mechanical must be an object")
    meaningful = sum(len(data[key]) for key in ("interfaces", "rails", "connectors", "constraints", "unknowns") if isinstance(data.get(key), list))
    if meaningful == 0:
        issues.append("record at least one requirement or explicit unknown")
    if data.get("schema") != 2:
        issues.append("requirements schema must be 2")
    for key in ("critical_nets", "placement_regions"):
        if isinstance(data.get(key), list) and any(not isinstance(item, dict) for item in data[key]):
            issues.append(f"{key} entries must be objects")
    issues.extend(issue for issue in unknown_issues(data) if issue not in issues)
    issues.extend(prototype_issues(data))
    if require_reviewed:
        if data.get("status") != "reviewed":
            issues.append(f"requirements status must be reviewed before {gate}")
        if blocking_unknowns(data, gate):
            issues.append(f"resolve blocking unknowns for {gate}; later evidence remains registered")
    return issues
