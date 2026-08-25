#!/usr/bin/env python3
"""Create an isolated KRT input with copper removed only for reviewed nets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from krt_flow import copy_project_companions, net_names
from restore_krt_outside_scope import copper_blocks
from stage_policy import current_stage, require_operation


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def block_points(block: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for match in re.finditer(
        r"\((?:start|mid|end|at)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)",
        block,
    ):
        points.append((float(match.group(1)), float(match.group(2))))
    return points


def in_bbox(block: str, bbox: tuple[float, float, float, float] | None) -> bool:
    if bbox is None:
        return True
    x1, y1, x2, y2 = bbox
    return any(x1 <= x <= x2 and y1 <= y <= y2 for x, y in block_points(block))


def strip_selected_copper(
    text: str,
    selected: set[str],
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[str, dict[str, int]]:
    spans: list[tuple[int, int]] = []
    removed: dict[str, int] = {}
    for start, end, net, _ in copper_blocks(text):
        if net in selected and in_bbox(_, bbox):
            spans.append((start, end))
            removed[net] = removed.get(net, 0) + 1
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), dict(sorted(removed.items()))


def validate_critical_probe(group: str | None, component: str | None) -> tuple[str, str]:
    policy = json.loads((ROOT / "config/placement-routability.json").read_text(encoding="utf-8"))
    checkpoint = policy.get("checkpoint") or {}
    groups = [str(value) for value in checkpoint.get("critical_probe_groups", [])]
    if group not in groups:
        raise SystemExit("Critical reroute probe group is not reviewed")
    expected = [
        str(value)
        for value in (checkpoint.get("critical_probe_components") or {}).get(group, [])
    ]
    if expected and component not in expected:
        raise SystemExit("Critical reroute probe component is not reviewed for its group")
    return str(group), str(component)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--nets", nargs="+", required=True)
    parser.add_argument("--critical-probe", action="store_true")
    parser.add_argument("--probe-group")
    parser.add_argument("--probe-component")
    parser.add_argument("--placement-sha256")
    parser.add_argument(
        "--bbox", nargs=4, type=float, metavar=("X1", "Y1", "X2", "Y2"),
        help="Remove selected-net copper only when one of its geometry points is inside this board-coordinate box.",
    )
    args = parser.parse_args()

    if args.critical_probe:
        probe_group, probe_component = validate_critical_probe(
            args.probe_group, args.probe_component
        )
        if not isinstance(args.placement_sha256, str) or not re.fullmatch(
            r"[0-9A-Fa-f]{64}", args.placement_sha256
        ):
            raise SystemExit("A critical reroute probe requires a 64-character placement SHA-256")
        require_operation("candidate_route")
    else:
        if args.probe_group or args.probe_component or args.placement_sha256:
            raise SystemExit("Probe metadata requires --critical-probe")
        if current_stage().get("stage") != "routing":
            raise SystemExit("KRT reroute inputs are allowed only during the routing stage")
        probe_group = probe_component = None
    source = args.input.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise SystemExit(f"Input board not found: {source}")
    if output == source:
        raise SystemExit("Reroute input must be a separate isolated board")
    if output.exists():
        raise SystemExit(f"Refusing to overwrite reroute input: {output}")
    before = sha256(source)
    if before != args.expected_sha256.upper():
        raise SystemExit("Input board SHA-256 does not match --expected-sha256")
    if len(args.nets) > 32 or len(set(args.nets)) != len(args.nets):
        raise SystemExit("Reroute scope requires 1-32 unique explicit net names")
    if any(not net or any(char in net for char in "*?[") for net in args.nets):
        raise SystemExit("Reroute scope accepts explicit net names only")
    available = set(net_names(source))
    missing = sorted(set(args.nets) - available)
    if missing:
        raise SystemExit("Reroute nets are absent from the board: " + ", ".join(missing))

    bbox = tuple(args.bbox) if args.bbox else None
    if bbox and not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        raise SystemExit("Reroute bbox requires X1 < X2 and Y1 < Y2")
    stripped, removed = strip_selected_copper(
        source.read_text(encoding="utf-8"), set(args.nets), bbox
    )
    if not removed:
        raise SystemExit("Reroute scope selected no existing copper")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(stripped, encoding="utf-8", newline="\n")
    source_project = source.with_suffix(".kicad_pro")
    output_project = output.with_suffix(".kicad_pro")
    if source_project.is_file():
        shutil.copy2(source_project, output_project)
        copy_project_companions(source_project, output_project)

    receipt = {
        "schema": 1,
        "kind": "krt_reroute_input",
        "success": True,
        "critical_probe": args.critical_probe,
        "probe_group": probe_group,
        "probe_component": probe_component,
        "placement_sha256": (
            args.placement_sha256.upper() if args.placement_sha256 else None
        ),
        "source": str(source.relative_to(ROOT)),
        "source_sha256": before,
        "output": str(output.relative_to(ROOT)),
        "output_sha256": sha256(output),
        "selected_nets": args.nets,
        "bbox_mm": list(bbox) if bbox else None,
        "removed_copper_items_by_net": removed,
        "removed_copper_item_count": sum(removed.values()),
        "invariant": "Only selected-net copper is removed; components, pads, nets, zones, outline and unselected copper are preserved.",
    }
    receipt_path = output.parent / "reroute-input.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**receipt, "receipt": str(receipt_path.relative_to(ROOT))}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
