#!/usr/bin/env python3
"""Check that reviewed pads/vias share one copper component on one net."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_locked_runtime() -> int | None:
    """Re-run under the same dependency-locked Python used by KRT."""
    config = json.loads((ROOT / "config/kicad-routing-tools.json").read_text(encoding="utf-8"))
    locked_python = (ROOT / config["tool"]["python"]).resolve()
    if not locked_python.is_file():
        raise SystemExit(f"Locked KRT Python is unavailable: {locked_python}")
    if os.path.normcase(str(Path(sys.executable).resolve())) == os.path.normcase(str(locked_python)):
        return None
    result = subprocess.run([str(locked_python), str(Path(__file__).resolve()), *sys.argv[1:]], check=False)
    return result.returncode


def load_locked_modules():
    config = json.loads((ROOT / "config/kicad-routing-tools.json").read_text(encoding="utf-8"))
    repository = (ROOT / config["tool"]["repository"]).resolve()
    expected = str(config["tool"]["commit"])
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    ).stdout.strip()
    if actual != expected:
        raise SystemExit("Locked KRT checkout is at the wrong commit")
    sys.path.insert(0, str(repository / "py_router"))
    from check_connected import check_net_connectivity  # type: ignore
    from geometry_utils import UnionFind  # type: ignore
    from kicad_parser import parse_kicad_pcb  # type: ignore
    return parse_kicad_pcb, check_net_connectivity, UnionFind


def split_pad(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise SystemExit("Pad anchors must use REF:PAD syntax")
    reference, number = value.rsplit(":", 1)
    if not reference or not number:
        raise SystemExit("Pad anchors must use non-empty REF:PAD syntax")
    return reference, number


def main() -> int:
    delegated = ensure_locked_runtime()
    if delegated is not None:
        return delegated
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--net", required=True)
    parser.add_argument("--pad", action="append", default=[])
    parser.add_argument("--via", action="append", nargs=2, type=float, metavar=("X", "Y"), default=[])
    parser.add_argument("--tolerance", type=float, default=0.02)
    args = parser.parse_args()
    if len(args.pad) + len(args.via) < 2:
        raise SystemExit("An anchor connectivity check requires at least two pad/via anchors")
    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    parse_board, check_net, union_find = load_locked_modules()
    pcb = parse_board(str(board))
    net_id = next((identifier for identifier, value in pcb.nets.items() if value.name == args.net), None)
    if net_id is None:
        raise SystemExit(f"Net not found: {args.net}")
    pads = list(pcb.pads_by_net.get(net_id, []))
    segments = [item for item in pcb.segments if item.net_id == net_id]
    vias = [item for item in pcb.vias if item.net_id == net_id]
    zones = [item for item in pcb.zones if item.net_id == net_id]
    result = check_net(
        net_id, segments, vias, pads, zones, args.tolerance,
        return_graph=True, pcb_data=pcb,
    )
    graph = result.get("graph") or {}
    uf = union_find()
    for left, right in graph.get("edges") or []:
        uf.union(left, right)
    anchor_roots: list[int] = []
    pad_matches: dict[str, int] = defaultdict(int)
    for value in args.pad:
        reference, number = split_pad(value)
        indexes = [
            index for index, pad in enumerate(pads)
            if pad.component_ref == reference and pad.pad_number == number
        ]
        if not indexes:
            raise SystemExit(f"Reviewed pad anchor is missing from net {args.net}: {value}")
        pad_matches[value] = len(indexes)
        for index in indexes:
            anchor_roots.append(uf.find(graph["pad_index_repr"][index]))
    via_matches: list[dict[str, float]] = []
    for x, y in args.via:
        indexes = [
            index for index, via in enumerate(vias)
            if abs(via.x - x) <= args.tolerance and abs(via.y - y) <= args.tolerance
        ]
        if len(indexes) != 1:
            raise SystemExit(f"Reviewed via anchor must match exactly once on {args.net}: {x},{y}")
        via_matches.append({"x_mm": x, "y_mm": y})
        anchor_roots.append(uf.find(graph["via_index_repr"][indexes[0]]))
    connected = len(set(anchor_roots)) == 1
    receipt = {
        "schema": 1,
        "kind": "kicad_anchor_connectivity",
        "success": connected,
        "board": str(board),
        "net": args.net,
        "pad_anchors": dict(sorted(pad_matches.items())),
        "via_anchors": via_matches,
        "anchor_count": len(anchor_roots),
        "component_count": len(set(anchor_roots)),
        "limits": "Reviewed local-anchor connectivity only; this does not claim whole-net or release completeness.",
    }
    print(compact(receipt))
    return 0 if connected else 2


if __name__ == "__main__":
    raise SystemExit(main())
