#!/usr/bin/env python3
"""Export a compact placement/net context instead of sending a whole PCB to an LLM."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")


def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        command = [str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
        raise SystemExit(subprocess.call(command))


def mm(value: int) -> float:
    return round(value / 1_000_000, 4)


def fallback_classify_net(name: str, endpoint_count: int) -> str:
    lowered = name.lower()
    power_tokens = ("gnd", "vcc", "vdd", "3v3", "5v", "power", "supply")
    if endpoint_count > 10 or lowered in {"lv", "hv", "pos", "neg"} or any(
        token in lowered for token in power_tokens
    ):
        return "distribution_or_power"
    if endpoint_count > 4:
        return "multi_drop_signal"
    if endpoint_count == 2:
        return "point_to_point"
    return "small_fanout"


def match_net_policy(name: str, endpoint_count: int, rules: list[dict[str, object]]) -> dict[str, object]:
    for rule in rules:
        if re.search(str(rule["pattern"]), name, re.IGNORECASE):
            return {
                "classification": rule["classification"],
                "review": rule.get("review", []),
            }
    return {
        "classification": fallback_classify_net(name, endpoint_count),
        "review": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--board",
        type=Path,
        default=ROOT / "elec/layout/default/default.kicad_pcb",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build/delegated/pcb-context.json",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "config/net-review-policy.json",
    )
    args = parser.parse_args()

    import pcbnew

    board_path = args.board.resolve()
    board = pcbnew.LoadBoard(str(board_path))
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    policy_rules = policy.get("rules", [])
    bbox = board.GetBoardEdgesBoundingBox()
    components = []
    net_endpoints: dict[str, list[dict[str, object]]] = defaultdict(list)
    group_positions: dict[str, list[tuple[float, float, str]]] = defaultdict(list)

    for footprint in sorted(board.GetFootprints(), key=lambda item: item.GetReference()):
        fields = {
            field.GetName(): field.GetText().strip()
            for field in footprint.GetFields()
        }
        position = footprint.GetPosition()
        x, y = mm(position.x), mm(position.y)
        address = fields.get("atopile_address", "")
        group = address.split(".", 1)[0] if address else "mechanical_or_external"
        group_positions[group].append((x, y, footprint.GetReference()))
        fpid = footprint.GetFPID()
        pads = []
        for pad in sorted(footprint.Pads(), key=lambda item: item.GetNumber()):
            pad_position = pad.GetPosition()
            endpoint = {
                "ref": footprint.GetReference(),
                "pad": pad.GetNumber(),
                "x_mm": mm(pad_position.x),
                "y_mm": mm(pad_position.y),
            }
            net = pad.GetNetname()
            pads.append({"number": pad.GetNumber(), "net": net})
            if net:
                net_endpoints[net].append(endpoint)
        components.append(
            {
                "ref": footprint.GetReference(),
                "value": footprint.GetValue(),
                "footprint": f"{fpid.GetLibNickname()}:{fpid.GetLibItemName()}",
                "x_mm": x,
                "y_mm": y,
                "rotation_deg": round(footprint.GetOrientationDegrees(), 3),
                "side": board.GetLayerName(footprint.GetLayer()),
                "atopile_address": address,
                "group": group,
                "pads": pads,
            }
        )

    nets = []
    for name, endpoints in sorted(net_endpoints.items()):
        xs = [float(item["x_mm"]) for item in endpoints]
        ys = [float(item["y_mm"]) for item in endpoints]
        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys)) if len(endpoints) > 1 else 0.0
        net_policy = match_net_policy(name, len(endpoints), policy_rules)
        nets.append(
            {
                "name": name,
                "endpoint_count": len(endpoints),
                "classification": net_policy["classification"],
                "review": net_policy["review"],
                "hpwl_mm": round(hpwl, 4),
                "endpoints": endpoints,
            }
        )

    groups = []
    for name, positions in sorted(group_positions.items()):
        xs = [item[0] for item in positions]
        ys = [item[1] for item in positions]
        groups.append(
            {
                "name": name,
                "refs": sorted(item[2] for item in positions),
                "origin_bbox_mm": {
                    "x_min": min(xs),
                    "y_min": min(ys),
                    "x_max": max(xs),
                    "y_max": max(ys),
                },
            }
        )

    context = {
        "schema": 1,
        "board": str(board_path),
        "board_sha256": hashlib.sha256(board_path.read_bytes()).hexdigest().upper(),
        "net_review_policy": str(args.policy.resolve()),
        "outline_mm": {
            "x": mm(bbox.GetX()),
            "y": mm(bbox.GetY()),
            "width": mm(bbox.GetWidth()),
            "height": mm(bbox.GetHeight()),
        },
        "component_count": len(components),
        "net_count": len(nets),
        "components": components,
        "functional_groups": groups,
        "nets": nets,
        "highest_hpwl_nets": [
            {
                "name": item["name"],
                "hpwl_mm": item["hpwl_mm"],
                "endpoint_count": item["endpoint_count"],
                "classification": item["classification"],
            }
            for item in sorted(nets, key=lambda item: float(item["hpwl_mm"]), reverse=True)[:20]
        ],
        "highest_hpwl_signal_nets": [
            {
                "name": item["name"],
                "hpwl_mm": item["hpwl_mm"],
                "endpoint_count": item["endpoint_count"],
                "classification": item["classification"],
            }
            for item in sorted(nets, key=lambda item: float(item["hpwl_mm"]), reverse=True)
            if item["classification"] != "distribution_or_power"
        ][:20],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "bytes": args.output.stat().st_size,
                "board_sha256": context["board_sha256"],
                "components": len(components),
                "nets": len(nets),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    ensure_kicad_python()
    sys.exit(main())
