#!/usr/bin/env python3
"""Apply a bounded, SHA-guarded explicit trace/via plan to an isolated KiCad candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from classify_routing_task import classify_board
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
PROFILES = {
    "usb_connector_esd_fanout": {
        "nets": {"USB_DP_RAW", "USB_DM_RAW"}, "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"USB_DP_RAW", "USB_DM_RAW", "GND"},
    },
    "usb_device_diff_fcu": {
        "nets": {"USB_DP_ESD", "USB_DM_ESD", "USB_DP", "USB_DM"},
        "layers": {"F.Cu"}, "via_nets": set(),
    },
    "usb_device_escape_fcu": {
        "nets": {"USB_DP_ESD", "USB_DM_ESD", "USB_DP", "USB_DM"},
        "layers": {"F.Cu"}, "via_nets": set(),
    },
    "usb_cc_local": {
        "nets": {"USB_CC1", "USB_CC2"},
        "layers": {"F.Cu", "B.Cu"}, "via_nets": {"USB_CC1", "USB_CC2", "GND"},
    },
    "internal_ui_control": {
        "nets": {"ESP_EN", "ESP_BOOT"},
        "layers": {"F.Cu", "B.Cu"}, "via_nets": {"ESP_EN", "ESP_BOOT", "GND"},
    },
    "target_control_local": {
        "nets": {
            "VTREF_ADC", "TARGET_EN_N", "TARGET_ENABLE_REQUEST",
            "VTREF_OK_N", "TARGET_INTERLOCK",
        },
        "layers": {"F.Cu", "B.Cu"},
        "via_nets": {
            "VTREF_ADC", "TARGET_EN_N", "TARGET_ENABLE_REQUEST",
            "VTREF_OK_N", "TARGET_INTERLOCK", "GND",
        },
    },
    "routing_corridor_adjustment": {
        "nets": {
            "GPIO10", "GPIO11", "GPIO12", "GPIO13", "GPIO14",
            "TARGET_EN_N", "TARGET_ENABLE_REQUEST", "VTREF_OK_N", "TARGET_INTERLOCK",
        },
        "layers": {"F.Cu", "B.Cu"},
        "via_nets": {
            "GPIO10", "GPIO11", "GPIO12", "GPIO13", "GPIO14",
            "TARGET_EN_N", "TARGET_ENABLE_REQUEST", "VTREF_OK_N", "TARGET_INTERLOCK", "GND",
        },
    },
    "ground_return_vias": {
        "nets": set(), "layers": set(), "via_nets": {"GND"},
        "min_segments": 0,
    },
    "ground_plane_access": {
        "nets": {"GND"}, "layers": {"F.Cu", "B.Cu"}, "via_nets": {"GND"},
    },
    "high_current_ground_return": {
        "nets": {"GND"}, "layers": {"F.Cu"}, "via_nets": {"GND"},
    },
    "j3_connector_esd_fanout": {
        "nets": {"VTREF", "SWDIO", "SWCLK", "NRST", "BOOT0", "TARGET_TX", "TARGET_RX", "GND"},
        "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"VTREF", "SWDIO", "SWCLK", "NRST", "BOOT0", "TARGET_TX", "TARGET_RX", "GND"},
    },
    "buck_sw_fcu_neckdown": {
        "nets": {"BUCK_SW"}, "layers": {"F.Cu"}, "via_nets": set(),
    },
    "buck_power_local": {
        "nets": {"3V3", "USB_5V", "GND"}, "layers": {"F.Cu"},
        "via_nets": {"GND"},
    },
    "buck_smt_clearance_rework": {
        "nets": {"3V3", "GND", "BUCK_SW", "BUCK_BST"},
        "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"3V3", "GND"},
        "requires_return_vias": False,
    },
    "buck_feedback_sense": {
        "nets": {"3V3"}, "layers": {"F.Cu", "B.Cu"}, "via_nets": {"3V3", "GND"},
    },
    "3v3_local_escape": {
        "nets": {"3V3"}, "layers": {"F.Cu"}, "via_nets": set(),
    },
    "3v3_low_current_branch": {
        "nets": {"3V3"}, "layers": {"F.Cu"}, "via_nets": set(),
    },
    "3v3_low_current_layer_transition": {
        "nets": {"3V3"}, "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"3V3"}, "requires_return_vias": False, "max_vias": 1,
    },
    "3v3_main_trunk": {
        "nets": {"3V3"}, "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"3V3", "GND"},
    },
    "usb_5v_distribution": {
        "nets": {"USB_5V"}, "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"USB_5V", "GND"},
    },
    "usb_vbus_raw_escape": {
        "nets": {"USB_VBUS_RAW"}, "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"USB_VBUS_RAW", "GND"},
    },
    "usb_vbus_sense_dp_reroute": {
        "nets": {"USB_VBUS_RAW", "USB_DP_RAW"}, "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"USB_VBUS_RAW", "USB_DP_RAW", "GND"},
    },
    "usb_shield_bond": {
        "nets": {"USB_SHIELD"}, "layers": {"F.Cu", "B.Cu"},
        "via_nets": {"USB_SHIELD"}, "requires_return_vias": False, "max_vias": 1,
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def sexpr_block_spans(text: str, head: str):
    """Yield balanced KiCad S-expression spans without loading the whole AST."""
    marker = f"({head}"
    start = 0
    while True:
        index = text.find(marker, start)
        if index < 0:
            return
        boundary = index + len(marker)
        if boundary < len(text) and not text[boundary].isspace():
            start = boundary
            continue
        depth = 0
        quoted = False
        escaped = False
        for end in range(index, len(text)):
            char = text[end]
            if escaped:
                escaped = False
                continue
            if char == "\\" and quoted:
                escaped = True
                continue
            if char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        yield index, end + 1, text[index:end + 1]
                        start = end + 1
                        break
        else:
            return


def sexpr_field(block: str, name: str) -> str:
    match = re.search(rf'\({re.escape(name)}\s+([^()]+?)\)', block)
    return " ".join(match.group(1).split()) if match else ""


def remove_exact_copper_blocks(
    path: Path, plan: dict[str, object], tolerance_mm: float = 0.005,
) -> dict[str, list[dict[str, object]]]:
    """Remove reviewed track/via blocks textually, avoiding KiCad 10 SWIG deletion hangs."""
    text = path.read_text(encoding="utf-8")
    net_names = {
        code: bytes(name, "utf-8").decode("unicode_escape")
        for code, name in re.findall(r'\(net\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\)', text)
    }

    def point(value: str) -> tuple[float, float]:
        parts = value.split()
        return float(parts[0]), float(parts[1])

    def near(left: tuple[float, float], right: tuple[float, float]) -> bool:
        return abs(left[0] - right[0]) <= tolerance_mm and abs(left[1] - right[1]) <= tolerance_mm

    def net_name(block: str) -> str:
        value = sexpr_field(block, "net")
        if value.startswith('"') and value.endswith('"'):
            return bytes(value[1:-1], "utf-8").decode("unicode_escape")
        return net_names.get(value, value)

    spans: list[tuple[int, int]] = []
    used: set[tuple[int, int]] = set()
    removed_segments: list[dict[str, object]] = []
    removed_vias: list[dict[str, object]] = []
    for index, item in enumerate(plan.get("remove_segments", [])):
        wanted_start = tuple(float(value) for value in item["start_mm"])
        wanted_end = tuple(float(value) for value in item["end_mm"])
        matches = []
        for start, end, block in sexpr_block_spans(text, "segment"):
            layer = sexpr_field(block, "layer").strip('"')
            net = net_name(block)
            actual_start, actual_end = point(sexpr_field(block, "start")), point(sexpr_field(block, "end"))
            if net == item["net"] and layer == item["layer"] and (
                (near(actual_start, wanted_start) and near(actual_end, wanted_end))
                or (near(actual_start, wanted_end) and near(actual_end, wanted_start))
            ):
                matches.append((start, end))
        if len(matches) != 1:
            raise SystemExit(f"Exact textual removed segment {index} is missing or ambiguous")
        spans.append(matches[0])
        removed_segments.append({
            "index": index, "net": item["net"], "layer": item["layer"],
            "start_mm": item["start_mm"], "end_mm": item["end_mm"],
        })
    for index, item in enumerate(plan.get("remove_vias", [])):
        wanted = tuple(float(value) for value in item["at_mm"])
        matches: list[tuple[int, int, str]] = []
        for start, end, block in sexpr_block_spans(text, "via"):
            net = net_name(block)
            if net == item["net"] and near(point(sexpr_field(block, "at")), wanted):
                matches.append((start, end, block))
        if len(matches) != 1:
            raise SystemExit(f"Exact textual removed via {index} is missing or ambiguous")
        start, end, block = matches[0]
        spans.append((start, end))
        removed_vias.append({
            "index": index, "net": item["net"], "at_mm": item["at_mm"],
            "diameter_mm": float(sexpr_field(block, "size")),
            "drill_mm": float(sexpr_field(block, "drill")),
        })
    for index, value in enumerate(plan.get("existing_return_vias", [])):
        wanted = tuple(float(component) for component in value)
        matches = [
            1 for _, _, block in sexpr_block_spans(text, "via")
            if net_name(block) == "GND"
            and near(point(sexpr_field(block, "at")), wanted)
        ]
        if len(matches) != 1:
            raise SystemExit(f"Reviewed existing GND return via {index} is missing or ambiguous")
    for span in spans:
        if span in used:
            raise SystemExit("A copper block was selected for removal more than once")
        used.add(span)
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    path.write_text(text, encoding="utf-8", newline="")
    return {"segments": removed_segments, "vias": removed_vias}


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def validate_critical_probe(group: str | None, component: str | None) -> tuple[str, str]:
    policy = json.loads((ROOT / "config/placement-routability.json").read_text(encoding="utf-8"))
    checkpoint = policy.get("checkpoint") or {}
    groups = [str(value) for value in checkpoint.get("critical_probe_groups", [])]
    if group not in groups:
        raise SystemExit("Critical trace probe group is not reviewed")
    expected = [
        str(value)
        for value in (checkpoint.get("critical_probe_components") or {}).get(group, [])
    ]
    if expected and component not in expected:
        raise SystemExit("Critical trace probe component is not reviewed for its group")
    return str(group), str(component)


def run_selected_connectivity(board: Path, nets: list[str], log: Path) -> dict[str, object]:
    config = json.loads((ROOT / "config/kicad-routing-tools.json").read_text(encoding="utf-8"))
    repository = (ROOT / config["tool"]["repository"]).resolve()
    python = (ROOT / config["tool"]["python"]).resolve()
    checker = repository / "py_router/check_connected.py"
    expected = str(config["tool"]["commit"])
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    ).stdout.strip()
    if actual != expected or not python.is_file() or not checker.is_file():
        raise SystemExit("Locked KRT connectivity checker is unavailable or at the wrong commit")
    result = subprocess.run(
        [str(python), str(checker), str(board), "--nets", *nets, "--quiet"],
        cwd=repository, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    return {
        "success": result.returncode == 0,
        "exit_code": result.returncode,
        "checked_nets": nets,
        "diagnostic_tail": (result.stdout + result.stderr)[-1200:],
    }


def run_anchor_connectivity(board: Path, groups: list[dict[str, object]], output: Path) -> dict[str, object]:
    config = json.loads((ROOT / "config/kicad-routing-tools.json").read_text(encoding="utf-8"))
    python = (ROOT / config["tool"]["python"]).resolve()
    rows: list[dict[str, object]] = []
    output.parent.mkdir(parents=True, exist_ok=True)
    for index, group in enumerate(groups):
        command = [
            str(python), str(ROOT / "scripts/check_kicad_anchor_connectivity.py"),
            "--board", str(board), "--net", str(group["net"]),
        ]
        for pad in group.get("pads", []):
            command += ["--pad", str(pad)]
        for point in group.get("vias", []):
            command += ["--via", str(point[0]), str(point[1])]
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
        if not lines:
            raise SystemExit(f"Anchor connectivity group {index} produced no receipt: {result.stderr[-500:]}")
        row = json.loads(lines[-1])
        row["exit_code"] = result.returncode
        rows.append(row)
    receipt = {"success": all(row.get("success") is True for row in rows), "groups": rows}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def run_candidate_audit(source: Path, candidate: Path, output: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/krt_candidate_audit.py"),
            "--input", str(source), "--candidate", str(candidate),
            "--report", str(output),
        ],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if not output.is_file():
        raise SystemExit("Critical trace probe candidate audit did not produce a report")
    report = json.loads(output.read_text(encoding="utf-8"))
    report["exit_code"] = result.returncode
    return report


def load_plan(path: Path) -> dict[str, object]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Trace plan is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("kind") != "kicad_trace_plan":
        raise SystemExit("Trace plan must be schema-1 kicad_trace_plan")
    profile_name = plan.get("profile")
    if profile_name not in PROFILES:
        raise SystemExit("Trace plan profile is not supported")
    profile = PROFILES[str(profile_name)]
    segments = plan.get("segments", [])
    vias = plan.get("vias", [])
    remove_segments = plan.get("remove_segments", [])
    remove_vias = plan.get("remove_vias", [])
    min_segments = int(profile.get("min_segments", 1))
    if not isinstance(segments, list) or not min_segments <= len(segments) <= 40:
        raise SystemExit(f"A bounded trace plan requires {min_segments}-40 segments")
    max_vias = int(profile.get("max_vias", 12))
    if not isinstance(vias, list) or len(vias) > max_vias:
        raise SystemExit(f"This bounded trace profile may contain at most {max_vias} vias")
    if not isinstance(remove_segments, list) or len(remove_segments) > 20:
        raise SystemExit("A bounded trace plan may remove at most 20 exact segments")
    if not isinstance(remove_vias, list) or len(remove_vias) > 8:
        raise SystemExit("A bounded trace plan may remove at most 8 exact vias")
    for item in segments:
        if not isinstance(item, dict) or item.get("net") not in profile["nets"]:
            raise SystemExit("Every segment must use a net allowed by its trace profile")
        if item.get("layer") not in profile["layers"]:
            raise SystemExit("Trace segment layer is not allowed by its profile")
        for key in ("start_mm", "end_mm"):
            point = item.get(key)
            if not isinstance(point, list) or len(point) != 2:
                raise SystemExit(f"Segment {key} must be [x_mm, y_mm]")
            float(point[0]); float(point[1])
        if "width_mm" in item:
            float(item["width_mm"])
    for item in remove_segments:
        if not isinstance(item, dict) or item.get("net") not in profile["nets"]:
            raise SystemExit("Every removed segment must use a net allowed by its trace profile")
        if item.get("layer") not in profile["layers"]:
            raise SystemExit("Removed segment layer is not allowed by its profile")
        for key in ("start_mm", "end_mm"):
            point = item.get(key)
            if not isinstance(point, list) or len(point) != 2:
                raise SystemExit(f"Removed segment {key} must be [x_mm, y_mm]")
            float(point[0]); float(point[1])
    for item in vias:
        if not isinstance(item, dict) or item.get("net") not in profile["via_nets"]:
            raise SystemExit("Every via must use a net allowed by its trace profile")
        point = item.get("at_mm")
        if not isinstance(point, list) or len(point) != 2:
            raise SystemExit("Via at_mm must be [x_mm, y_mm]")
        float(point[0]); float(point[1])
    for item in remove_vias:
        if not isinstance(item, dict) or item.get("net") not in profile["via_nets"]:
            raise SystemExit("Every removed via must use a net allowed by its trace profile")
        point = item.get("at_mm")
        if not isinstance(point, list) or len(point) != 2:
            raise SystemExit("Removed via at_mm must be [x_mm, y_mm]")
        float(point[0]); float(point[1])
    existing_return_vias = plan.get("existing_return_vias", [])
    if not isinstance(existing_return_vias, list) or len(existing_return_vias) > 8:
        raise SystemExit("existing_return_vias must be a bounded list")
    for point in existing_return_vias:
        if not isinstance(point, list) or len(point) != 2:
            raise SystemExit("Existing return vias must use [x_mm, y_mm]")
        float(point[0]); float(point[1])
    if (
        any(item.get("layer") == "B.Cu" for item in segments)
        and profile.get("requires_return_vias", True)
    ):
        ground_vias = sum(item.get("net") == "GND" for item in vias)
        if ground_vias + len(existing_return_vias) < 2:
            raise SystemExit("A B.Cu connector crossover requires at least two explicit GND return vias")
    pairs = plan.get("parallel_pairs", [])
    if not isinstance(pairs, list) or len(pairs) > 20:
        raise SystemExit("parallel_pairs must be a bounded list")
    if profile_name == "usb_device_diff_fcu" and not pairs:
        raise SystemExit("Protected USB trace plans require explicit parallel-pair evidence")
    anchor_groups = plan.get("anchor_groups", [])
    if not isinstance(anchor_groups, list) or len(anchor_groups) > 8:
        raise SystemExit("anchor_groups must be a bounded list")
    allowed_anchor_nets = profile["nets"] | profile["via_nets"]
    for group in anchor_groups:
        if not isinstance(group, dict) or group.get("net") not in allowed_anchor_nets:
            raise SystemExit("Every anchor group must use a net allowed by its trace profile")
        pads = group.get("pads", [])
        anchor_vias = group.get("vias", [])
        if not isinstance(pads, list) or not isinstance(anchor_vias, list) or len(pads) + len(anchor_vias) < 2:
            raise SystemExit("Every anchor group requires at least two pad/via anchors")
        if len(pads) > 12 or len(anchor_vias) > 12:
            raise SystemExit("Every anchor group must remain bounded")
        for value in pads:
            if not isinstance(value, str) or ":" not in value:
                raise SystemExit("Anchor pads must use REF:PAD syntax")
        for point in anchor_vias:
            if not isinstance(point, list) or len(point) != 2:
                raise SystemExit("Anchor vias must use [x_mm, y_mm]")
            float(point[0]); float(point[1])
    return plan


def validate_acceptance_anchors(plan: dict[str, object]) -> None:
    """Require bounded local connectivity claims for every net receiving track copper."""
    segment_nets = {str(item["net"]) for item in plan.get("segments", [])}
    if not segment_nets:
        return
    groups = plan.get("anchor_groups", [])
    grouped_nets = {str(group["net"]) for group in groups}
    missing = sorted(segment_nets - grouped_nets)
    if missing:
        raise SystemExit(
            "Every net receiving explicit track copper requires an anchor group: "
            + ", ".join(missing)
        )


def validate_parallel_pairs(plan: dict[str, object], geometry: dict[str, float]) -> None:
    segments = plan["segments"]
    expected = geometry["track_width_mm"] + geometry["pair_gap_mm"]
    for pair in plan.get("parallel_pairs", []):
        if not isinstance(pair, dict):
            raise SystemExit("Every parallel pair must identify two segment indexes")
        indexes = [pair.get("positive_segment"), pair.get("negative_segment")]
        if any(not isinstance(index, int) or index < 0 or index >= len(segments) for index in indexes):
            raise SystemExit("Parallel-pair segment index is invalid")
        left, right = (segments[indexes[0]], segments[indexes[1]])
        if left["layer"] != right["layer"] or not left["net"].endswith("DP_ESD") or not right["net"].endswith("DM_ESD"):
            raise SystemExit("Parallel-pair evidence must bind protected DP/DM segments on one layer")
        ax, ay = map(float, left["start_mm"]); bx, by = map(float, left["end_mm"])
        cx, cy = map(float, right["start_mm"]); dx, dy = map(float, right["end_mm"])
        vx, vy, wx, wy = bx - ax, by - ay, dx - cx, dy - cy
        left_length, right_length = math.hypot(vx, vy), math.hypot(wx, wy)
        if left_length <= 0 or abs(left_length - right_length) > 0.001 or abs(vx * wy - vy * wx) > 0.001:
            raise SystemExit("Reviewed parallel-pair segments must be parallel and equal length")
        distance = abs(vx * (cy - ay) - vy * (cx - ax)) / left_length
        if abs(distance - expected) > 0.001:
            raise SystemExit("Parallel-pair center spacing does not match reviewed width plus gap")


def point_segment_distance_mm(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Return the shortest Euclidean distance from a point to a segment."""
    px, py = point
    ax, ay = start
    bx, by = end
    vx, vy = bx - ax, by - ay
    length_sq = vx * vx + vy * vy
    if length_sq <= 1e-18:
        return math.hypot(px - ax, py - ay)
    ratio = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / length_sq))
    return math.hypot(px - (ax + ratio * vx), py - (ay + ratio * vy))


def segment_segment_distance_mm(
    first_start: tuple[float, float], first_end: tuple[float, float],
    second_start: tuple[float, float], second_end: tuple[float, float],
) -> float:
    """Return zero for intersecting segments, otherwise their endpoint distance."""
    def orientation(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, point) -> bool:
        return (
            min(a[0], b[0]) - 1e-9 <= point[0] <= max(a[0], b[0]) + 1e-9
            and min(a[1], b[1]) - 1e-9 <= point[1] <= max(a[1], b[1]) + 1e-9
            and abs(orientation(a, b, point)) <= 1e-9
        )

    values = (
        orientation(first_start, first_end, second_start),
        orientation(first_start, first_end, second_end),
        orientation(second_start, second_end, first_start),
        orientation(second_start, second_end, first_end),
    )
    crosses = values[0] * values[1] < 0 and values[2] * values[3] < 0
    touches = (
        (abs(values[0]) <= 1e-9 and on_segment(first_start, first_end, second_start))
        or (abs(values[1]) <= 1e-9 and on_segment(first_start, first_end, second_end))
        or (abs(values[2]) <= 1e-9 and on_segment(second_start, second_end, first_start))
        or (abs(values[3]) <= 1e-9 and on_segment(second_start, second_end, first_end))
    )
    if crosses or touches:
        return 0.0
    return min(
        point_segment_distance_mm(first_start, second_start, second_end),
        point_segment_distance_mm(first_end, second_start, second_end),
        point_segment_distance_mm(second_start, first_start, first_end),
        point_segment_distance_mm(second_end, first_start, first_end),
    )


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (current[1] > point[1]) != (previous[1] > point[1]):
            crossing = (
                (previous[0] - current[0]) * (point[1] - current[1])
                / (previous[1] - current[1]) + current[0]
            )
            if point[0] < crossing:
                inside = not inside
        previous = current
    return inside


def point_polygon_distance_mm(point: tuple[float, float], polygon: list[tuple[float, float]]) -> float:
    if point_in_polygon(point, polygon):
        return 0.0
    return min(
        point_segment_distance_mm(point, polygon[index - 1], polygon[index])
        for index in range(len(polygon))
    )


def segment_polygon_distance_mm(
    start: tuple[float, float], end: tuple[float, float], polygon: list[tuple[float, float]],
) -> float:
    if point_in_polygon(start, polygon) or point_in_polygon(end, polygon):
        return 0.0
    return min(
        segment_segment_distance_mm(start, end, polygon[index - 1], polygon[index])
        for index in range(len(polygon))
    )


def copper_clearance_violations(
    plan: dict[str, object], geometry: dict[str, float],
    obstacles: list[dict[str, object]], *, clearance_mm: float,
    approximation_tolerance_mm: float = 0.01,
) -> list[dict[str, object]]:
    """Preflight planned outer-layer copper against existing and planned copper."""
    violations: list[dict[str, object]] = []
    default_width = float(geometry["track_width_mm"])
    via_radius = float(geometry["via_diameter_mm"]) / 2
    planned_segments = [
        {
            "kind": "segment", "net": str(item["net"]), "layers": {str(item["layer"])},
            "start": tuple(map(float, item["start_mm"])),
            "end": tuple(map(float, item["end_mm"])),
            "radius": float(item.get("width_mm", default_width)) / 2,
            "label": f"planned segment {index}", "plan_item": "segment", "plan_index": index,
        }
        for index, item in enumerate(plan["segments"])
    ]
    planned_vias = [
        {
            "kind": "circle", "net": str(item["net"]), "layers": {"F.Cu", "B.Cu"},
            "center": tuple(map(float, item["at_mm"])), "radius": via_radius,
            "label": f"planned via {index}", "plan_item": "via", "plan_index": index,
        }
        for index, item in enumerate(plan["vias"])
    ]
    planned = [*planned_segments, *planned_vias]

    def shape_distance(first: dict[str, object], second: dict[str, object]) -> float:
        if first["kind"] == "segment" and second["kind"] == "segment":
            return segment_segment_distance_mm(first["start"], first["end"], second["start"], second["end"]) - float(first["radius"]) - float(second["radius"])
        if first["kind"] == "segment" and second["kind"] == "circle":
            return point_segment_distance_mm(second["center"], first["start"], first["end"]) - float(first["radius"]) - float(second["radius"])
        if first["kind"] == "circle" and second["kind"] == "segment":
            return shape_distance(second, first)
        if first["kind"] == "circle" and second["kind"] == "circle":
            return math.dist(first["center"], second["center"]) - float(first["radius"]) - float(second["radius"])
        if first["kind"] == "segment" and second["kind"] == "polygon":
            return segment_polygon_distance_mm(first["start"], first["end"], second["polygon"]) - float(first["radius"])
        if first["kind"] == "circle" and second["kind"] == "polygon":
            return point_polygon_distance_mm(first["center"], second["polygon"]) - float(first["radius"])
        if first["kind"] == "polygon":
            return shape_distance(second, first)
        raise ValueError("Unsupported copper obstacle geometry")

    for planned_index, item in enumerate(planned):
        comparisons = [*obstacles, *planned[planned_index + 1:]]
        for obstacle in comparisons:
            if item["net"] == obstacle.get("net"):
                continue
            if not set(item["layers"]) & set(obstacle.get("layers", [])):
                continue
            actual = shape_distance(item, obstacle)
            if actual + approximation_tolerance_mm + 1e-9 >= clearance_mm:
                continue
            violations.append({
                "type": "copper_clearance",
                "item": item["plan_item"],
                "index": item["plan_index"],
                "net": item["net"],
                "layers": sorted(set(item["layers"]) & set(obstacle.get("layers", []))),
                "obstacle": obstacle.get("label"),
                "obstacle_net": obstacle.get("net"),
                "actual_mm": round(actual, 6),
                "required_mm": round(clearance_mm, 6),
            })
    return violations


def board_copper_obstacles(board) -> list[dict[str, object]]:
    """Extract compact outer-layer track, via and rectangular-pad obstacles."""
    import pcbnew

    obstacles: list[dict[str, object]] = []
    for track in board.GetTracks():
        net = track.GetNetname() or None
        if isinstance(track, pcbnew.PCB_VIA):
            position = track.GetPosition()
            obstacles.append({
                "kind": "circle", "net": net, "layers": {"F.Cu", "B.Cu"},
                "center": (pcbnew.ToMM(position.x), pcbnew.ToMM(position.y)),
                "radius": pcbnew.ToMM(track.GetWidth(pcbnew.F_Cu)) / 2,
                "label": f"via {net or '<no-net>'} at {pcbnew.ToMM(position.x):.3f},{pcbnew.ToMM(position.y):.3f}",
            })
            continue
        layer = board.GetLayerName(track.GetLayer())
        if layer not in {"F.Cu", "B.Cu"}:
            continue
        start, end = track.GetStart(), track.GetEnd()
        obstacles.append({
            "kind": "segment", "net": net, "layers": {layer},
            "start": (pcbnew.ToMM(start.x), pcbnew.ToMM(start.y)),
            "end": (pcbnew.ToMM(end.x), pcbnew.ToMM(end.y)),
            "radius": pcbnew.ToMM(track.GetWidth()) / 2,
            "label": f"{layer} track {net or '<no-net>'}",
        })
    layer_ids = {name: board.GetLayerID(name) for name in ("F.Cu", "B.Cu")}
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            layers = {name for name, layer_id in layer_ids.items() if pad.IsOnLayer(layer_id)}
            if not layers:
                continue
            position, size = pad.GetPosition(), pad.GetSize()
            center = (pcbnew.ToMM(position.x), pcbnew.ToMM(position.y))
            half_x, half_y = pcbnew.ToMM(size.x) / 2, pcbnew.ToMM(size.y) / 2
            try:
                angle = math.radians(float(pad.GetOrientationDegrees()))
            except AttributeError:
                angle = math.radians(float(pad.GetOrientation().AsDegrees()))
            cosine, sine = math.cos(angle), math.sin(angle)
            polygon = []
            for local_x, local_y in ((-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)):
                polygon.append((
                    center[0] + local_x * cosine - local_y * sine,
                    center[1] + local_x * sine + local_y * cosine,
                ))
            obstacles.append({
                "kind": "polygon", "net": pad.GetNetname() or None, "layers": layers,
                "polygon": polygon,
                "label": f"pad {footprint.GetReference()}:{pad.GetNumber() or '<no-number>'}",
            })
    return obstacles


def hole_clearance_violations(
    plan: dict[str, object],
    geometry: dict[str, float],
    holes: list[dict[str, object]],
    *,
    hole_clearance_mm: float,
    hole_to_hole_mm: float,
) -> list[dict[str, object]]:
    """Conservatively preflight planned copper against existing drilled holes.

    Oval drills use their largest radius.  This may reject a marginal plan but
    cannot silently accept a centerline that crosses the drilled envelope.
    Same-net plated pads are allowed as trace endpoints/connections; NPTH and
    unrelated plated holes always remain obstacles.
    """
    violations: list[dict[str, object]] = []
    default_width = float(geometry["track_width_mm"])
    for index, item in enumerate(plan["segments"]):
        net = str(item["net"])
        start = tuple(map(float, item["start_mm"]))
        end = tuple(map(float, item["end_mm"]))
        width = float(item.get("width_mm", default_width))
        for hole in holes:
            if not hole.get("npth") and hole.get("net") == net:
                continue
            center = tuple(map(float, hole["center_mm"]))
            hole_radius = float(hole["radius_mm"])
            center_distance = point_segment_distance_mm(center, start, end)
            actual = center_distance - hole_radius - width / 2
            if actual + 1e-9 >= hole_clearance_mm:
                continue
            violations.append({
                "type": "hole_clearance",
                "item": "segment",
                "index": index,
                "net": net,
                "layer": item["layer"],
                "hole": hole.get("label"),
                "actual_mm": round(actual, 6),
                "required_mm": round(hole_clearance_mm, 6),
            })
    via_radius = float(geometry["via_diameter_mm"]) / 2
    via_drill_radius = float(geometry["via_drill_mm"]) / 2
    for index, item in enumerate(plan["vias"]):
        center = tuple(map(float, item["at_mm"]))
        for hole in holes:
            hole_center = tuple(map(float, hole["center_mm"]))
            hole_radius = float(hole["radius_mm"])
            center_distance = math.dist(center, hole_center)
            copper_actual = center_distance - hole_radius - via_radius
            drill_actual = center_distance - hole_radius - via_drill_radius
            if copper_actual + 1e-9 >= hole_clearance_mm and drill_actual + 1e-9 >= hole_to_hole_mm:
                continue
            violations.append({
                "type": "hole_clearance" if copper_actual < hole_clearance_mm else "hole_to_hole",
                "item": "via",
                "index": index,
                "net": item["net"],
                "hole": hole.get("label"),
                "actual_mm": round(min(copper_actual, drill_actual), 6),
                "required_mm": round(
                    hole_clearance_mm if copper_actual < hole_clearance_mm else hole_to_hole_mm,
                    6,
                ),
            })
    return violations


def trace_plan_geometry_preflight(board, plan: dict[str, object], geometry: dict[str, float]) -> dict[str, object]:
    """Check existing copper, holes and board-edge containment before creating a candidate."""
    import pcbnew

    settings = board.GetDesignSettings()
    hole_clearance_mm = pcbnew.ToMM(settings.m_HoleClearance)
    hole_to_hole_mm = pcbnew.ToMM(settings.m_HoleToHoleMin)
    edge_clearance_mm = pcbnew.ToMM(settings.m_CopperEdgeClearance)
    holes: list[dict[str, object]] = []
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            drill_x = pcbnew.ToMM(pad.GetDrillSizeX())
            drill_y = pcbnew.ToMM(pad.GetDrillSizeY())
            if max(drill_x, drill_y) <= 0:
                continue
            position = pad.GetPosition()
            holes.append({
                "center_mm": [pcbnew.ToMM(position.x), pcbnew.ToMM(position.y)],
                "radius_mm": max(drill_x, drill_y) / 2,
                "npth": pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH,
                "net": pad.GetNetname() or None,
                "label": f"{footprint.GetReference()}:{pad.GetNumber() or 'NPTH'}",
            })
    violations = hole_clearance_violations(
        plan,
        geometry,
        holes,
        hole_clearance_mm=hole_clearance_mm,
        hole_to_hole_mm=hole_to_hole_mm,
    )
    copper_obstacles = board_copper_obstacles(board)
    violations.extend(copper_clearance_violations(
        plan, geometry, copper_obstacles,
        clearance_mm=float(geometry["clearance_mm"]),
    ))

    outline = pcbnew.SHAPE_POLY_SET()
    outline_ok = bool(board.GetBoardPolygonOutlines(outline, False)) and outline.OutlineCount() > 0
    deflated: dict[int, object] = {}

    def inside_edge(point_mm: tuple[float, float], copper_radius_mm: float) -> bool:
        if not outline_ok:
            return False
        inset_mm = edge_clearance_mm + copper_radius_mm
        inset_key = round(pcbnew.FromMM(inset_mm))
        polygon = deflated.get(inset_key)
        if polygon is None:
            polygon = pcbnew.SHAPE_POLY_SET(outline)
            polygon.Deflate(
                inset_key,
                pcbnew.CORNER_STRATEGY_CHAMFER_ALL_CORNERS,
                pcbnew.FromMM(0.01),
            )
            deflated[inset_key] = polygon
        point = pcbnew.VECTOR2I(pcbnew.FromMM(point_mm[0]), pcbnew.FromMM(point_mm[1]))
        return bool(polygon.Contains(point))

    default_width = float(geometry["track_width_mm"])
    for index, item in enumerate(plan["segments"]):
        start = tuple(map(float, item["start_mm"]))
        end = tuple(map(float, item["end_mm"]))
        length = math.dist(start, end)
        sample_count = max(1, math.ceil(length / 0.1))
        radius = float(item.get("width_mm", default_width)) / 2
        bad_point = None
        for sample in range(sample_count + 1):
            ratio = sample / sample_count
            point = (
                start[0] + ratio * (end[0] - start[0]),
                start[1] + ratio * (end[1] - start[1]),
            )
            if not inside_edge(point, radius):
                bad_point = point
                break
        if bad_point is not None:
            violations.append({
                "type": "copper_edge_clearance",
                "item": "segment",
                "index": index,
                "net": item["net"],
                "layer": item["layer"],
                "at_mm": [round(value, 6) for value in bad_point],
                "required_mm": round(edge_clearance_mm, 6),
            })
    via_radius = float(geometry["via_diameter_mm"]) / 2
    for index, item in enumerate(plan["vias"]):
        point = tuple(map(float, item["at_mm"]))
        if inside_edge(point, via_radius):
            continue
        violations.append({
            "type": "copper_edge_clearance",
            "item": "via",
            "index": index,
            "net": item["net"],
            "at_mm": list(point),
            "required_mm": round(edge_clearance_mm, 6),
        })
    return {
        "schema": 1,
        "kind": "kicad_trace_geometry_preflight",
        "success": outline_ok and not violations,
        "checked": {
            "segments": len(plan["segments"]),
            "vias": len(plan["vias"]),
            "drilled_holes": len(holes),
            "copper_obstacles": len(copper_obstacles),
        },
        "constraints_mm": {
            "hole_clearance": hole_clearance_mm,
            "hole_to_hole": hole_to_hole_mm,
            "copper_edge_clearance": edge_clearance_mm,
            "copper_clearance": float(geometry["clearance_mm"]),
            "copper_approximation_tolerance": 0.01,
        },
        "outline_available": outline_ok,
        "violation_count": len(violations),
        "violations": violations[:20],
    }


def reviewed_geometry(profile_name: str) -> dict[str, float]:
    config = json.loads((ROOT / "config/kicad-routing-tools.json").read_text(encoding="utf-8"))
    profile = config["profiles"][profile_name]
    routing = profile["routing"]
    if profile_name == "high_current_ground_return":
        manufacturing = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
        values = manufacturing["routing_rules"]["values"]
        power_width = float(config["profiles"]["outer_power_fcu"]["routing"]["track_width"])
        if abs(float(routing["track_width"]) - power_width) > 1e-12:
            raise SystemExit("High-current ground width no longer matches reviewed outer-power width")
        return {
            "track_width_mm": power_width,
            "clearance_mm": float(values["clearance"]),
            "via_diameter_mm": float(values["via_size"]),
            "via_drill_mm": float(values["via_drill"]),
        }
    if profile_name == "3v3_main_trunk":
        manufacturing = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
        values = manufacturing["routing_rules"]["values"]
        power_width = float(config["profiles"]["outer_power"]["routing"]["track_width"])
        if abs(float(routing["track_width"]) - power_width) > 1e-12:
            raise SystemExit("3V3 main-trunk width no longer matches reviewed outer-power width")
        return {
            "track_width_mm": power_width,
            "neckdown_width_mm": float(values["track_width"]),
            "clearance_mm": float(values["clearance"]),
            "via_diameter_mm": float(values["via_size"]),
            "via_drill_mm": float(values["via_drill"]),
        }
    if profile_name == "usb_shield_bond":
        manufacturing = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
        values = manufacturing["routing_rules"]["values"]
        strap_width = float(config["profiles"]["outer_power"]["routing"]["track_width"])
        if abs(float(routing["track_width"]) - strap_width) > 1e-12:
            raise SystemExit("USB shield strap width no longer matches the reviewed wide-copper width")
        return {
            "track_width_mm": strap_width,
            "neckdown_width_mm": float(values["track_width"]),
            "clearance_mm": float(values["clearance"]),
            "via_diameter_mm": float(values["via_size"]),
            "via_drill_mm": float(values["via_drill"]),
        }
    if profile_name in {"buck_sw_fcu_neckdown", "buck_power_local", "buck_smt_clearance_rework"}:
        manufacturing = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
        default_width = float(manufacturing["routing_rules"]["values"]["track_width"])
        power_width = float(config["profiles"]["outer_power_fcu"]["routing"]["track_width"])
        return {
            "track_width_mm": power_width,
            "neckdown_width_mm": default_width,
            "clearance_mm": float(routing["clearance"]),
            "via_diameter_mm": float(routing["via_size"]),
            "via_drill_mm": float(routing["via_drill"]),
        }
    if profile_name in {"usb_5v_distribution", "usb_vbus_raw_escape"}:
        manufacturing = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
        values = manufacturing["routing_rules"]["values"]
        geometry = {
            "track_width_mm": float(routing["track_width"]),
            "clearance_mm": float(values["clearance"]),
            "via_diameter_mm": float(values["via_size"]),
            "via_drill_mm": float(values["via_drill"]),
        }
        if profile_name in {"usb_5v_distribution", "usb_vbus_raw_escape"}:
            geometry["neckdown_width_mm"] = float(values["track_width"])
        return geometry
    if profile_name == "usb_device_diff_fcu":
        evidence = json.loads((ROOT / profile["impedance_evidence"]).read_text(encoding="utf-8"))
        manufacturing = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
        geometry = evidence["usb2_differential"]["pcb_geometry_mm"]
        values = {
            "track_width_mm": float(geometry["base_width"]),
            "neckdown_width_mm": float(manufacturing["routing_rules"]["values"]["track_width"]),
            "clearance_mm": float(routing["clearance"]),
            "pair_gap_mm": float(geometry["pair_gap"]),
            "via_diameter_mm": float(routing["via_size"]),
            "via_drill_mm": float(routing["via_drill"]),
        }
        if routing.get("layers") != ["F.Cu"]:
            raise SystemExit("Protected USB profile must remain F.Cu only")
        return values
    if profile_name in {"usb_device_escape_fcu", "usb_cc_local", "internal_ui_control", "target_control_local", "routing_corridor_adjustment", "ground_return_vias", "ground_plane_access", "j3_connector_esd_fanout", "usb_vbus_sense_dp_reroute", "buck_feedback_sense", "3v3_local_escape", "3v3_low_current_branch", "3v3_low_current_layer_transition"}:
        evidence = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
        values = evidence["routing_rules"]["values"]
        return {
            "track_width_mm": float(values["track_width"]),
            "clearance_mm": float(values["clearance"]),
            "via_diameter_mm": float(values["via_size"]),
            "via_drill_mm": float(values["via_drill"]),
        }
    evidence = json.loads((ROOT / profile["manufacturing_evidence"]).read_text(encoding="utf-8"))
    exception = evidence["fine_pitch_exceptions"]["usb_connector_fanout"]
    if (
        exception.get("reviewed") is not True
        or set(exception.get("nets", [])) != PROFILES[profile_name]["nets"]
    ):
        raise SystemExit("Reviewed USB connector fanout exception is incomplete")
    width = float(exception["track_width"])
    values = {
        "track_width_mm": width,
        "clearance_mm": float(exception["clearance"]),
        "via_diameter_mm": float(routing["via_size"]),
        "via_drill_mm": float(routing["via_drill"]),
    }
    if abs(float(routing["track_width"]) - width) > 1e-12:
        raise SystemExit("KRT fanout profile no longer matches reviewed manufacturing evidence")
    return values


def apply_connector_fanout_rule(output: Path, clearance_mm: float) -> None:
    path = output.with_suffix(".kicad_dru")
    text = path.read_text(encoding="utf-8") if path.is_file() else "(version 1)\n"
    marker = 'rule "Reviewed USB connector fanout clearance"'
    if marker in text:
        return
    condition = " || ".join(
        f"A.NetName == '{name}' || B.NetName == '{name}'" for name in sorted(PROFILES["usb_connector_esd_fanout"]["nets"])
    )
    rule = (
        "\n# Reviewed J1-to-U2 fine-pitch exception; these RAW nets terminate at U2.\n"
        f"(rule \"Reviewed USB connector fanout clearance\"\n"
        f"  (condition \"{condition}\")\n"
        f"  (constraint clearance (min {clearance_mm:.6f}mm)))\n"
    )
    path.write_text(text.rstrip() + "\n" + rule, encoding="utf-8", newline="\n")


def copy_candidate(source: Path, output: Path) -> None:
    if output == source:
        raise SystemExit("Trace plan output must be an isolated candidate")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    for suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
        companion = source.with_suffix(suffix)
        if companion.is_file():
            shutil.copy2(companion, output.with_suffix(suffix))
    for name in ("fp-lib-table", "sym-lib-table"):
        companion = source.parent / name
        if not companion.is_file():
            continue
        text = companion.read_text(encoding="utf-8")
        def rebase(match: re.Match[str]) -> str:
            target = (source.parent / match.group(1)).resolve()
            return "${KIPRJMOD}/" + Path(os.path.relpath(target, output.parent)).as_posix()
        (output.parent / name).write_text(
            re.sub(r"\$\{KIPRJMOD\}/([^\")]+)", rebase, text),
            encoding="utf-8", newline="\n",
        )


def applied_net_binding_report(inserted: list[tuple[object, str, str, int]]) -> dict[str, object]:
    """Verify that connectivity rebuilding did not bind planned copper to another net."""
    mismatches = []
    for board_item, expected_net, kind, index in inserted:
        actual_net = str(board_item.GetNetname())
        if actual_net == expected_net:
            continue
        position = board_item.GetPosition()
        mismatches.append({
            "kind": kind,
            "index": index,
            "expected_net": expected_net,
            "actual_net": actual_net,
            "position_mm": [
                round(float(position.x) / 1_000_000, 6),
                round(float(position.y) / 1_000_000, 6),
            ],
        })
    return {
        "success": not mismatches,
        "checked_item_count": len(inserted),
        "mismatches": mismatches,
        "limits": "Net binding is a fast post-connectivity guard; independent DRC remains mandatory.",
    }


def trace_stdout_index(receipt: dict[str, object], evidence: Path) -> dict[str, object]:
    audit = receipt.get("audit") if isinstance(receipt.get("audit"), dict) else {}
    connectivity = receipt.get("connectivity") if isinstance(receipt.get("connectivity"), dict) else None
    return {
        "success": receipt.get("success"),
        "accepted": receipt.get("accepted"),
        "critical_probe": receipt.get("critical_probe"),
        "route_area": receipt.get("route_area"),
        "profile": receipt.get("profile"),
        "nets": receipt.get("nets"),
        "segment_count": receipt.get("segment_count"),
        "via_count": receipt.get("via_count"),
        "removed_segment_count": receipt.get("removed_segment_count"),
        "removed_via_count": receipt.get("removed_via_count"),
        "candidate_sha256": receipt.get("candidate_sha256"),
        "geometry_preflight_pass": (receipt.get("geometry_preflight") or {}).get("success") if isinstance(receipt.get("geometry_preflight"), dict) else None,
        "net_binding_pass": (receipt.get("net_binding") or {}).get("success") if isinstance(receipt.get("net_binding"), dict) else None,
        "connectivity_pass": connectivity.get("success") if connectivity is not None else None,
        "audit": {
            "success": audit.get("success"),
            "unconnected_delta": audit.get("unconnected_delta"),
            "new_violation_count": audit.get("new_violation_count"),
        } if audit else None,
        "receipt": relative(evidence),
    }


def main() -> int:
    # Re-enter KiCad Python before parsing plans, copying candidates, or
    # creating artifact runs so the outer interpreter has no side effects.
    ensure_kicad_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--critical-probe", action="store_true")
    parser.add_argument("--probe-group")
    parser.add_argument("--probe-component")
    parser.add_argument("--placement-sha256")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.critical_probe:
        probe_group, probe_component = validate_critical_probe(args.probe_group, args.probe_component)
        if not isinstance(args.placement_sha256, str) or not re.fullmatch(r"[0-9A-Fa-f]{64}", args.placement_sha256):
            raise SystemExit("A critical trace probe requires a 64-character placement SHA-256")
        require_operation("candidate_route")
    else:
        if args.probe_group or args.probe_component or args.placement_sha256:
            raise SystemExit("Probe metadata requires --critical-probe")
        probe_group = probe_component = None
        require_operation("route")
    source = args.input.resolve()
    output = args.output.resolve()
    plan_path = args.plan.resolve()
    if not source.is_file():
        raise SystemExit(f"Trace plan input board is missing: {source}")
    plan = load_plan(plan_path)
    validate_acceptance_anchors(plan)
    before = sha256(source)
    if plan.get("board_sha256") != before:
        raise SystemExit("Trace plan board SHA-256 does not match its input candidate")
    profile_name = str(plan["profile"])
    reviewed = reviewed_geometry(profile_name)
    for key, value in reviewed.items():
        if abs(float(plan.get(key, -1)) - value) > 1e-12:
            raise SystemExit(f"Trace plan {key} must match reviewed profile/evidence")
    if profile_name == "usb_device_diff_fcu":
        validate_parallel_pairs(plan, reviewed)
    if profile_name in {"buck_sw_fcu_neckdown", "buck_power_local", "3v3_main_trunk", "usb_5v_distribution", "usb_vbus_raw_escape", "usb_device_diff_fcu", "usb_shield_bond"}:
        allowed_widths = {reviewed["track_width_mm"], reviewed["neckdown_width_mm"]}
        used_widths = {float(item.get("width_mm", reviewed["track_width_mm"])) for item in plan["segments"]}
        if used_widths != allowed_widths:
            raise SystemExit("Mixed-width plan must use both reviewed default and power widths")
    import pcbnew

    source_board = pcbnew.LoadBoard(str(source))
    planner = classify_board(
        source,
        sorted({str(item["net"]) for item in plan["segments"] if str(item["net"]) != "GND"}),
        ROOT / "config/routing-planner.json",
        ROOT / "config/kicad-routing-tools.json",
    )
    if planner.get("routing_order_blockers"):
        raise SystemExit(json.dumps({
            "success": False,
            "error": "routing_order_prerequisite_incomplete",
            "planner": planner,
        }, ensure_ascii=False, separators=(",", ":")))
    geometry_preflight = trace_plan_geometry_preflight(source_board, plan, reviewed)
    if geometry_preflight["success"] is not True:
        raise SystemExit(json.dumps({
            "success": False,
            "error": "trace_plan_geometry_preflight_failed",
            **geometry_preflight,
        }, ensure_ascii=False, separators=(",", ":")))
    # Reserve the immutable run before writing any candidate or companion
    # file. A duplicate run id must fail without leaving untracked design
    # output behind.
    run = create_run(
        "physical" if args.critical_probe else "routing",
        "kicad-trace-critical-probe" if args.critical_probe else "kicad-trace-plan",
        args.run_id,
    )
    geometry_preflight_path = run.evidence / "geometry-preflight.json"
    geometry_preflight_path.write_text(
        json.dumps(geometry_preflight, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    progress_path = run.tmp / "progress.json"

    def progress(step: str) -> None:
        progress_path.write_text(
            json.dumps({"schema": 1, "step": step}, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    progress("copy_candidate")
    copy_candidate(source, output)
    if profile_name == "usb_connector_esd_fanout":
        apply_connector_fanout_rule(output, reviewed["clearance_mm"])
    removed_segments: list[dict[str, object]] = []
    removed_vias: list[dict[str, object]] = []
    if plan.get("remove_segments") or plan.get("remove_vias") or plan.get("existing_return_vias"):
        progress("resolve_and_write_exact_removals")
        removed = remove_exact_copper_blocks(output, plan)
        removed_segments = removed["segments"]
        removed_vias = removed["vias"]
    progress("load_candidate")
    board = pcbnew.LoadBoard(str(output))
    allowed_names = PROFILES[profile_name]["nets"] | PROFILES[profile_name]["via_nets"]
    nets = {name: board.FindNet(name) for name in allowed_names}
    missing = sorted(name for name, net in nets.items() if net is None)
    if missing:
        raise SystemExit("Trace plan nets are missing from board: " + ", ".join(missing))
    progress("insert_copper")
    inserted: list[tuple[object, str, str, int]] = []
    for index, item in enumerate(plan["segments"]):
        track = pcbnew.PCB_TRACK(board)
        track.SetNet(nets[item["net"]])
        track.SetLayer(board.GetLayerID(item["layer"]))
        segment_width = float(item.get("width_mm", reviewed["track_width_mm"]))
        track.SetWidth(pcbnew.FromMM(segment_width))
        track.SetStart(pcbnew.VECTOR2I(*[pcbnew.FromMM(float(v)) for v in item["start_mm"]]))
        track.SetEnd(pcbnew.VECTOR2I(*[pcbnew.FromMM(float(v)) for v in item["end_mm"]]))
        board.Add(track)
        inserted.append((track, item["net"], "segment", index))
    for index, item in enumerate(plan["vias"]):
        via = pcbnew.PCB_VIA(board)
        via.SetNet(nets[item["net"]])
        via.SetPosition(pcbnew.VECTOR2I(*[pcbnew.FromMM(float(v)) for v in item["at_mm"]]))
        via.SetWidth(pcbnew.FromMM(reviewed["via_diameter_mm"]))
        via.SetDrill(pcbnew.FromMM(reviewed["via_drill_mm"]))
        via.SetLayerPair(board.GetLayerID("F.Cu"), board.GetLayerID("B.Cu"))
        board.Add(via)
        inserted.append((via, item["net"], "via", index))
    progress("build_connectivity")
    board.BuildConnectivity()
    net_binding = applied_net_binding_report(inserted)
    net_binding_path = run.evidence / "net-binding.json"
    net_binding_path.write_text(
        json.dumps(net_binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if net_binding["success"] is not True:
        payload = {
            "success": False,
            "error": "applied_copper_net_mismatch",
            **net_binding,
            "report": relative(net_binding_path),
        }
        finish_run(
            run,
            False,
            artifacts={"net_binding": relative(net_binding_path), "plan": relative(plan_path)},
            metrics={"net_binding_mismatches": len(net_binding["mismatches"])},
        )
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 1
    # Through vias cross the reviewed inner GND planes. Refill the copied
    # candidate so KiCad creates deterministic antipads instead of reporting
    # the stale pre-plan fill as a zero-clearance collision.
    progress("fill_zones")
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    progress("save_candidate")
    pcbnew.SaveBoard(str(output), board)
    progress("audit_candidate")

    selected_nets = sorted({
        item["net"]
        for item in [*plan["segments"], *plan["vias"], *plan.get("remove_segments", [])]
        + list(plan.get("remove_vias", []))
    })
    connectivity = None
    if plan.get("anchor_groups"):
        connectivity = run_anchor_connectivity(
            output, plan["anchor_groups"], run.evidence / "anchor-connectivity.json",
        )
    audit = run_candidate_audit(source, output, run.evidence / "candidate-audit.json")
    progress("write_receipt")
    accepted = bool(
        (connectivity is None or connectivity["success"] is True)
        and audit.get("success") is True
    )
    receipt = {
        "schema": 1, "kind": "kicad_trace_plan", "success": accepted,
        "accepted": accepted,
        "critical_probe": args.critical_probe,
        "probe_group": probe_group,
        "route_area": probe_group,
        "probe_component": probe_component,
        "placement_sha256": args.placement_sha256.upper() if args.placement_sha256 else None,
        "input": relative(source), "input_sha256": before,
        "candidate": relative(output), "candidate_sha256": sha256(output),
        "plan": relative(plan_path), "plan_sha256": sha256(plan_path), "profile": profile_name,
        "nets": selected_nets,
        "segment_count": len(plan["segments"]), "via_count": len(plan["vias"]),
        "removed_segment_count": len(removed_segments),
        "removed_via_count": len(removed_vias),
        "existing_return_via_count": len(plan.get("existing_return_vias", [])),
        "geometry_preflight": {
            "success": geometry_preflight["success"],
            "checked": geometry_preflight["checked"],
            "constraints_mm": geometry_preflight["constraints_mm"],
            "report": relative(geometry_preflight_path),
        },
        "net_binding": {
            "success": True,
            "checked_item_count": net_binding["checked_item_count"],
            "report": relative(net_binding_path),
        },
        "connectivity": connectivity,
        "audit": {
            "success": audit.get("success"),
            "unconnected_delta": audit.get("unconnected_delta"),
            "new_violation_count": len(audit.get("new_violations", [])),
            "new_violation_types": audit.get("new_violation_types", {}),
            "report": relative(run.evidence / "candidate-audit.json"),
        } if audit else None,
        **reviewed,
        "limits": "Explicit profile-bounded copper/vias only; independent DRC, connectivity, return-path, parity and physical review remain mandatory.",
    }
    evidence = run.evidence / "kicad-trace-plan.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, accepted, artifacts={"receipt": relative(evidence), "plan": relative(plan_path), "geometry_preflight": relative(geometry_preflight_path), "net_binding": relative(net_binding_path)}, metrics={"segments": len(plan["segments"]), "vias": len(plan["vias"]), "accepted": accepted})
    print(json.dumps(trace_stdout_index(receipt, evidence), separators=(",", ":")))
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
