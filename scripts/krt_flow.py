#!/usr/bin/env python3
"""Token-efficient, policy-driven adapter for KiCadRoutingTools.

The configured KRT checkout is treated as immutable. Full router output is written to
disk; stdout receives one compact JSON receipt suitable for an AI tool call.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_tool_policy import require_tool
from artifact_store import create_run, finish_run, relative
from pcb_policy import load_acceptance, reviewed_routing_rules
from design_policy import require_policy
from stage_policy import require_operation


DEFAULT_CONFIG = Path("config/kicad-routing-tools.json")
PROTECTED_EXTRA_FLAGS = {
    "--layers", "--layer-costs", "--track-width", "--impedance",
    "--clearance", "--via-size", "--via-drill", "--fab-tier",
    "--fab-overrides", "--hole-to-hole-clearance",
    "--board-edge-clearance", "--enable-used-layers", "--overwrite",
    "--no-fix-drc-settings", "--no-clamp-netclasses", "--net-clearances",
    "--max-iterations", "--max-probe-iterations", "--keep-input-copper",
}


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bounded(values: list[Any], limit: int) -> dict[str, Any]:
    limit = max(0, limit)
    return {
        "count": len(values),
        "preview": values[:limit],
        "truncated": len(values) > limit,
    }


def routing_task_advice(
    config: dict[str, Any], root: Path, board: Path, nets: list[str], config_path: Path,
) -> dict[str, Any] | None:
    planner_value = config.get("routing_planner")
    if not planner_value:
        return None
    result = subprocess.run(
        [
            str(Path(config["kicad_cli"]).with_name("python.exe")),
            str(root / "scripts/classify_routing_task.py"),
            "--board", str(board), "--nets", *nets,
            "--policy", str(resolve(root, planner_value)),
            "--krt-config", str(config_path.resolve()),
        ],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    rows = [line for line in result.stdout.splitlines() if line.lstrip().startswith("{")]
    if result.returncode != 0 or not rows:
        raise SystemExit(compact({
            "success": False, "error": "routing_task_classifier_failed",
            "diagnostic_tail": (result.stdout + result.stderr)[-1000:],
        }))
    return json.loads(rows[-1])


def protected_extra_conflicts(values: list[str]) -> list[str]:
    normalized = {
        value.split("=", 1)[0].lower() for value in values if value.startswith("--")
    }
    return sorted(normalized & PROTECTED_EXTRA_FLAGS)


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_config(path: Path) -> tuple[dict[str, Any], Path]:
    path = path.resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema") != 1:
        raise SystemExit(f"Unsupported config schema: {config.get('schema')!r}")
    return config, path.parent.parent


def resolve(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def apply_exploration_policy(
    config: dict[str, Any], profile: dict[str, Any], enabled: bool,
) -> dict[str, str]:
    if not enabled:
        return {}
    exploration = config.get("exploration", {})
    if not isinstance(exploration, dict):
        raise SystemExit("KRT exploration policy must be an object")
    routing_overrides = exploration.get("routing_overrides", {})
    environment = exploration.get("environment", {})
    if not isinstance(routing_overrides, dict) or not isinstance(environment, dict):
        raise SystemExit("KRT exploration routing_overrides/environment must be objects")
    profile["routing"].update(routing_overrides)
    resolved_environment = {str(key): str(value) for key, value in environment.items()}
    if int(profile["routing"].get("max_ripup", -1)) != 0:
        raise SystemExit("Exploration policy must set max_ripup to zero")
    return resolved_environment


def require_critical_probe_mode(args: argparse.Namespace, profile: dict[str, Any], root: Path) -> dict[str, object]:
    """Authorize only a bounded, isolated pre-promotion routing probe."""
    if not args.explore:
        raise SystemExit("A physical critical probe requires --explore")
    if int(profile.get("routing", {}).get("max_ripup", -1)) != 0:
        raise SystemExit("A physical critical probe requires max_ripup=0")
    policy_path = root / "config" / "placement-routability.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    checkpoint = policy.get("checkpoint") or {}
    groups = [str(value) for value in checkpoint.get("critical_probe_groups", [])]
    if args.route_area not in groups:
        raise SystemExit(compact({
            "success": False,
            "error": "critical_probe_route_area_not_reviewed",
            "route_area": args.route_area,
            "reviewed_route_areas": groups,
        }))
    component_map = checkpoint.get("critical_probe_components") or {}
    expected = [str(value) for value in component_map.get(args.route_area, [])]
    if expected and args.probe_component not in expected:
        raise SystemExit(compact({
            "success": False,
            "error": "critical_probe_component_not_reviewed",
            "route_area": args.route_area,
            "probe_component": args.probe_component,
            "reviewed_components": expected,
        }))
    return require_operation("candidate_route")


def copy_project_companions(source_project: Path, output_project: Path) -> list[Path]:
    """Copy deterministic KiCad rule/library context beside an isolated candidate."""
    copied: list[Path] = []
    source_dru = source_project.with_suffix(".kicad_dru")
    output_dru = output_project.with_suffix(".kicad_dru")
    if source_dru.is_file():
        shutil.copy2(source_dru, output_dru)
        copied.append(output_dru)
    source_fp_table = source_project.parent / "fp-lib-table"
    output_fp_table = output_project.parent / "fp-lib-table"
    if source_fp_table.is_file():
        # Candidate directories live at a different depth. Resolve KIPRJMOD to
        # the reviewed source-project directory so the same project-local
        # footprint libraries remain available to kicad-cli DRC.
        table = source_fp_table.read_text(encoding="utf-8")
        table = table.replace("${KIPRJMOD}", source_project.parent.as_posix())
        output_fp_table.write_text(table, encoding="utf-8")
        copied.append(output_fp_table)
    return copied


def require_active_stack(config: dict[str, Any], root: Path, profile: dict[str, Any]) -> None:
    stack_value = config.get("board_stack")
    if not stack_value:
        return
    stack = json.loads(resolve(root, stack_value).read_text(encoding="utf-8"))
    active = stack.get("active")
    selected = stack.get("profiles", {}).get(active)
    if not isinstance(selected, dict):
        raise SystemExit(f"Invalid active board stack profile: {active!r}")
    copper_layers = selected.get("copper_layers")
    configured_layers = profile.get("routing", {}).get("layers")
    expected_forbidden = selected.get("forbidden_signal_layers", [])
    configured_forbidden = config.get("guardrails", {}).get("forbidden_signal_layers", [])
    # KRT routes signals only.  On the four-layer profile the inner copper
    # layers are intentional planes and must therefore be absent from its
    # routing-layer list, while still existing in the KiCad stackup.
    expected_layers = [layer for layer in copper_layers if layer not in expected_forbidden] if isinstance(copper_layers, list) else None
    layer_contract_ok = configured_layers == expected_layers
    if profile.get("engine") == "differential" or profile.get("allow_layer_subset") is True:
        layer_contract_ok = bool(
            isinstance(configured_layers, list)
            and configured_layers
            and all(layer in expected_layers for layer in configured_layers)
        )
    if not layer_contract_ok or configured_forbidden != expected_forbidden:
        raise SystemExit(compact({
            "success": False,
            "error": "board_stack_krt_mismatch",
            "active_stack": active,
            "expected_layers": expected_layers,
            "configured_layers": configured_layers,
            "next": "Run scripts/init_project.py --layers 2 or --layers 4, then review routing policy.",
        }))


def require_impedance_evidence(root: Path, profile: dict[str, Any]) -> None:
    if not profile.get("impedance_evidence"):
        return
    evidence_value = profile.get("impedance_evidence")
    if not evidence_value:
        raise SystemExit("Differential profile requires impedance_evidence")
    evidence = json.loads(resolve(root, evidence_value).read_text(encoding="utf-8"))
    stackup = evidence.get("stackup", {})
    usb = evidence.get("usb2_differential", {})
    geometry = usb.get("pcb_geometry_mm", {})
    routing = profile.get("routing", {})
    expected = {"track_width": geometry.get("base_width")}
    if "diff_pair_gap" in routing:
        expected["diff_pair_gap"] = geometry.get("pair_gap")
    physical = stackup.get("physical_layers") if isinstance(stackup, dict) else None
    expected_physical_order = [
        "F.Cu", "dielectric 1.1", "dielectric 1.2", "In1.Cu", "dielectric 2",
        "In2.Cu", "dielectric 3.1", "dielectric 3.2", "B.Cu",
    ]
    if (
        evidence.get("schema") != 2
        or stackup.get("template") != "JLC04161H-3313A"
        or not stackup.get("template_access_id")
        or not stackup.get("laminated_construction_code")
        or not isinstance(physical, list)
        or [item.get("name") for item in physical] != expected_physical_order
    ):
        raise SystemExit(compact({
            "success": False,
            "error": "differential_stackup_evidence_incomplete",
            "evidence": evidence_value,
            "required_template": "JLC04161H-3313A",
        }))
    request = usb.get("rounded_geometry_verification_request_mil", {})
    response = usb.get("rounded_geometry_verification_response", {})
    if (
        usb.get("calculator_model") != "DiffEdgeCoupledCoatedMicrostrip1B"
        or abs(float(request.get("H1", 0)) - 8.126) > 1e-9
        or abs(float(response.get("dImpedance", 0)) - 90.0) > 0.5
        or response.get("dResultValid") != 1
    ):
        raise SystemExit(compact({
            "success": False,
            "error": "differential_calculator_evidence_invalid",
            "evidence": evidence_value,
        }))
    mismatches = {
        key: {"profile": routing.get(key), "evidence": value}
        for key, value in expected.items()
        if value is None or routing.get(key) is None
        or abs(float(routing[key]) - float(value)) > 1e-12
    }
    engine = profile.get("engine", "single")
    geometry_role = profile.get("geometry_role")
    if engine == "differential" and (
        usb.get("signal_layer") != "L1" or routing.get("layers") != ["F.Cu"]
    ):
        mismatches["signal_layer"] = {
            "profile": routing.get("layers"), "evidence": usb.get("signal_layer")
        }
    if geometry_role == "usb_connector_esd_fanout" and routing.get("layers") != ["F.Cu", "B.Cu"]:
        mismatches["fanout_layers"] = {
            "profile": routing.get("layers"), "evidence": ["F.Cu", "B.Cu"]
        }
    if engine != "differential" and geometry_role != "usb_connector_esd_fanout":
        mismatches["geometry_role"] = {
            "profile": geometry_role,
            "evidence": "usb_connector_esd_fanout",
        }
    if mismatches:
        raise SystemExit(compact({
            "success": False, "error": "differential_evidence_mismatch",
            "evidence": evidence_value, "mismatches": mismatches,
        }))


def require_board_stack_matches(
    board: Path, config: dict[str, Any], root: Path, *, physical_stackup: bool = False,
) -> None:
    stack = json.loads(resolve(root, config["board_stack"]).read_text(encoding="utf-8"))
    active = stack.get("active")
    selected = stack.get("profiles", {}).get(active, {})
    expected = selected.get("copper_layers", []) if isinstance(selected, dict) else []
    plane_layers = set((selected.get("plane_nets") or {}).keys()) if isinstance(selected, dict) else set()
    text = board.read_text(encoding="utf-8", errors="replace")
    declared = dict(re.findall(r'\(\d+\s+"([^"]+)"\s+(signal|power)', text))
    mismatches = []
    for layer in expected:
        expected_type = "power" if layer in plane_layers else "signal"
        if declared.get(layer) != expected_type:
            mismatches.append({"layer": layer, "expected_type": expected_type, "actual_type": declared.get(layer)})
    if mismatches:
        raise SystemExit(compact({
            "success": False, "error": "board_stack_board_mismatch", "active_stack": active,
            "layer_mismatches": mismatches,
            "next": "Do not rewrite an existing board automatically. Create a matching new board or change its layer stack in KiCad, then rerun this check.",
        }))
    if physical_stackup:
        evidence = json.loads((root / "config" / "jlcpcb-impedance-evidence.json").read_text(encoding="utf-8"))
        stackup_start = text.find("\t\t(stackup")
        setup_end = text.find("\n\t\t(pad_to_mask_clearance", stackup_start)
        stackup_text = text[stackup_start:setup_end] if stackup_start >= 0 and setup_end > stackup_start else ""
        required = [
            '(layer "F.Cu"', '(thickness 0.035)', '(layer "In1.Cu"',
            '(layer "In2.Cu"', '(thickness 0.0152)', '(layer "B.Cu"',
            '(thickness 0.107 locked)', '(thickness 0.0994 locked)',
            '(thickness 1.065 locked)', '(epsilon_r 4.38)', 'addsublayer',
            '(dielectric_constraints yes)',
        ]
        missing = [token for token in required if token not in stackup_text]
        if evidence.get("stackup", {}).get("template") != "JLC04161H-3313A" or missing:
            raise SystemExit(compact({
                "success": False,
                "error": "board_manufacturer_stackup_mismatch",
                "board": str(board),
                "template": evidence.get("stackup", {}).get("template"),
                "missing_tokens": missing,
                "next": "Return to the physical stage and apply the SHA-guarded reviewed manufacturer stackup before differential routing.",
            }))


def net_names(board: Path) -> list[str]:
    text = board.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r'\(net\s+(?:\d+\s+)?"([^"]+)"\)', text))
    return sorted(name for name in names if name)


def expand_nets(board: Path, includes: list[str], excludes: list[str]) -> list[str]:
    available = net_names(board)
    selected = {
        name
        for pattern in includes
        for name in available
        if fnmatch.fnmatchcase(name, pattern)
    }
    selected = {
        name for name in selected
        if not any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes)
    }
    return sorted(selected)


def suggested_low_risk_nets(board: Path, excludes: list[str], limit: int = 8) -> list[str]:
    """Suggest actual board nets only; never silently select them for routing."""
    names = net_names(board)
    candidates = [
        name for name in names
        if not any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes)
    ]
    return candidates[:limit]


def project_default_clearance(board: Path) -> float | None:
    project = board.with_suffix(".kicad_pro")
    if not project.is_file():
        return None
    data = json.loads(project.read_text(encoding="utf-8"))
    classes = data.get("net_settings", {}).get("classes", [])
    if isinstance(classes, dict):
        default = classes.get("Default")
    else:
        default = next(
            (item for item in classes if item.get("name") == "Default"), None
        )
    if not default or default.get("clearance") is None:
        return None
    return float(default["clearance"])


def sexpr_blocks(text: str, head: str):
    needle = f"({head}"
    offset = 0
    while True:
        start = text.find(needle, offset)
        if start < 0:
            return
        depth = 0
        quoted = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
            elif quoted and char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        yield text[start:index + 1]
                        offset = index + 1
                        break
        else:
            return


def board_metrics(board: Path, forbidden_layers: list[str]) -> dict[str, Any]:
    text = board.read_text(encoding="utf-8", errors="replace")
    counts: dict[str, int] = {}
    widths: set[float] = set()
    forbidden: dict[str, int] = {}
    for block in sexpr_blocks(text, "segment"):
        layer_match = re.search(r'\(layer\s+(?:"([^"]+)"|([^\s)]+))\)', block)
        if not layer_match:
            continue
        layer = layer_match.group(1) or layer_match.group(2)
        counts[layer] = counts.get(layer, 0) + 1
        width_match = re.search(r"\(width\s+([0-9.]+)\)", block)
        if width_match:
            widths.add(float(width_match.group(1)))
    for layer in forbidden_layers:
        if counts.get(layer):
            forbidden[layer] = counts[layer]
    vias = sum(1 for _ in sexpr_blocks(text, "via"))
    return {
        "segments": counts,
        "vias": vias,
        "widths_mm": sorted(widths),
        "forbidden_segments": forbidden,
    }


def parse_router_summaries(output: str) -> dict[str, Any] | None:
    parsed: list[dict[str, Any]] = []
    for line in output.splitlines():
        if "JSON_SUMMARY:" not in line:
            continue
        try:
            parsed.append(json.loads(line.split("JSON_SUMMARY:", 1)[1].strip()))
        except json.JSONDecodeError:
            continue
    if not parsed:
        return None
    # Reconciliation prints a smaller later summary. Start with the broadest
    # scope, then apply explicit later successes/failures to its single-net set.
    base = max(
        parsed,
        key=lambda item: (
            int(item.get("multipoint_pads_total", 0)),
            len(item.get("routed_single", [])) + len(item.get("failed_single", [])),
        ),
    )
    merged = dict(base)
    routed = set(base.get("routed_single", []))
    failed = set(base.get("failed_single", []))
    base_index = parsed.index(base)
    for item in parsed[base_index + 1:]:
        recovered = set(item.get("routed_single", []))
        still_failed = set(item.get("failed_single", []))
        routed.update(recovered)
        failed.difference_update(recovered)
        failed.update(still_failed)
        routed.difference_update(still_failed)
    merged["routed_single"] = sorted(routed)
    merged["failed_single"] = sorted(failed)
    merged["summary_record_count"] = len(parsed)
    return merged


def concise_router(summary: dict[str, Any] | None, max_items: int) -> dict[str, Any]:
    if not summary:
        return {"summary_found": False}
    failed_multi = summary.get("failed_multipoint", [])
    return {
        "summary_found": True,
        "routed_single": len(summary.get("routed_single", [])),
        "routed_diff_pairs": bounded(summary.get("routed_diff_pairs", []), max_items),
        "failed_diff_pairs": bounded(summary.get("failed_diff_pairs", []), max_items),
        "partial_diff_pairs": bounded(summary.get("partial_diff_pairs", []), max_items),
        "single_ended_diff_pairs": bounded(summary.get("single_ended_diff_pairs", []), max_items),
        "failed_single": bounded(summary.get("failed_single", []), max_items),
        "multipoint_pads_connected": summary.get("multipoint_pads_connected"),
        "multipoint_pads_total": summary.get("multipoint_pads_total"),
        "failed_multipoint_count": sum(
            len(item.get("failed_pads", [])) for item in failed_multi
        ),
        "vias_reported": summary.get("total_vias"),
        "time_s": summary.get("total_time"),
        "summary_record_count": summary.get("summary_record_count", 1),
    }


def concise_metrics(metrics: dict[str, Any] | None, max_items: int) -> dict[str, Any] | None:
    if not metrics:
        return None
    segments = sorted(metrics.get("segments", {}).items())
    widths = metrics.get("widths_mm", [])
    return {
        "segment_layers": bounded(segments, max_items),
        "vias": metrics.get("vias"),
        "widths_mm": bounded(widths, max_items),
        "forbidden_segments": metrics.get("forbidden_segments", {}),
    }


def copper_obstacle_fingerprint(
    board: Path, bounds_mm: tuple[float, float, float, float] | None = None,
) -> str:
    """Hash existing copper/keepout geometry, optionally inside a reviewed route box."""
    text = board.read_text(encoding="utf-8", errors="replace")
    geometry: list[str] = []
    for head in ("segment", "arc", "via", "zone"):
        for block in sexpr_blocks(text, head):
            if bounds_mm is not None:
                points = [
                    (float(x), float(y))
                    for x, y in re.findall(r'\((?:start|mid|end|at|xy)\s+(-?[0-9.]+)\s+(-?[0-9.]+)', block)
                ]
                if points:
                    block_bounds = (
                        min(point[0] for point in points), min(point[1] for point in points),
                        max(point[0] for point in points), max(point[1] for point in points),
                    )
                    x0, y0, x1, y1 = bounds_mm
                    if block_bounds[2] < x0 or block_bounds[0] > x1 or block_bounds[3] < y0 or block_bounds[1] > y1:
                        continue
            geometry.append(re.sub(r"\s+", " ", block).strip())
    payload = compact({"bounds_mm": bounds_mm, "geometry": sorted(geometry)})
    return hashlib.sha256(payload.encode()).hexdigest()[:16].upper()


def failure_family_signature(
    profile: str, placement_sha256: str, nets: list[str], layers: list[str], obstacle_sha256: str = "",
) -> str:
    payload = compact({
        "profile": profile,
        "placement_sha256": placement_sha256,
        "nets": sorted(nets),
        "layers": layers,
        "obstacle_sha256": obstacle_sha256,
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def consecutive_family_failures(history: list[dict[str, Any]], family: str) -> int:
    failures = 0
    for item in reversed(history):
        if item.get("failure_family") != family:
            continue
        if item.get("success") is True:
            break
        if item.get("structural_failure") is True:
            failures += 1
    return failures


def consecutive_area_rejections(history: list[dict[str, Any]], route_area: str) -> int:
    """Count rejected attempts since the latest accepted candidate in one stable work area."""
    rejected = 0
    for item in reversed(history):
        if item.get("route_area") != route_area:
            continue
        if item.get("success") is True:
            break
        rejected += 1
    return rejected


def route_area_health(history: list[dict[str, Any]], route_area: str, limit: int) -> dict[str, Any]:
    rows = [item for item in history if item.get("route_area") == route_area]
    rejected = consecutive_area_rejections(history, route_area)
    outcome_counts: dict[str, int] = {}
    for item in rows:
        outcome = str(item.get("outcome_class", "unknown"))
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    stalled = rejected >= limit
    return {
        "route_area": route_area,
        "attempts": len(rows),
        "accepted": sum(item.get("success") is True for item in rows),
        "consecutive_rejections": rejected,
        "rejection_limit": limit,
        "stalled": stalled,
        "outcomes": dict(sorted(outcome_counts.items())),
        "next": (
            "diagnose_route_plan_tool_guardrail_or_corridor; placement_change_requires_separate_escalation"
            if stalled else "continue_current_work_packet"
        ),
    }


def route_area_signature(placement_sha256: str, nets: list[str], explicit: str | None = None) -> str:
    if explicit:
        return explicit
    payload = compact({"placement_sha256": placement_sha256, "nets": sorted(nets)})
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def strategy_signature(profile: str, routing: dict[str, Any], strategy_class: str = "") -> str:
    return hashlib.sha256(compact({
        "strategy_class": strategy_class,
        "layers": routing.get("layers"),
        "extra_args": routing.get("extra_args", []),
        "ripup_enabled": int(routing.get("max_ripup", 0)) > 0,
    }).encode()).hexdigest()[:16]


def classify_route_outcome(
    *, accepted: bool, router_exit_code: int, output_exists: bool, route_ok: bool,
    router_complete: bool, connectivity_complete: bool, audit_pass: bool | None,
) -> tuple[str, bool]:
    if accepted:
        return "accepted", False
    if router_exit_code != 0 or not output_exists:
        return "tool_failure", False
    if not route_ok:
        return "guardrail_or_candidate_integrity_failure", False
    if not router_complete or not connectivity_complete:
        return "structural_no_path_or_incomplete", True
    if audit_pass is False:
        return "candidate_drc_regression", False
    return "audit_or_policy_failure", False


def route_area_escalation(
    *, accepted: bool, structural_failure: bool,
    prior_area_structural: list[dict[str, Any]], strategy: str,
) -> dict[str, object]:
    """Retain failure history without carrying escalation past a passing strategy."""
    attempts = len(prior_area_structural) + (1 if structural_failure else 0)
    strategies = {
        str(item.get("strategy_signature"))
        for item in prior_area_structural if item.get("strategy_signature")
    }
    if structural_failure:
        strategies.add(strategy)
    if accepted:
        return {
            "routing_review_required": False,
            "corridor_review_required": False,
            "placement_change_authorized": False,
            "structural_attempts_in_route_area": attempts,
            "distinct_strategies_in_route_area": len(strategies),
            "resolved_by_passing_strategy": bool(attempts),
            "next": None,
        }
    return {
        "routing_review_required": attempts >= 2,
        "corridor_review_required": attempts >= 2 and len(strategies) >= 2,
        "placement_change_authorized": False,
        "structural_attempts_in_route_area": attempts,
        "distinct_strategies_in_route_area": len(strategies),
        "resolved_by_passing_strategy": False,
        "next": (
            "record_escalation_then_review_corridor; move_footprints_only_with_separate_routability_and_semantic_evidence"
            if attempts >= 2 and len(strategies) >= 2
            else "try_one_meaningfully_distinct_routing_strategy"
            if structural_failure
            else "fix_the_recorded_non_structural_failure_without_changing_placement"
        ),
    }


def candidate_diff(before: dict[str, Any], after: dict[str, Any] | None, audit: dict[str, Any] | None) -> dict[str, Any] | None:
    """Triage-only geometry/DRC delta; it is intentionally not acceptance."""
    if after is None:
        return None
    layers = sorted(set(before.get("segments", {})) | set(after.get("segments", {})))
    segment_delta = {
        layer: after.get("segments", {}).get(layer, 0) - before.get("segments", {}).get(layer, 0)
        for layer in layers
        if after.get("segments", {}).get(layer, 0) != before.get("segments", {}).get(layer, 0)
    }
    before_widths = set(before.get("widths_mm", []))
    return {
        "segment_delta_by_layer": segment_delta,
        "via_delta": after.get("vias", 0) - before.get("vias", 0),
        "new_widths_mm": sorted(set(after.get("widths_mm", [])) - before_widths),
        "forbidden_segments_after": after.get("forbidden_segments", {}),
        "new_drc_violation_count": (audit or {}).get("new_violation_count"),
        "unconnected_delta": (audit or {}).get("unconnected_delta"),
        "limits": ["Triage only: this summary does not prove routing, placement, return-path, or release acceptance."],
    }


def concise_candidate_diff(value: dict[str, Any] | None, max_items: int) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "segment_delta_by_layer": bounded(sorted(value.get("segment_delta_by_layer", {}).items()), max_items),
        "via_delta": value.get("via_delta"),
        "new_widths_mm": bounded(value.get("new_widths_mm", []), max_items),
        "forbidden_segments_after": value.get("forbidden_segments_after", {}),
        "new_drc_violation_count": value.get("new_drc_violation_count"),
        "unconnected_delta": value.get("unconnected_delta"),
        "limits": value.get("limits", []),
    }


def concise_routing_geometry(value: dict[str, Any] | None, max_items: int) -> dict[str, Any] | None:
    if not value:
        return None
    if value.get("success") is not True:
        return {
            "success": False,
            "error": value.get("error"),
            "exit_code": value.get("exit_code"),
        }
    return {
        "success": True,
        "nets": bounded(value.get("nets", []), max_items),
        "change": value.get("change"),
        "quality_warnings": bounded(value.get("quality_warnings", []), max_items),
        "limits": value.get("limits"),
    }


def routing_quality_warnings(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not value:
        return []
    if value.get("success") is not True:
        return [{
            "type": "routing_geometry_unavailable",
            "severity": "soft",
            "message": "Routing quality metrics were unavailable; deterministic DRC/connectivity acceptance is unchanged, but visual review must not be skipped.",
        }]
    return list(value.get("quality_warnings", []))


def concise_audit(audit: dict[str, Any] | None, max_items: int) -> dict[str, Any] | None:
    if not audit:
        return None
    categories = sorted((audit.get("drc_categories") or {}).items())
    return {
        "exit_code": audit.get("exit_code"),
        "baseline_pass": audit.get("baseline_pass"),
        "unconnected": audit.get("unconnected"),
        "in1_non_ground_segments": audit.get("in1_non_ground_segments"),
        "new_violation_count": audit.get("new_violation_count"),
        "drc_cache_hits": audit.get("drc_cache_hits"),
        "phase_ms": audit.get("phase_ms"),
        "drc_categories": bounded(categories, max_items),
        "report": audit.get("report"),
    }


def route_stdout_index(receipt: dict[str, Any]) -> dict[str, Any]:
    """Keep default route output small; the report command exposes bounded detail."""
    router = receipt.get("router") if isinstance(receipt.get("router"), dict) else {}
    failed_single = router.get("failed_single") if isinstance(router.get("failed_single"), dict) else {}
    audit = receipt.get("audit") if isinstance(receipt.get("audit"), dict) else {}
    guardrails = receipt.get("guardrails") if isinstance(receipt.get("guardrails"), dict) else {}
    warnings = receipt.get("quality_warnings")
    warning_count = warnings.get("count") if isinstance(warnings, dict) else len(warnings or [])
    return {
        "success": receipt.get("success"),
        "route_ok": receipt.get("route_ok"),
        "outcome_class": receipt.get("outcome_class"),
        "structural_failure": receipt.get("structural_failure"),
        "route_area": receipt.get("route_area"),
        "strategy_class": receipt.get("strategy_class"),
        "attempt_number": receipt.get("attempt_number"),
        "family_attempt_number": receipt.get("family_attempt_number"),
        "run_id": receipt.get("run_id"),
        "router": {
            "routed_single": router.get("routed_single"),
            "failed_single_count": failed_single.get("count"),
            "failed_multipoint_count": router.get("failed_multipoint_count"),
            "multipoint_pads_connected": router.get("multipoint_pads_connected"),
            "multipoint_pads_total": router.get("multipoint_pads_total"),
            "vias_reported": router.get("vias_reported"),
            "time_s": router.get("time_s"),
        },
        "audit": {
            "baseline_pass": audit.get("baseline_pass"),
            "unconnected": audit.get("unconnected"),
            "unconnected_delta": audit.get("unconnected_delta"),
            "new_violation_count": audit.get("new_violation_count"),
        } if audit else None,
        "guardrails_pass": all(value is not False for value in (
            guardrails.get("input_preserved"),
            guardrails.get("project_rules_preserved"),
            guardrails.get("copper_scope_preserved"),
        )),
        "quality_warning_count": warning_count,
        "phase_ms": receipt.get("phase_ms"),
        "output": receipt.get("output"),
        "report": receipt.get("report"),
    }


def tool_state(config: dict[str, Any], root: Path) -> dict[str, Any]:
    tool = config["tool"]
    repository = resolve(root, tool["repository"])
    python = resolve(root, tool["python"])
    router = repository / tool.get("router_script", "route.py")
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    actual_commit = commit.stdout.strip() if commit.returncode == 0 else None
    expected_commit = tool.get("commit")
    import_check = subprocess.run(
        [
            str(python), "-c",
            "import numpy, scipy, shapely; "
            f"import sys; sys.path.insert(0, {str(repository)!r}); "
            "import rust_router.grid_router",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    ) if python.is_file() else None
    help_check = subprocess.run(
        [str(python), str(router), "--help"],
        cwd=repository, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    ) if python.is_file() and router.is_file() else None
    required_flags = {
        "--layers", "--layer-costs", "--no-fix-drc-settings", "--fab-overrides",
    }
    help_text = help_check.stdout if help_check and help_check.returncode == 0 else ""
    missing_flags = sorted(flag for flag in required_flags if flag not in help_text)
    if "--no-clamp-netclasses" in help_text:
        netclass_mode = "legacy_no_clamp"
    elif "--net-clearances" in help_text:
        netclass_mode = "native_netclasses"
    else:
        netclass_mode = "unsupported"
        missing_flags.append("netclass-preservation-capability")
    return {
        "ok": bool(
            repository.is_dir()
            and python.is_file()
            and router.is_file()
            and expected_commit
            and actual_commit
            and actual_commit == expected_commit
            and import_check is not None and import_check.returncode == 0
            and help_check is not None and help_check.returncode == 0
            and not missing_flags
        ),
        "repository": str(repository),
        "python": str(python),
        "router": str(router),
        "expected_commit": expected_commit,
        "actual_commit": actual_commit,
        "python_import_ok": bool(import_check and import_check.returncode == 0),
        "missing_cli_flags": missing_flags,
        "netclass_mode": netclass_mode,
    }


def build_command(
    config: dict[str, Any], root: Path, profile: dict[str, Any],
    source: Path, output: Path, nets: list[str],
) -> list[str]:
    state = tool_state(config, root)
    if not state["ok"]:
        raise SystemExit(compact({"success": False, "error": "tool_check_failed", **state}))
    router = state["router"]
    if profile.get("engine") == "differential":
        router = str(Path(router).with_name("route_diff.py"))
        if not Path(router).is_file():
            raise SystemExit("Configured KRT checkout has no route_diff.py")
    command = [state["python"], router, str(source), str(output), *nets]
    routing = profile["routing"]
    required_numeric = ("track_width", "clearance", "via_size", "via_drill")
    missing = [key for key in required_numeric if routing.get(key) is None]
    if missing:
        raise SystemExit(
            "Explicit project routing values are required: " + ", ".join(missing)
        )
    layers = routing["layers"]
    costs = routing["layer_costs"]
    if len(layers) != len(costs):
        raise SystemExit("routing.layers and routing.layer_costs must have equal length")
    command += ["--layers", *map(str, layers), "--layer-costs", *map(str, costs)]
    options = {
        "track_width": "--track-width",
        "via_size": "--via-size",
        "via_drill": "--via-drill",
        "grid_step": "--grid-step",
        "via_cost": "--via-cost",
        "max_iterations": "--max-iterations",
        "max_probe_iterations": "--max-probe-iterations",
        "max_ripup": "--max-ripup",
        "hole_to_hole_clearance": "--hole-to-hole-clearance",
        "board_edge_clearance": "--board-edge-clearance",
        "max_iterations": "--max-iterations",
    }
    for key, flag in options.items():
        value = routing.get(key)
        if value is not None:
            command += [flag, str(value)]
    if profile.get("engine") == "differential":
        command += ["--diff-pair-gap", str(routing["diff_pair_gap"])]
        if routing.get("diff_pair_intra_match"):
            command += ["--diff-pair-intra-match"]
        if routing.get("no_gnd_vias"):
            command += ["--no-gnd-vias"]
    if state["netclass_mode"] == "legacy_no_clamp":
        command += ["--clearance", str(routing["clearance"])]
    else:
        board_clearance = project_default_clearance(source)
        configured_clearance = float(routing["clearance"])
        if board_clearance is None:
            raise SystemExit(
                "Latest KRT requires a same-stem .kicad_pro with a Default clearance"
            )
        if abs(board_clearance - configured_clearance) > 1e-9:
            raise SystemExit(
                "Configured clearance must equal the KiCad Default netclass when "
                "using latest KRT: "
                f"config={configured_clearance}, project={board_clearance}"
            )
    fab_overrides = routing.get("fab_overrides")
    if fab_overrides:
        command += ["--fab-overrides", str(resolve(root, fab_overrides))]
    command += ["--no-fix-drc-settings", "--keep-input-copper"]
    if state["netclass_mode"] == "legacy_no_clamp":
        command += ["--no-clamp-netclasses"]
    extra_args = [str(value) for value in routing.get("extra_args", [])]
    conflicts = protected_extra_conflicts(extra_args)
    if conflicts:
        raise SystemExit(
            "extra_args may not override protected policy flags: " + ", ".join(conflicts)
        )
    command += extra_args
    return command


def run_audit(
    config: dict[str, Any], root: Path, source: Path, output: Path, log_path: Path,
) -> dict[str, Any] | None:
    audit = config.get("audit", {})
    command_template = audit.get("command")
    if not audit.get("enabled") or not command_template:
        return None
    substitutions = {
        "input": str(source), "output": str(output), "root": str(root),
        "work_dir": str(log_path.parent / "drc"),
    }
    command = [str(item).format(**substitutions) for item in command_template]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n===== POST-ROUTE AUDIT =====\n")
        handle.write(result.stdout)
        handle.write(result.stderr)
    parsed = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and (
            "board_sha256" in candidate or "baseline_pass" in candidate
        ):
            parsed = candidate
            break
    return {
        "exit_code": result.returncode,
        "baseline_pass": parsed.get("baseline_pass") if parsed else None,
        "unconnected": (
            (parsed or {}).get("unconnected_after")
            if (parsed or {}).get("unconnected_after") is not None
            else (parsed or {}).get("drc_categories", {}).get("unconnected_items")
        ),
        "in1_non_ground_segments": (parsed or {}).get("in1_non_ground_segments"),
        "drc_categories": (parsed or {}).get("drc_categories"),
        "new_violation_count": (parsed or {}).get("new_violation_count"),
        "new_violation_types": (parsed or {}).get("new_violation_types", {}),
        "new_violation_signature": (parsed or {}).get("new_violation_signature"),
        "unconnected_delta": (
            (parsed or {}).get("unconnected_after", 0) - (parsed or {}).get("unconnected_before", 0)
            if (parsed or {}).get("unconnected_after") is not None and (parsed or {}).get("unconnected_before") is not None
            else None
        ),
        "drc_cache_hits": (parsed or {}).get("drc_cache_hits"),
        "phase_ms": (parsed or {}).get("phase_ms"),
        "report": (parsed or {}).get("report"),
    }


def normalize_candidate(
    config: dict[str, Any], root: Path, output: Path, nets: list[str],
    routing: dict[str, Any],
) -> dict[str, Any]:
    """Apply reviewed copper dimensions and refill zones before candidate DRC."""
    required = ("track_width", "via_size", "via_drill")
    missing = [name for name in required if routing.get(name) is None]
    if missing:
        raise SystemExit("Candidate normalization lacks reviewed values: " + ", ".join(missing))
    kicad_python = Path(config["kicad_cli"]).with_name("python.exe")
    command = [
        str(kicad_python), str(root / "scripts" / "normalize_krt_candidate.py"),
        "--board", str(output), "--expected-sha256", sha256(output),
        "--nets", *nets,
        "--track-width", str(routing["track_width"]),
        "--via-size", str(routing["via_size"]),
        "--via-drill", str(routing["via_drill"]),
    ]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0:
        raise SystemExit(compact({
            "success": False,
            "error": "candidate_normalization_failed",
            "exit_code": result.returncode,
            "stderr": result.stderr[-1000:],
        }))
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise SystemExit("Candidate normalization emitted no receipt")
    return json.loads(lines[-1])


def check_candidate_copper_scope(
    root: Path, source: Path, output: Path, nets: list[str], receipt_path: Path,
) -> dict[str, Any]:
    command = [
        sys.executable, str(root / "scripts" / "check_krt_candidate_copper_scope.py"),
        "--input", str(source), "--candidate", str(output),
        "--nets", *nets, "--output", str(receipt_path),
    ]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise SystemExit(compact({
            "success": False, "error": "candidate_copper_scope_check_failed",
            "exit_code": result.returncode, "stderr": result.stderr[-1000:],
        }))
    return json.loads(lines[-1])


def summarize_routing_geometry(
    root: Path,
    source: Path,
    candidate: Path,
    nets: list[str],
    route_bbox: tuple[float, float, float, float] | None,
    output: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(root / "scripts" / "summarize_kicad_routing_geometry.py"),
        "--board", str(candidate),
        "--baseline", str(source),
        "--nets", *nets,
        "--max-via-positions", "0",
        "--output", str(output),
    ]
    if route_bbox:
        command += ["--reference-bbox-mm", *[str(value) for value in route_bbox]]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    if result.returncode != 0 or not lines:
        return {
            "success": False,
            "error": "routing_geometry_summary_failed",
            "exit_code": result.returncode,
            "diagnostic_tail": (result.stdout + result.stderr)[-800:],
        }
    return json.loads(lines[-1])


def check_selected_net_connectivity(
    config: dict[str, Any], root: Path, board: Path, nets: list[str], log_path: Path,
) -> dict[str, Any]:
    """Check selected-net connectivity on the final saved candidate.

    KRT's run summary can include temporary rip-up victims. The saved board is
    the authoritative object for completion, so query it after restoration and
    normalization while retaining the router summary as diagnostic evidence.
    """
    state = tool_state(config, root)
    checker = Path(state["repository"]) / "py_router" / "check_connected.py"
    command = [state["python"], str(checker), str(board), "--nets", *nets, "--quiet"]
    result = subprocess.run(
        command, cwd=Path(state["repository"]), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n===== FINAL SELECTED-NET CONNECTIVITY =====\n")
        handle.write(result.stdout)
        handle.write(result.stderr)
    return {
        "success": result.returncode == 0,
        "exit_code": result.returncode,
        "checked_nets": nets,
        "diagnostic_tail": (result.stdout + result.stderr)[-1200:],
    }


def command_check(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    state = tool_state(config, root)
    board = resolve(root, args.input) if args.input else None
    project_ready = None
    stack_ready = None
    next_steps: list[str] = []
    if board and board.is_file():
        project_ready = board.with_suffix(".kicad_pro").is_file()
        if not project_ready:
            next_steps.append("Create a .kicad_pro by running init_project.py for a new scaffold or opening/saving the board in KiCad.")
        try:
            require_board_stack_matches(board, config, root)
            stack_ready = True
        except SystemExit:
            stack_ready = False
            next_steps.append("Make the actual KiCad board layer stack match config/board-stack.json; do not rewrite an existing board automatically.")
    max_items = int(config.get("artifacts", {}).get("receipt_max_items", 8))
    result = {
        "success": state["ok"] and (board is None or (board.is_file() and project_ready and stack_ready)),
        "tool": {
            "expected_commit": state["expected_commit"],
            "actual_commit": state["actual_commit"],
            "python_import_ok": state["python_import_ok"],
            "missing_cli_flags": state["missing_cli_flags"],
            "netclass_mode": state["netclass_mode"],
        },
        "input": str(board) if board else None,
        "input_sha256": sha256(board) if board and board.is_file() else None,
        "profiles": bounded(sorted(config.get("profiles", {})), max_items),
        "project_ready": project_ready,
        "board_stack_ready": stack_ready,
        "next": next_steps,
    }
    print(compact(result))
    return 0 if result["success"] else 1


def command_route(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    require_tool("krt")
    if args.profile not in config.get("profiles", {}):
        raise SystemExit(f"Unknown profile: {args.profile}")
    profile = copy.deepcopy(config["profiles"][args.profile])
    require_active_stack(config, root, profile)
    require_impedance_evidence(root, profile)
    if args.grid_step is not None:
        profile["routing"]["grid_step"] = args.grid_step
    if args.max_ripup is not None:
        profile["routing"]["max_ripup"] = args.max_ripup
    source = resolve(root, args.input)
    if not source.is_file():
        raise SystemExit(f"Input board not found: {source}")
    require_board_stack_matches(
        source, config, root, physical_stackup=profile.get("engine") == "differential",
    )
    source_project = source.with_suffix(".kicad_pro")
    if not source_project.is_file():
        raise SystemExit(compact({
            "success": False, "error": "kicad_project_missing", "board": str(source),
            "next": "Run python scripts/init_project.py --layers 2 (or 4) for a new scaffold, or open/save the existing board in KiCad to create its .kicad_pro before routing.",
        }))
    load_acceptance(resolve(root, config["acceptance_policy"]), "routing_candidate", {
        "separate_candidate", "input_sha256_preserved", "project_rules_preserved",
        "no_forbidden_signal_layers", "candidate_audit_pass",
    })
    # A dry run must exercise the same resolved routing contract as a real
    # candidate route; otherwise it can fail on template nulls or validate a
    # different command than the one it reports.  The stage mutation guard is
    # the only behaviour that differs for dry-run.
    resolved_policy = require_policy("candidate_routing")
    reviewed_routing_rules(resolve(root, config["manufacturing_profile"]))
    resolved_routing = resolved_policy.get("routing", {}).get("krt", {})
    if not isinstance(resolved_routing, dict):
        raise SystemExit("Resolved design policy has no KRT routing artifact")
    evidence_bound_values = {
        key: profile["routing"].get(key)
        for key in profile.get("evidence_bound_fields", [])
    }
    profile["routing"].update(resolved_routing)
    if evidence_bound_values:
        profile["routing"].update(evidence_bound_values)
    # Exploration limits are deliberately applied last so the resolved
    # production policy cannot re-enable rip-up or unbounded search.
    exploration_environment = apply_exploration_policy(config, profile, args.explore)
    # A profile may pin hard rescue floors through a reviewed overrides file.
    # Without one, KRT uses its locked standard-tier defaults.
    if args.critical_probe:
        require_critical_probe_mode(args, profile, root)
    elif not args.dry_run:
        require_operation("route")
    include = args.nets or profile.get("include_nets", [])
    if not include:
        raise SystemExit(compact({
            "success": False,
            "error": "no_include_nets",
            "next": "Review suggested actual board nets, then pass --nets explicitly; no routing was performed.",
            "suggested_low_risk_nets": bounded(
                suggested_low_risk_nets(source, profile.get("exclude_nets", [])),
                int(config.get("artifacts", {}).get("receipt_max_items", 8)),
            ),
        }))
    if "*" in include and profile.get("require_explicit_include", True) and not args.allow_all_nets:
        raise SystemExit("Wildcard '*' requires --allow-all-nets for this profile")
    wildcard_patterns = [value for value in include if any(char in value for char in "*?[")]
    if (
        wildcard_patterns
        and profile.get("require_explicit_include", True)
        and not args.allow_wildcard_nets
        and not (include == ["*"] and args.allow_all_nets)
    ):
        raise SystemExit(
            "Wildcard net patterns require --allow-wildcard-nets: "
            + ", ".join(wildcard_patterns)
        )
    nets = expand_nets(source, include, profile.get("exclude_nets", []))
    if not nets:
        raise SystemExit("Net policy selected zero board nets")
    planner_advice = routing_task_advice(config, root, source, nets, args.config)
    if planner_advice and planner_advice.get("krt_allowed") is not True and not args.force:
        raise SystemExit(compact({
            "success": False,
            "error": "routing_task_not_for_krt",
            "planner": planner_advice,
            "next": "Use a bounded explicit trace plan, or complete the reported earlier routing-order group before KRT.",
        }))
    if config.get("attempt_policy", {}).get("require_explicit_route_area", True) and not args.route_area:
        raise SystemExit(compact({
            "success": False, "error": "route_area_required",
            "next": "Pass a stable --route-area name for this physical block so distinct net subsets and strategies share one escalation budget.",
        }))
    if args.route_area and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", args.route_area):
        raise SystemExit("--route-area must contain only letters, numbers, dot, dash, or underscore")
    if config.get("attempt_policy", {}).get("require_explicit_strategy_class", True) and not args.strategy_class:
        raise SystemExit(compact({
            "success": False, "error": "strategy_class_required",
            "next": "Pass a stable --strategy-class such as fcu-only, dual-layer, ordered-bus, or pre-escaped; numeric tuning alone is not a distinct strategy.",
        }))
    if args.strategy_class and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", args.strategy_class):
        raise SystemExit("--strategy-class must contain only letters, numbers, dot, dash, or underscore")
    route_bbox = tuple(args.route_bbox_mm) if args.route_bbox_mm else None
    if route_bbox and (route_bbox[0] >= route_bbox[2] or route_bbox[1] >= route_bbox[3]):
        raise SystemExit("--route-bbox-mm requires ordered X0 Y0 X1 Y1 coordinates")

    artifacts = config.get("artifacts", {})
    max_items = int(artifacts.get("receipt_max_items", 8))
    history_path = resolve(
        root, artifacts.get("history", "build/06-routing/evidence/krt-history.jsonl")
    )
    source_before = sha256(source)
    source_metrics = board_metrics(source, config.get("guardrails", {}).get("forbidden_signal_layers", []))
    placement_probe = subprocess.run(
        [
            str(Path(config["kicad_cli"]).with_name("python.exe")),
            str(root / "scripts" / "check_kicad_placement_routability.py"),
            "--board", str(source),
        ],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    placement_receipts = [line for line in placement_probe.stdout.splitlines() if line.strip().startswith("{")]
    try:
        placement_sha256 = json.loads(placement_receipts[-1])["candidate"]["placement_sha256"]
    except (IndexError, KeyError, json.JSONDecodeError):
        placement_sha256 = source_before
    signature_payload = compact({
        "profile": args.profile,
        "tool_commit": config["tool"].get("commit"),
        "input_sha256": source_before,
        "nets": nets,
        "routing": profile["routing"],
        "mode": "explore" if args.explore else "production",
        "exploration_environment": exploration_environment,
    })
    attempt_signature = hashlib.sha256(signature_payload.encode("utf-8")).hexdigest()[:16]
    history = read_history(history_path)
    prior_attempts = sum(
        1 for item in history
        if item.get("attempt_signature") == attempt_signature
    )
    obstacle_sha256 = copper_obstacle_fingerprint(source, route_bbox)
    automatic_family = failure_family_signature(
        args.profile, placement_sha256, nets, profile["routing"].get("layers", []), obstacle_sha256
    )
    failure_family = (
        hashlib.sha256(compact({"label": args.failure_family, "automatic": automatic_family}).encode()).hexdigest()[:16]
        if args.failure_family else automatic_family
    )
    route_area = route_area_signature(placement_sha256, nets, args.route_area)
    strategy = strategy_signature(args.profile, profile["routing"], args.strategy_class or "")
    prior_family_failures = consecutive_family_failures(history, failure_family)
    prior_family_attempts = sum(1 for item in history if item.get("failure_family") == failure_family)
    attempt_limit = int(config.get("attempt_policy", {}).get("max_identical_attempts", 2))
    family_limit = int(config.get("attempt_policy", {}).get("max_topology_failures", 2))
    rejection_limit = int(config.get("attempt_policy", {}).get("max_route_area_rejections_without_acceptance", 3))
    area_health = route_area_health(history, route_area, rejection_limit)
    if (
        prior_attempts >= attempt_limit
        and not args.override_attempt_limit
        and not args.dry_run
    ):
        raise SystemExit(compact({
            "success": False,
            "error": "identical_attempt_limit",
            "attempt_signature": attempt_signature,
            "attempts": prior_attempts,
            "limit": attempt_limit,
            "next": "Change the profile/net set/routing parameters or use --override-attempt-limit after review",
        }))
    if prior_family_failures >= family_limit and not args.override_attempt_limit and not args.dry_run:
        raise SystemExit(compact({
            "success": False,
            "error": "topology_failure_limit",
            "failure_family": failure_family,
            "placement_sha256": placement_sha256,
            "failures": prior_family_failures,
            "limit": family_limit,
            "next": config.get("attempt_policy", {}).get("on_structural_limit", "create_routing_escalation_receipt"),
        }))
    if area_health["stalled"] and not args.override_attempt_limit and not args.dry_run:
        raise SystemExit(compact({
            "success": False,
            "error": "route_area_progress_stalled",
            "health": area_health,
            "next": "Review the compact failure mix and change the routing hypothesis; do not keep numeric-tuning retries or move footprints without the separate placement escalation gate.",
        }))
    attempt_number = prior_attempts + 1
    family_attempt_number = prior_family_attempts + 1
    batch = config.get("batch_policy", {})
    batch_applies = args.profile in batch.get("profiles", [])
    batch_recommended = bool(batch_applies and len(nets) < int(batch.get("recommended_min_nets", 4)))
    small_batch_blocked = bool(
        batch_recommended
        and batch.get("mode") == "require_or_explicit_exception"
        and not args.allow_small_batch
    )
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_id = args.run_id or f"{args.profile}-{timestamp}"
    if args.dry_run:
        output = resolve(root, args.output) if args.output else root / "build/06-routing/tmp/dry-run/candidate.kicad_pcb"
        command = build_command(config, root, profile, source, output, nets)
        print(compact({
            "success": not area_health["stalled"] and not small_batch_blocked, "dry_run": True, "artifacts_created": False,
            "kind": "routing_work_packet",
            "critical_probe": args.critical_probe,
            "probe_component": args.probe_component,
            "profile": args.profile, "attempt_number": attempt_number,
            "failure_family": failure_family, "route_area": route_area,
            "strategy_signature": strategy, "strategy_class": args.strategy_class,
            "placement_sha256": placement_sha256,
            "obstacle_sha256": obstacle_sha256,
            "route_bbox_mm": route_bbox,
            "nets": bounded(nets, max_items), "output": relative(output),
            "command": command,
            "batch_recommended": batch_recommended,
            "small_batch_requires_exception": small_batch_blocked,
            "task_advice": planner_advice,
            "progress_health": area_health,
            "stop_conditions": [
                "three_consecutive_rejected_candidates",
                "two_structural_failures_require_corridor_review",
                "candidate_drc_or_guardrail_failure_requires_routing_diagnosis_not_placement",
            ],
        }))
        return 0 if not area_health["stalled"] and not small_batch_blocked else 2
    if small_batch_blocked:
        raise SystemExit(compact({
            "success": False,
            "error": "underfilled_low_risk_batch",
            "selected_net_count": len(nets),
            "recommended_min_nets": int(batch.get("recommended_min_nets", 4)),
            "next": "Add related low-risk nets to this functional work packet, use an explicit trace plan for a short endpoint link, or pass --allow-small-batch with a reviewed reason for the final leftovers.",
        }))
    run = create_run(
        "physical" if args.critical_probe else "routing",
        "krt-critical-probe" if args.critical_probe else "krt",
        run_id,
        allow_existing=args.force,
    )
    output = resolve(root, args.output) if args.output else run.tmp / "candidate.kicad_pcb"
    if output == source:
        raise SystemExit("Output must be a separate candidate; in-place routing is forbidden")
    if output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite output: {output}")
    log_path = run.tmp / "krt.log"
    report_path = run.evidence / "krt-report.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    collisions = [path for path in (log_path, report_path) if path.exists()]
    if collisions and not args.force:
        raise SystemExit("Refusing to overwrite run artifacts: " + ", ".join(map(str, collisions)))

    command = build_command(config, root, profile, source, output, nets)
    output_project = output.with_suffix(".kicad_pro")
    source_project_hash = sha256(source_project) if source_project.is_file() else None
    started = dt.datetime.now(dt.timezone.utc)
    if output.exists() and args.force:
        output.unlink()
    if args.force:
        for path in collisions:
            path.unlink()
        if output_project.is_file() and output_project != source_project:
            output_project.unlink()
    if source_project.is_file() and output_project != source_project:
        output_project.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_project, output_project)
        copy_project_companions(source_project, output_project)
    phase_ms: dict[str, float] = {}
    phase_started = time.perf_counter()
    router_environment = os.environ.copy()
    router_environment.update(exploration_environment)
    result = subprocess.run(
        command, cwd=Path(tool_state(config, root)["repository"]),
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        env=router_environment,
    )
    phase_ms["router_process"] = round((time.perf_counter() - phase_started) * 1000, 1)
    combined = result.stdout + ("\n===== STDERR =====\n" + result.stderr if result.stderr else "")
    log_path.write_text(combined, encoding="utf-8")
    source_after = sha256(source)
    if source_project.is_file() and output_project != source_project:
        # Routers may opportunistically rewrite Board Setup even with
        # --no-fix-drc-settings. Project policy remains input-owned.
        shutil.copy2(source_project, output_project)
        copy_project_companions(source_project, output_project)
    normalization = None
    restoration = None
    copper_scope = None
    routing_geometry = None
    if result.returncode == 0 and output.is_file():
        phase_started = time.perf_counter()
        restore_result = subprocess.run([
            sys.executable, str(root / "scripts" / "restore_krt_outside_scope.py"),
            "--input", str(source), "--candidate", str(output),
            "--expected-sha256", sha256(output), "--nets", *nets,
        ], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        restore_lines = [line for line in restore_result.stdout.splitlines() if line.strip().startswith("{")]
        if restore_result.returncode != 0 or not restore_lines:
            raise SystemExit(compact({
                "success": False, "error": "outside_scope_restoration_failed",
                "exit_code": restore_result.returncode, "stderr": restore_result.stderr[-1000:],
            }))
        restoration = json.loads(restore_lines[-1])
        phase_ms["outside_scope_restore"] = round((time.perf_counter() - phase_started) * 1000, 1)
        phase_started = time.perf_counter()
        normalization = normalize_candidate(
            config, root, output, nets, profile["routing"]
        )
        phase_ms["normalization_and_zone_refill"] = round((time.perf_counter() - phase_started) * 1000, 1)
        if source_project.is_file() and output_project != source_project:
            shutil.copy2(source_project, output_project)
            copy_project_companions(source_project, output_project)
        phase_started = time.perf_counter()
        copper_scope = check_candidate_copper_scope(
            root, source, output, nets, run.evidence / "copper-scope.json"
        )
        phase_ms["copper_scope"] = round((time.perf_counter() - phase_started) * 1000, 1)
        phase_started = time.perf_counter()
        routing_geometry = summarize_routing_geometry(
            root, source, output, nets, route_bbox,
            run.evidence / "routing-geometry.json",
        )
        phase_ms["routing_geometry"] = round((time.perf_counter() - phase_started) * 1000, 1)
    output_project_hash = sha256(output_project) if output_project.is_file() else None
    project_rules_preserved = bool(
        source_project_hash is None or source_project_hash == output_project_hash
    )
    summary = parse_router_summaries(combined)
    concise = concise_router(summary, max_items)
    diagnostics = {
        "log_lines": len(combined.splitlines()),
        "log_bytes": len(combined.encode("utf-8")),
        "fab_escalations": combined.count("escalated standard"),
        "route_failed_markers": combined.count("ROUTE FAILED"),
    }
    phase_started = time.perf_counter()
    metrics = board_metrics(output, config.get("guardrails", {}).get("forbidden_signal_layers", [])) if output.is_file() else None
    phase_ms["board_metrics"] = round((time.perf_counter() - phase_started) * 1000, 1)
    connectivity = None
    if output.is_file():
        phase_started = time.perf_counter()
        connectivity = check_selected_net_connectivity(config, root, output, nets, log_path)
        phase_ms["selected_net_connectivity"] = round((time.perf_counter() - phase_started) * 1000, 1)
    phase_started = time.perf_counter()
    audit = run_audit(config, root, source, output, log_path) if output.is_file() else None
    phase_ms["candidate_audit"] = round((time.perf_counter() - phase_started) * 1000, 1)
    geometry_diff = candidate_diff(source_metrics, metrics, audit)
    quality_warnings = routing_quality_warnings(routing_geometry)
    if profile.get("engine") == "differential":
        router_complete = bool(
            summary
            and summary.get("routed_diff_pairs")
            and not summary.get("failed_diff_pairs")
            and not summary.get("partial_diff_pairs")
            and not summary.get("single_ended_diff_pairs")
            and not summary.get("single_ended_followup_nets")
        )
    else:
        router_complete = bool(
            summary
            and not summary.get("failed_single")
            and not summary.get("failed_multipoint")
        )
    complete = bool(connectivity and connectivity.get("success"))
    guardrails_ok = bool(metrics is not None and not metrics["forbidden_segments"])
    acceptance = config.get("acceptance", {})
    accepted = bool(
        result.returncode == 0
        and output.is_file()
        and source_before == source_after
        and project_rules_preserved
        and guardrails_ok
        and bool(copper_scope and copper_scope.get("success"))
        and (complete or not acceptance.get("require_router_complete", True))
        and (
            not acceptance.get("require_audit_pass", True)
            or bool(audit and audit.get("baseline_pass"))
        )
        and (
            not acceptance.get("require_project_rules", True)
            or source_project_hash is not None
        )
    )
    route_ok = bool(
        result.returncode == 0
        and output.is_file()
        and guardrails_ok
        and bool(copper_scope and copper_scope.get("success"))
        and source_before == source_after
        and project_rules_preserved
    )
    outcome_class, structural_failure = classify_route_outcome(
        accepted=accepted,
        router_exit_code=result.returncode,
        output_exists=output.is_file(),
        route_ok=route_ok,
        router_complete=router_complete,
        connectivity_complete=complete,
        audit_pass=(audit or {}).get("baseline_pass"),
    )
    prior_area_structural = [
        item for item in history
        if item.get("route_area") == route_area and item.get("structural_failure") is True
    ]
    escalation = route_area_escalation(
        accepted=accepted, structural_failure=structural_failure,
        prior_area_structural=prior_area_structural, strategy=strategy,
    )
    report = {
        "schema": 1,
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "accepted": accepted,
        "critical_probe": args.critical_probe,
        "probe_component": args.probe_component,
        "router_exit_code": result.returncode,
        "profile": args.profile,
        "mode": "explore" if args.explore else "production",
        "exploration_environment": exploration_environment,
        "attempt_signature": attempt_signature,
        "attempt_number": attempt_number,
        "failure_family": failure_family,
        "family_attempt_number": family_attempt_number,
        "prior_structural_failures": prior_family_failures,
        "placement_sha256": placement_sha256,
        "obstacle_sha256": obstacle_sha256,
        "route_bbox_mm": route_bbox,
        "route_area": route_area,
        "strategy_signature": strategy,
        "strategy_class": args.strategy_class,
        "outcome_class": outcome_class,
        "structural_failure": structural_failure,
        "escalation": escalation,
        "input": str(source),
        "input_sha256_before": source_before,
        "input_sha256_after": source_after,
        "input_project": str(source_project) if source_project.is_file() else None,
        "input_project_sha256": source_project_hash,
        "output": str(output),
        "output_sha256": sha256(output) if output.is_file() else None,
        "output_project": str(output_project) if output_project.is_file() else None,
        "output_project_sha256": output_project_hash,
        "resolved_nets": nets,
        "command": command,
        "router": summary,
        "router_complete": router_complete,
        "final_selected_net_connectivity": connectivity,
        "phase_ms": phase_ms,
        "diagnostics": diagnostics,
        "board_metrics": metrics,
        "candidate_diff": geometry_diff,
        "normalization": normalization,
        "restoration": restoration,
        "copper_scope": copper_scope,
        "routing_geometry": routing_geometry,
        "audit": audit,
        "log": str(log_path),
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "success": accepted,
        "route_ok": route_ok,
        "outcome_class": outcome_class,
        "structural_failure": structural_failure,
        "route_area": route_area,
        "route_bbox_mm": route_bbox,
        "strategy_signature": strategy,
        "strategy_class": args.strategy_class,
        "escalation": escalation,
        "attempt_number": attempt_number,
        "failure_family": failure_family,
        "family_attempt_number": family_attempt_number,
        "prior_structural_failures": prior_family_failures,
        "batch": {
            "recommended": batch_recommended,
            "selected_net_count": len(nets),
            "reason": batch.get("reason") if batch_recommended else None,
        },
        "run_id": run_id,
        "output": str(output),
        "log": str(log_path),
        "report": str(report_path),
        "router": concise,
        "mode": "explore" if args.explore else "production",
        "completion": {
            "final_board_pass": complete,
            "router_summary_pass": router_complete,
        },
        "phase_ms": phase_ms,
        "diagnostics": diagnostics,
        "guardrails": {
            "input_preserved": source_before == source_after,
            "project_rules_preserved": project_rules_preserved,
            "project_rules_available": source_project_hash is not None,
            "forbidden_segments": (metrics or {}).get("forbidden_segments"),
            "copper_scope_preserved": bool(copper_scope and copper_scope.get("success")),
            "outside_scope_additions": (copper_scope or {}).get("outside_scope_additions"),
            "outside_scope_removals": (copper_scope or {}).get("outside_scope_removals"),
        },
        "audit": {
            "baseline_pass": (audit or {}).get("baseline_pass"),
            "unconnected": (audit or {}).get("unconnected"),
            "unconnected_delta": (audit or {}).get("unconnected_delta"),
            "new_violation_count": (audit or {}).get("new_violation_count"),
            "new_violation_types": (audit or {}).get("new_violation_types"),
            "new_violation_signature": (audit or {}).get("new_violation_signature"),
            "in1_non_ground_segments": (audit or {}).get("in1_non_ground_segments"),
            "drc_cache_hits": (audit or {}).get("drc_cache_hits"),
            "phase_ms": (audit or {}).get("phase_ms"),
        } if audit else None,
        "candidate_diff": concise_candidate_diff(geometry_diff, max_items),
        "normalization": normalization,
        "restoration": restoration,
        "copper_scope": copper_scope,
        "routing_geometry": concise_routing_geometry(routing_geometry, max_items),
        "quality_warnings": quality_warnings,
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_record = {
        "run_id": run_id,
        "profile": args.profile,
        "input_sha256": source_before,
        "attempt_signature": attempt_signature,
        "attempt_number": attempt_number,
        "failure_family": failure_family,
        "family_attempt_number": family_attempt_number,
        "prior_structural_failures": prior_family_failures,
        "placement_sha256": placement_sha256,
        "obstacle_sha256": obstacle_sha256,
        "route_bbox_mm": route_bbox,
        "route_area": route_area,
        "strategy_signature": strategy,
        "strategy_class": args.strategy_class,
        "outcome_class": outcome_class,
        "structural_failure": structural_failure,
        "success": accepted,
        "route_ok": receipt["route_ok"],
        "failed_single_count": concise.get("failed_single", {}).get("count"),
        "failed_multipoint_count": concise.get("failed_multipoint_count"),
        "forbidden_segment_count": sum(
            (metrics or {}).get("forbidden_segments", {}).values()
        ),
        "audit_pass": (audit or {}).get("baseline_pass"),
        "quality_warning_count": len(quality_warnings),
        "route_bbox_excursion_mm": (
            (((routing_geometry or {}).get("change") or {}).get("reference_bbox_excursion") or {}).get("max_mm")
        ),
        "report": str(report_path),
    }
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(compact(history_record) + "\n")
    finish_run(
        run, accepted,
        artifacts={
            "candidate": relative(output) if output.is_file() else None,
            "log": relative(log_path),
            "report": relative(report_path),
            "routing_geometry": relative(run.evidence / "routing-geometry.json")
            if routing_geometry and routing_geometry.get("success") else None,
        },
        metrics={
            "accepted": accepted,
            "route_ok": receipt["route_ok"],
            "attempt_number": attempt_number,
            "net_count": len(nets),
            "quality_warning_count": len(quality_warnings),
        },
    )
    receipt["output"] = relative(output) if output.is_file() else str(output)
    receipt["log"] = relative(log_path)
    receipt["report"] = relative(report_path)
    print(compact(route_stdout_index(receipt)))
    return 0 if accepted else 2


def command_report(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    artifacts = config.get("artifacts", {})
    max_items = min(max(0, args.max_items), 20)
    if args.path:
        path = resolve(root, args.path)
    else:
        path = root / "build/06-routing/evidence" / args.run_id / "krt-report.json"
    if not path.is_file():
        raise SystemExit(f"Run report not found: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    router = report.get("router")
    print(compact({
        "success": True,
        "run_id": report.get("run_id"),
        "accepted": report.get("accepted"),
        "profile": report.get("profile"),
        "mode": report.get("mode", "production"),
        "output": report.get("output"),
        "resolved_nets": bounded(report.get("resolved_nets", []), max_items),
        "router": concise_router(router, max_items),
        "completion": {
            "final_board_pass": (report.get("final_selected_net_connectivity") or {}).get("success"),
            "router_summary_pass": report.get("router_complete"),
        },
        "phase_ms": report.get("phase_ms"),
        "diagnostics": report.get("diagnostics"),
        "board_metrics": concise_metrics(report.get("board_metrics"), max_items),
        "candidate_diff": concise_candidate_diff(report.get("candidate_diff"), max_items),
        "routing_geometry": concise_routing_geometry(report.get("routing_geometry"), max_items),
        "quality_warnings": bounded(routing_quality_warnings(report.get("routing_geometry")), max_items),
        "audit": concise_audit(report.get("audit"), max_items),
        "log": report.get("log"),
        "report": str(path),
    }))
    return 0


def command_history(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    artifacts = config.get("artifacts", {})
    path = resolve(root, artifacts.get("history", "build/06-routing/evidence/krt-history.jsonl"))
    if not path.is_file():
        print(compact({"success": True, "history": [], "path": str(path)}))
        return 0
    records = read_history(path)
    limit = min(max(1, args.limit), 20)
    print(compact({
        "success": True,
        "total": len(records),
        "history": records[-limit:],
        "path": str(path),
    }))
    return 0


def command_health(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    artifacts = config.get("artifacts", {})
    path = resolve(root, artifacts.get("history", "build/06-routing/evidence/krt-history.jsonl"))
    limit = int(config.get("attempt_policy", {}).get("max_route_area_rejections_without_acceptance", 3))
    health = route_area_health(read_history(path), args.route_area, limit)
    print(compact({"success": not health["stalled"], "health": health, "history": str(path)}))
    return 0 if not health["stalled"] else 2


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__)
    top.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = top.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Verify the resolved tool revision and optional input")
    check.add_argument("--input")
    check.set_defaults(handler=command_check)
    route = commands.add_parser("route", help="Run one policy-controlled route attempt")
    route.add_argument("--profile", required=True)
    route.add_argument("--input", required=True)
    route.add_argument("--output", help="Optional separate candidate; defaults to this run's tmp lane")
    route.add_argument("--nets", nargs="+")
    route.add_argument("--run-id")
    route.add_argument("--grid-step", type=float)
    route.add_argument("--max-ripup", type=int)
    route.add_argument(
        "--explore", action="store_true",
        help="Use the reviewed bounded exploration policy (no rip-up/rescue escalation)",
    )
    route.add_argument(
        "--critical-probe", action="store_true",
        help="Run an isolated reviewed pre-promotion probe in physical stage; requires --explore and a reviewed route area.",
    )
    route.add_argument(
        "--probe-component",
        help="Reviewed subcomponent of a composite critical-probe group.",
    )
    route.add_argument("--allow-all-nets", action="store_true")
    route.add_argument("--allow-wildcard-nets", action="store_true")
    route.add_argument("--force", action="store_true")
    route.add_argument("--override-attempt-limit", action="store_true")
    route.add_argument("--allow-small-batch", action="store_true", help="Reviewed exception for final low-risk leftovers below the batch minimum")
    route.add_argument("--failure-family", help="Optional reviewed label; automatic placement/obstacle topology remains part of the family")
    route.add_argument("--route-area", help="Stable reviewed name grouping distinct strategies for one physical routing area")
    route.add_argument("--strategy-class", help="Stable structural strategy name; numeric tuning alone is not a distinct strategy")
    route.add_argument("--route-bbox-mm", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"), help="Optional reviewed local obstacle/reference scope; copper excursion is reported as a soft quality warning, not rejected")
    route.add_argument("--dry-run", action="store_true")
    route.set_defaults(handler=command_route)
    report = commands.add_parser("report", help="Read a bounded run report without opening its log")
    report_source = report.add_mutually_exclusive_group(required=True)
    report_source.add_argument("--run-id")
    report_source.add_argument("--path")
    report.add_argument("--max-items", type=int, default=8)
    report.set_defaults(handler=command_report)
    history = commands.add_parser("history", help="Show compact recent run outcomes")
    history.add_argument("--limit", type=int, default=8)
    history.set_defaults(handler=command_history)
    health = commands.add_parser("health", help="Summarize one route area's progress without rerunning tools")
    health.add_argument("--route-area", required=True)
    health.set_defaults(handler=command_health)
    return top


def main() -> int:
    args = parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
