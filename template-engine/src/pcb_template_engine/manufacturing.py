"""Compile and evaluate a generic, SHA-bound manufacturing rule registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable


def object_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def compile_registry(
    policy: dict[str, Any],
    *,
    engine_version: str,
    engine_api_version: int,
    parser: dict[str, Any],
) -> dict[str, Any]:
    manufacturing = policy.get("manufacturing")
    stack = policy.get("board_stack")
    if not isinstance(manufacturing, dict) or not isinstance(stack, dict):
        raise SystemExit("Resolved design policy lacks manufacturing or board_stack")
    copper_layers = stack.get("copper_layers")
    patterns = manufacturing.get("gerber_file_patterns")
    authored = manufacturing.get("gerber_rules")
    if not isinstance(copper_layers, list) or not copper_layers:
        raise SystemExit("Resolved design policy has no active copper layers")
    if not isinstance(patterns, dict):
        raise SystemExit("Resolved design policy has no Gerber file patterns")
    missing_patterns = [str(layer) for layer in copper_layers if layer not in patterns]
    if missing_patterns:
        raise SystemExit("Gerber file patterns are missing copper layers: " + ", ".join(missing_patterns))
    if not isinstance(authored, list):
        raise SystemExit("Resolved design policy Gerber rules must be a list")
    rules: list[dict[str, Any]] = [{
        "id": "required_copper_layer_files",
        "phase": "gerber",
        "kind": "gerber_file_presence",
        "severity": "error",
        "layers": [str(layer) for layer in copper_layers],
    }]
    seen = {"required_copper_layer_files"}
    for raw in authored:
        if not isinstance(raw, dict):
            raise SystemExit("Every authored Gerber rule must be an object")
        rule_id = raw.get("id")
        kind = raw.get("kind")
        if not isinstance(rule_id, str) or not rule_id or rule_id in seen:
            raise SystemExit(f"Gerber rule id is missing or duplicated: {rule_id!r}")
        if kind not in {"copper_keepout", "package_file_presence"}:
            raise SystemExit(f"Unsupported compiled Gerber rule kind: {kind!r}")
        if kind == "package_file_presence":
            pattern = raw.get("pattern")
            minimum = raw.get("minimum_count", 1)
            maximum = raw.get("maximum_count")
            if not isinstance(pattern, str) or not pattern or not isinstance(minimum, int) or minimum < 0:
                raise SystemExit(f"Gerber package rule {rule_id} has invalid pattern/minimum_count")
            if maximum is not None and (not isinstance(maximum, int) or maximum < minimum):
                raise SystemExit(f"Gerber package rule {rule_id} has invalid maximum_count")
            rules.append({
                "id": rule_id,
                "phase": "gerber",
                "kind": kind,
                "severity": raw.get("severity", "error"),
                "pattern": pattern,
                "minimum_count": minimum,
                "maximum_count": maximum,
            })
            seen.add(rule_id)
            continue
        scope = raw.get("layer_scope", "all_copper")
        if scope == "all_copper":
            layers = [str(layer) for layer in copper_layers]
        elif isinstance(scope, list) and scope and set(scope).issubset(set(copper_layers)):
            layers = [str(layer) for layer in scope]
        else:
            raise SystemExit(f"Invalid layer_scope for Gerber rule {rule_id}")
        geometry = raw.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("kind") != "board_bbox_mm":
            raise SystemExit(f"Gerber rule {rule_id} needs board_bbox_mm geometry")
        bbox = geometry.get("value")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise SystemExit(f"Gerber rule {rule_id} bbox must contain x0,y0,x1,y1")
        rules.append({
            "id": rule_id,
            "phase": "gerber",
            "kind": kind,
            "severity": raw.get("severity", "error"),
            "layers": layers,
            "coordinate_system": raw.get("coordinate_system", "kicad_board_mm_y_down"),
            "geometry": geometry,
            "policy": raw.get("policy"),
        })
        seen.add(rule_id)
    special = manufacturing.get("special_processes", {})
    if not isinstance(special, dict):
        raise SystemExit("Resolved design policy special_processes must be an object")
    for process_id, raw in sorted(special.items()):
        if not isinstance(raw, dict):
            raise SystemExit(f"Special manufacturing process must be an object: {process_id}")
        rules.append({
            "id": str(process_id),
            "phase": "pre_export",
            "kind": "special_process_confirmation",
            "severity": "error",
            "requirements": raw,
        })
    order_profile = manufacturing.get("order_profile")
    if not isinstance(order_profile, dict) or order_profile.get("status") != "reviewed":
        raise SystemExit("Resolved design policy needs a reviewed manufacturing order_profile")
    fabrication = order_profile.get("fabrication")
    assembly = order_profile.get("assembly")
    if not isinstance(fabrication, dict) or not isinstance(assembly, dict):
        raise SystemExit("Manufacturing order_profile needs fabrication and assembly objects")
    finish = fabrication.get("surface_finish")
    mask = fabrication.get("solder_mask_color")
    modules = assembly.get("required_dfm_modules")
    if not isinstance(finish, str) or not finish or not isinstance(mask, str) or not mask:
        raise SystemExit("Manufacturing order_profile needs surface_finish and solder_mask_color")
    if not isinstance(modules, list) or not modules or not all(isinstance(item, str) and item for item in modules):
        raise SystemExit("Manufacturing order_profile needs required_dfm_modules")
    rules.append({
        "id": "dfm_order_profile",
        "phase": "external_dfm",
        "kind": "dfm_order_profile_match",
        "severity": "error",
        "requirements": {
            "surface_finish": finish,
            "solder_mask_color": mask.lower(),
            "assembly_service": assembly.get("service"),
            "required_dfm_modules": sorted(set(modules)),
        },
        "evidence": order_profile.get("evidence", []),
    })
    registry: dict[str, Any] = {
        "schema": 1,
        "kind": "manufacturing_registry",
        "engine": {"package": "pcb_template_engine", "version": engine_version, "api_version": engine_api_version},
        "policy_sha256": policy.get("policy_sha256"),
        "manufacturer": manufacturing.get("manufacturer"),
        "board_stack": {"active": stack.get("active"), "copper_layers": [str(layer) for layer in copper_layers]},
        "gerber": {"parser": parser, "file_patterns": {str(key): value for key, value in patterns.items()}},
        "rules": rules,
    }
    registry["registry_sha256"] = object_sha256(registry)
    return registry


def validate_registry(registry: dict[str, Any], policy: dict[str, Any], *, engine_version: str, engine_api_version: int) -> None:
    if registry.get("schema") != 1 or registry.get("kind") != "manufacturing_registry":
        raise SystemExit("Manufacturing registry must be schema 1 and kind manufacturing_registry")
    expected_hash = registry.get("registry_sha256")
    stable = dict(registry)
    stable.pop("registry_sha256", None)
    if expected_hash != object_sha256(stable):
        raise SystemExit("Manufacturing registry content hash mismatch")
    if registry.get("policy_sha256") != policy.get("policy_sha256"):
        raise SystemExit("Manufacturing registry is stale for the resolved design policy")
    engine = registry.get("engine")
    if not isinstance(engine, dict) or engine.get("version") != engine_version or engine.get("api_version") != engine_api_version:
        raise SystemExit("Manufacturing registry was compiled by an incompatible template engine")


def evaluate_pre_export(registry: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for rule in registry.get("rules", []):
        if not isinstance(rule, dict) or rule.get("phase") != "pre_export":
            continue
        if rule.get("kind") != "special_process_confirmation":
            raise SystemExit(f"Unsupported pre-export manufacturing rule: {rule.get('kind')!r}")
        raw = rule.get("requirements")
        if not isinstance(raw, dict):
            raise SystemExit(f"Special-process rule lacks requirements: {rule.get('id')}")
        allowed = raw.get("allowed_confirmed_outcomes", [])
        selected = raw.get("selected_outcome")
        evidence = raw.get("evidence")
        passed = bool(
            raw.get("status") == "confirmed"
            and isinstance(allowed, list) and selected in allowed
            and isinstance(evidence, str) and evidence.strip()
        )
        results.append({
            "id": rule.get("id"), "kind": rule.get("kind"), "pass": passed,
            "status": raw.get("status"), "selected_outcome": selected,
            "evidence": evidence, "component": raw.get("component"),
        })
    success = all(item["pass"] for item in results)
    return {
        "schema": 1,
        "kind": "manufacturing_registry_pre_export_gate",
        "success": success,
        "manufacturing_registry_pass": success,
        "special_manufacturing_processes_confirmed": success,
        "registry_sha256": registry.get("registry_sha256"),
        "policy_sha256": registry.get("policy_sha256"),
        "process_count": len(results),
        "pending": [item["id"] for item in results if not item["pass"]],
        "rules": results,
    }


def evaluate_dfm_observation(registry: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    rules = [
        rule for rule in registry.get("rules", [])
        if isinstance(rule, dict) and rule.get("phase") == "external_dfm"
    ]
    results: list[dict[str, Any]] = []
    for rule in rules:
        if rule.get("kind") != "dfm_order_profile_match":
            raise SystemExit(f"Unsupported external DFM rule: {rule.get('kind')!r}")
        expected = rule.get("requirements")
        actual_parameters = observation.get("order_parameters")
        modules = observation.get("analysis_modules")
        if not isinstance(expected, dict) or not isinstance(actual_parameters, dict) or not isinstance(modules, dict):
            passed = False
            mismatches = ["missing_structured_dfm_scope"]
        else:
            mismatches: list[str] = []
            actual_finish = str(actual_parameters.get("surface_finish", "")).strip().lower()
            actual_mask = str(actual_parameters.get("solder_mask_color", "")).strip().lower()
            if actual_finish != str(expected.get("surface_finish", "")).strip().lower():
                mismatches.append("surface_finish")
            if actual_mask != str(expected.get("solder_mask_color", "")).strip().lower():
                mismatches.append("solder_mask_color")
            for module in expected.get("required_dfm_modules", []):
                result = modules.get(module)
                if not isinstance(result, dict) or result.get("executed") is not True or result.get("pass") is not True:
                    mismatches.append(f"dfm_module:{module}")
            passed = not mismatches
        results.append({
            "id": rule.get("id"),
            "kind": rule.get("kind"),
            "pass": passed,
            "expected": expected,
            "actual": {
                "order_parameters": actual_parameters,
                "analysis_modules": modules,
            },
            "mismatches": mismatches,
        })
    success = bool(results) and all(item["pass"] for item in results)
    return {
        "schema": 1,
        "kind": "manufacturing_registry_dfm_gate",
        "success": success,
        "dfm_pass": success,
        "manufacturing_registry_pass": success,
        "registry_sha256": registry.get("registry_sha256"),
        "policy_sha256": registry.get("policy_sha256"),
        "rules": results,
        "mismatches": sorted({item for result in results for item in result["mismatches"]}),
    }


def boxes_intersect(
    left: tuple[tuple[float, float], tuple[float, float]],
    right: tuple[tuple[float, float], tuple[float, float]],
    *,
    epsilon: float = 0.001,
) -> bool:
    (ax0, ay0), (ax1, ay1) = left
    (bx0, by0), (bx1, by1) = right
    return ax1 > bx0 + epsilon and bx1 > ax0 + epsilon and ay1 > by0 + epsilon and by1 > ay0 + epsilon


def gerber_bbox(board_bbox: list[object]) -> tuple[tuple[float, float], tuple[float, float]]:
    if len(board_bbox) != 4:
        raise SystemExit("board_bbox_mm must contain x0,y0,x1,y1")
    x0, y0, x1, y1 = (float(value) for value in board_bbox)
    if x1 <= x0 or y1 <= y0:
        raise SystemExit("board_bbox_mm must have positive area")
    return (x0, -y1), (x1, -y0)


def evaluate_gerbers(
    registry: dict[str, Any],
    gerber_dir: Path,
    *,
    open_gerber: Callable[[Path], Any],
    file_sha256: Callable[[Path], str],
) -> dict[str, Any]:
    gerber = registry.get("gerber")
    if not isinstance(gerber, dict) or not isinstance(gerber.get("file_patterns"), dict):
        raise SystemExit("Manufacturing registry lacks Gerber file patterns")
    patterns = gerber["file_patterns"]
    results: list[dict[str, Any]] = []
    layer_files: dict[str, Path] = {}
    for rule in registry.get("rules", []):
        if not isinstance(rule, dict) or rule.get("phase") != "gerber":
            continue
        rule_id = str(rule.get("id"))
        if rule.get("kind") == "package_file_presence":
            pattern = str(rule.get("pattern", ""))
            matches = sorted(gerber_dir.glob(pattern))
            minimum = int(rule.get("minimum_count", 1))
            maximum = rule.get("maximum_count")
            passed = len(matches) >= minimum and (maximum is None or len(matches) <= int(maximum))
            results.append({
                "id": rule_id,
                "kind": rule.get("kind"),
                "pass": passed,
                "pattern": pattern,
                "match_count": len(matches),
                "matches": [str(path) for path in matches[:10]],
                "minimum_count": minimum,
                "maximum_count": maximum,
            })
            continue
        layers = rule.get("layers")
        if not isinstance(layers, list) or not layers:
            raise SystemExit(f"Gerber rule {rule_id} has no layers")
        if rule.get("kind") == "gerber_file_presence":
            layer_results = []
            for layer in layers:
                pattern = patterns.get(layer)
                if not isinstance(pattern, str) or not pattern:
                    raise SystemExit(f"No Gerber file pattern for layer {layer}")
                matches = sorted(gerber_dir.glob(pattern))
                passed = len(matches) == 1
                if passed:
                    layer_files[str(layer)] = matches[0]
                layer_results.append({"layer": layer, "pattern": pattern, "match_count": len(matches), "pass": passed})
            results.append({"id": rule_id, "kind": rule.get("kind"), "pass": all(item["pass"] for item in layer_results), "layers": layer_results})
            continue
        if rule.get("kind") != "copper_keepout":
            raise SystemExit(f"Unsupported Gerber manufacturing rule: {rule.get('kind')!r}")
        geometry = rule.get("geometry")
        if not isinstance(geometry, dict):
            raise SystemExit(f"Gerber rule {rule_id} lacks geometry")
        region = gerber_bbox(list(geometry.get("value", [])))
        layer_results = []
        for layer in layers:
            path = layer_files.get(str(layer))
            if path is None:
                pattern = patterns.get(layer)
                matches = sorted(gerber_dir.glob(str(pattern))) if pattern else []
                if len(matches) != 1:
                    layer_results.append({"layer": layer, "match_count": len(matches), "intersection_count": None, "pass": False})
                    continue
                path = matches[0]
            parsed = open_gerber(path)
            intersections = []
            for index, obj in enumerate(parsed.objects):
                bbox = obj.bounding_box()
                if bbox is not None and boxes_intersect(bbox, region):
                    intersections.append({
                        "object_index": index,
                        "object_type": type(obj).__name__,
                        "bbox_mm": [[round(float(value), 6) for value in bbox[0]], [round(float(value), 6) for value in bbox[1]]],
                    })
            layer_results.append({
                "layer": layer, "gerber": str(path), "gerber_sha256": file_sha256(path),
                "object_count": len(parsed.objects), "intersection_count": len(intersections),
                "intersection_preview": intersections[:5], "pass": not intersections,
            })
        results.append({
            "id": rule_id, "kind": rule.get("kind"), "pass": all(item["pass"] for item in layer_results),
            "gerber_bbox_mm": [[region[0][0], region[0][1]], [region[1][0], region[1][1]]],
            "layers": layer_results,
        })
    success = all(item["pass"] for item in results)
    return {
        "success": success,
        "manufacturing_registry_pass": success,
        "gerber_rules_pass": success,
        "gerber_keepout_pass": all(item["pass"] for item in results if item["kind"] == "copper_keepout"),
        "registry_sha256": registry.get("registry_sha256"),
        "policy_sha256": registry.get("policy_sha256"),
        "rules": results,
    }
