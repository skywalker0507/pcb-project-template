#!/usr/bin/env python3
"""Classify a bounded routing task before choosing KRT or an explicit trace plan."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def classify_metrics(
    metrics: dict[str, dict[str, object]], selected_nets: list[str], policy: dict[str, Any],
    connected: Callable[[str], bool],
) -> dict[str, Any]:
    selected = set(selected_nets)
    blockers: list[dict[str, str]] = []
    for scope in policy.get("ordered_scopes", []):
        groups = scope.get("groups", [])
        selected_indexes = [
            index for index, group in enumerate(groups)
            if selected & set(group.get("nets", []))
        ]
        if not selected_indexes:
            continue
        latest = max(selected_indexes)
        for group in groups[:latest]:
            for net in group.get("nets", []):
                if net in selected or connected(str(net)):
                    continue
                blockers.append({
                    "scope": str(scope.get("name")),
                    "required_group": str(group.get("name")),
                    "net": str(net),
                })
    power_profiles = policy.get("reviewed_multipoint_profiles", {})
    matched_power_profiles = {
        net: power_profiles[net]
        for net in selected_nets
        if net in power_profiles
    }
    local = policy.get("local_direct", {})
    maximum_endpoints = int(local.get("maximum_pad_endpoints", 2))
    maximum_span = float(local.get("maximum_span_mm", 5.0))
    require_empty = bool(local.get("require_no_existing_copper", True))
    local_direct = bool(selected_nets) and all(
        int(metrics[net]["pad_endpoint_count"]) <= maximum_endpoints
        and int(metrics[net]["pad_endpoint_count"]) == 2
        and float(metrics[net]["maximum_pad_span_mm"]) <= maximum_span
        and (not require_empty or int(metrics[net]["existing_copper_items"]) == 0)
        for net in selected_nets
    )
    if blockers:
        engine = "defer_for_routing_order"
        reason = "routing_order_prerequisite_incomplete"
    elif matched_power_profiles:
        engine = "explicit_trace_plan"
        reason = "reviewed_multipoint_decomposition_required"
    elif local_direct:
        engine = str(local.get("recommended_engine", "explicit_trace_plan"))
        reason = "short_two_endpoint_local_task"
    else:
        engine = "krt"
        reason = "global_or_multipoint_task"

    krt_gate_blockers: list[dict[str, str]] = []
    gate = policy.get("krt_prerequisite_gate", {})
    exempt_nets = {str(value) for value in gate.get("exempt_nets", [])}
    if engine == "krt" and not selected <= exempt_nets:
        for net in gate.get("required_connected_nets", []):
            name = str(net)
            if name in selected or connected(name):
                continue
            krt_gate_blockers.append({
                "gate": str(gate.get("name", "krt_prerequisite_gate")),
                "net": name,
                "reason": "critical_power_net_not_connected",
            })
    if krt_gate_blockers:
        engine = "defer_for_critical_power"
        reason = "critical_power_prerequisite_incomplete"
    return {
        "success": True,
        "selected_nets": selected_nets,
        "recommended_engine": engine,
        "krt_allowed": engine == "krt",
        "reason": reason,
        "routing_order_blockers": blockers,
        "krt_gate_blockers": krt_gate_blockers,
        "task_decomposition": matched_power_profiles,
        "metrics": metrics,
    }


def board_metrics(board, nets: list[str]) -> dict[str, dict[str, object]]:
    import pcbnew

    pads: dict[str, list[tuple[float, float]]] = {net: [] for net in nets}
    copper = {net: 0 for net in nets}
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            net = pad.GetNetname()
            if net not in pads:
                continue
            position = pad.GetPosition()
            point = (pcbnew.ToMM(position.x), pcbnew.ToMM(position.y))
            if point not in pads[net]:
                pads[net].append(point)
    for item in board.GetTracks():
        net = item.GetNetname()
        if net in copper:
            copper[net] += 1
    result = {}
    for net in nets:
        span = max((math.dist(left, right) for left in pads[net] for right in pads[net]), default=0.0)
        result[net] = {
            "pad_endpoint_count": len(pads[net]),
            "maximum_pad_span_mm": round(span, 6),
            "existing_copper_items": copper[net],
        }
    return result


def connectivity_probe(board: Path, krt_config: dict[str, Any]) -> Callable[[str], bool]:
    repository = (ROOT / str(krt_config["tool"]["repository"])).resolve()
    python = (ROOT / str(krt_config["tool"]["python"])).resolve()
    checker = repository / "py_router/check_connected.py"
    cache: dict[str, bool] = {}

    def connected(net: str) -> bool:
        if net not in cache:
            result = subprocess.run(
                [str(python), str(checker), str(board), "--nets", net, "--quiet"],
                cwd=repository, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            cache[net] = result.returncode == 0
        return cache[net]

    return connected


def classify_board(board_path: Path, nets: list[str], policy_path: Path, krt_config_path: Path) -> dict[str, Any]:
    import pcbnew

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    krt_config = json.loads(krt_config_path.read_text(encoding="utf-8"))
    board = pcbnew.LoadBoard(str(board_path))
    return classify_metrics(
        board_metrics(board, nets), nets, policy,
        connectivity_probe(board_path, krt_config),
    )


def main() -> int:
    ensure_kicad_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--nets", nargs="+", required=True)
    parser.add_argument("--policy", type=Path, default=ROOT / "config/routing-planner.json")
    parser.add_argument("--krt-config", type=Path, default=ROOT / "config/kicad-routing-tools.json")
    args = parser.parse_args()
    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    receipt = classify_board(board, list(dict.fromkeys(args.nets)), args.policy.resolve(), args.krt_config.resolve())
    print(compact(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
