#!/usr/bin/env python3
"""Render a bounded, SHA-bound PCB visual bundle for physical review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from stage_policy import current_stage

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python, kicad_cli


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = kicad_python()
KICAD_CLI = kicad_cli()
LOCAL_TRIGGERS = {"critical_net_changed", "new_or_moved_via", "clearance_delta", "route_marked_awkward", "reviewer_uncertain"}
CHECKPOINT_TRIGGERS = {"footprint_moved", "functional_block_completed", "routing_corridor_changed"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    ensure_host_kicad_python()


def render_3d(board: Path, output: Path, side: str) -> None:
    result = subprocess.run([
        str(KICAD_CLI), "pcb", "render", "--output", str(output),
        "--width", "1200", "--height", "1800", "--side", side,
        "--quality", "basic", "--background", "opaque", "--zoom", "0.75",
        str(board),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode or not output.is_file():
        raise SystemExit(json.dumps({
            "success": False, "error": "kicad_3d_render_failed", "side": side,
            "exit_code": result.returncode, "stderr": result.stderr[-500:],
        }, ensure_ascii=False, separators=(",", ":")))


def copper_preview(board, layer_name: str, output: Path, bounds_mm: tuple[float, float, float, float] | None = None) -> None:
    import pcbnew
    from PIL import Image, ImageDraw

    bbox = board.GetBoardEdgesBoundingBox()
    if bounds_mm:
        x0, y0, x1, y1 = (pcbnew.FromMM(value) for value in bounds_mm)
    else:
        x0, y0, x1, y1 = bbox.GetX(), bbox.GetY(), bbox.GetRight(), bbox.GetBottom()
    board_width = max(1, x1 - x0)
    board_height = max(1, y1 - y0)
    width = 1200
    height = max(800, min(1800, round(width * board_height / board_width)))
    margin = 45
    scale = min((width - 2 * margin) / board_width, (height - 2 * margin) / board_height)
    ox = (width - board_width * scale) / 2
    oy = (height - board_height * scale) / 2

    def point(position):
        return (ox + (position.x - x0) * scale, oy + (position.y - y0) * scale)

    def rect(item_bbox):
        left = ox + (item_bbox.GetX() - x0) * scale
        top = oy + (item_bbox.GetY() - y0) * scale
        right = ox + (item_bbox.GetRight() - x0) * scale
        bottom = oy + (item_bbox.GetBottom() - y0) * scale
        return (left, top, right, bottom)

    image = Image.new("RGB", (width, height), "#17191d")
    draw = ImageDraw.Draw(image)
    board_box = (ox, oy, ox + board_width * scale, oy + board_height * scale)
    draw.rectangle(board_box, fill="#0c2520", outline="#d0d4d8", width=3)
    layer_id = board.GetLayerID(layer_name)
    copper = "#e45f35" if layer_name == "F.Cu" else "#3b82d0"
    for item in board.GetTracks():
        if isinstance(item, pcbnew.PCB_VIA):
            continue
        if item.GetLayer() == layer_id:
            draw.line([point(item.GetStart()), point(item.GetEnd())], fill=copper,
                      width=max(2, round(item.GetWidth() * scale)))
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            if not pad.IsOnLayer(layer_id):
                continue
            box = rect(pad.GetBoundingBox())
            if pad.GetShape() in {pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_OVAL}:
                draw.ellipse(box, fill="#e2b84f", outline="#fff0a6", width=1)
            else:
                draw.rectangle(box, fill="#e2b84f", outline="#fff0a6", width=1)
    for item in board.GetTracks():
        if not isinstance(item, pcbnew.PCB_VIA):
            continue
        center = point(item.GetPosition())
        radius = item.GetWidth(layer_id) * scale / 2
        drill = item.GetDrillValue() * scale / 2
        draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius),
                     fill="#d0a33a", outline="#fff0a6")
        draw.ellipse((center[0] - drill, center[1] - drill, center[0] + drill, center[1] + drill), fill="#17191d")
    image.save(output)


def merge_bounds(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if left is None:
        return right
    if right is None:
        return left
    return (
        min(left[0], right[0]), min(left[1], right[1]),
        max(left[2], right[2]), max(left[3], right[3]),
    )


def changed_geometry_bounds(
    baseline: dict[tuple[object, ...], tuple[float, float, float, float]],
    candidate: dict[tuple[object, ...], tuple[float, float, float, float]],
    padding_mm: float,
) -> tuple[tuple[float, float, float, float] | None, dict[str, int]]:
    added = set(candidate) - set(baseline)
    removed = set(baseline) - set(candidate)
    bounds = None
    for key in added:
        bounds = merge_bounds(bounds, candidate[key])
    for key in removed:
        bounds = merge_bounds(bounds, baseline[key])
    if bounds is not None:
        bounds = (
            bounds[0] - padding_mm, bounds[1] - padding_mm,
            bounds[2] + padding_mm, bounds[3] + padding_mm,
        )
    return bounds, {"added": len(added), "removed": len(removed)}


def copper_geometry(board, layer_name: str) -> dict[tuple[object, ...], tuple[float, float, float, float]]:
    """Return compact geometry fingerprints and exact item bounds for one layer."""
    import pcbnew

    layer_id = board.GetLayerID(layer_name)
    result: dict[tuple[object, ...], tuple[float, float, float, float]] = {}
    for item in board.GetTracks():
        box = item.GetBoundingBox()
        bounds = tuple(pcbnew.ToMM(value) for value in (
            box.GetX(), box.GetY(), box.GetRight(), box.GetBottom(),
        ))
        if isinstance(item, pcbnew.PCB_VIA):
            position = item.GetPosition()
            key = (
                "via", item.GetNetname(), position.x, position.y,
                item.GetWidth(layer_id), item.GetDrillValue(),
            )
        else:
            if item.GetLayer() != layer_id:
                continue
            start = (item.GetStart().x, item.GetStart().y)
            end = (item.GetEnd().x, item.GetEnd().y)
            key = (
                type(item).__name__, item.GetNetname(), layer_name,
                min(start, end), max(start, end), item.GetWidth(),
                box.GetX(), box.GetY(), box.GetRight(), box.GetBottom(),
            )
        result[key] = bounds
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--tier", choices=("local", "checkpoint", "release"), default="release")
    parser.add_argument("--layer", choices=("F.Cu", "B.Cu"), default="F.Cu", help="Copper layer for a local review.")
    parser.add_argument("--bbox-mm", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"), help="Optional reviewed crop; unioned with actual changed-copper bounds when --baseline is used.")
    parser.add_argument("--baseline", type=Path, help="Baseline board used to derive the actual changed-copper crop for a local review.")
    parser.add_argument("--crop-padding-mm", type=float, default=2.0, help="Visual-only padding around actual changed copper (default: 2.0 mm).")
    parser.add_argument("--trigger", action="append", choices=sorted(LOCAL_TRIGGERS | CHECKPOINT_TRIGGERS), default=[])
    args = parser.parse_args()
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "routing", "release"}:
        raise SystemExit("PCB visual bundles are allowed only during physical, routing, or release stages")
    board_path = args.board.resolve()
    if not board_path.is_file():
        raise SystemExit(f"Board not found: {board_path}")
    if args.tier == "local":
        if args.bbox_mm and (args.bbox_mm[0] >= args.bbox_mm[2] or args.bbox_mm[1] >= args.bbox_mm[3]):
            raise SystemExit("A local visual review requires an ordered --bbox-mm X0 Y0 X1 Y1 crop")
        if not args.bbox_mm and not args.baseline:
            raise SystemExit("A local visual review requires --baseline, --bbox-mm, or both")
        if not 0 <= args.crop_padding_mm <= 20:
            raise SystemExit("--crop-padding-mm must be between 0 and 20 mm")
        if not LOCAL_TRIGGERS.intersection(args.trigger):
            raise SystemExit("A local visual review requires an explicit local --trigger")
    elif args.baseline:
        raise SystemExit("--baseline is only valid for a local visual review")
    if args.tier == "checkpoint" and not CHECKPOINT_TRIGGERS.intersection(args.trigger):
        raise SystemExit("A checkpoint visual review requires an explicit checkpoint --trigger")
    ensure_kicad_python()
    import pcbnew

    run = None
    if args.output_dir:
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        run = create_run(stage, "pcb-visual-bundle", args.run_id)
        output_dir = run.tmp
    board = pcbnew.LoadBoard(str(board_path))
    artifacts: dict[str, Path]
    crop_receipt = None
    if args.tier == "local":
        effective_bounds = tuple(args.bbox_mm) if args.bbox_mm else None
        diff_receipt = None
        if args.baseline:
            baseline_path = args.baseline.resolve()
            if not baseline_path.is_file():
                raise SystemExit(f"Visual baseline board not found: {baseline_path}")
            baseline_board = pcbnew.LoadBoard(str(baseline_path))
            diff_bounds, counts = changed_geometry_bounds(
                copper_geometry(baseline_board, args.layer),
                copper_geometry(board, args.layer),
                args.crop_padding_mm,
            )
            if diff_bounds is None:
                raise SystemExit(f"No changed {args.layer} copper exists between baseline and candidate")
            effective_bounds = merge_bounds(effective_bounds, diff_bounds)
            diff_receipt = {
                "baseline": relative(baseline_path),
                "baseline_sha256": sha256(baseline_path),
                "changed_items": counts,
                "padding_mm": args.crop_padding_mm,
                "changed_bbox_mm": [round(value, 6) for value in diff_bounds],
            }
        artifacts = {"local_copper": output_dir / "local-copper.png"}
        copper_preview(board, args.layer, artifacts["local_copper"], effective_bounds)
        crop_receipt = {
            "bbox_mm": [round(value, 6) for value in effective_bounds],
            "requested_bbox_mm": args.bbox_mm,
            "layer": args.layer,
            "source": "changed_copper" if args.baseline and not args.bbox_mm else "changed_copper_union_requested" if args.baseline else "requested",
            "diff": diff_receipt,
        }
    else:
        artifacts = {
            "front_copper": output_dir / "front-copper.png",
            "back_copper": output_dir / "back-copper.png",
        }
        copper_preview(board, "F.Cu", artifacts["front_copper"])
        copper_preview(board, "B.Cu", artifacts["back_copper"])
        if args.tier == "release":
            artifacts = {
                "top_3d": output_dir / "top-3d.png",
                "bottom_3d": output_dir / "bottom-3d.png",
                **artifacts,
            }
            render_3d(board_path, artifacts["top_3d"], "top")
            render_3d(board_path, artifacts["bottom_3d"], "bottom")
    images = {
        name: {"path": relative(path), "sha256": sha256(path), "bytes": path.stat().st_size}
        for name, path in artifacts.items()
    }
    receipt = {
        "schema": 1, "kind": "pcb_visual_bundle", "success": True,
        "tier": args.tier,
        "triggers": args.trigger,
        "board": relative(board_path), "board_sha256": sha256(board_path),
        "images": images,
        "crop": crop_receipt,
        "review_dimensions": [
            "overall density and functional partitioning", "connector and control accessibility",
            "critical-component proximity", "critical-route directness and congestion",
            "layer transitions and return-via placement", "silkscreen readability",
        ],
        "limits": "Images are bounded review aids. DRC, connectivity, electrical parity, return-path and manufacturing gates remain authoritative.",
    }
    receipt_path = (run.evidence if run else output_dir) / "visual-bundle.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if run:
        finish_run(run, True, artifacts={"receipt": relative(receipt_path), **{key: relative(value) for key, value in artifacts.items()}}, metrics={"board_sha256": receipt["board_sha256"], "image_count": len(images)})
    print(json.dumps({**receipt, "receipt": relative(receipt_path)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
