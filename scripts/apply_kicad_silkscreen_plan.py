#!/usr/bin/env python3
"""Apply a bounded, SHA-guarded reference-field silkscreen plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from stage_policy import current_stage

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = kicad_python()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def configured_board() -> Path:
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    return (ROOT / str(flow["project"]["board"])).resolve()


def ensure_kicad_python() -> None:
    ensure_host_kicad_python()


def load_plan(path: Path) -> dict[str, object]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Silkscreen plan is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("kind") != "kicad_silkscreen_plan":
        raise SystemExit("Silkscreen plan must be schema-1 kicad_silkscreen_plan")
    fields = plan.get("reference_fields", [])
    texts = plan.get("board_texts", [])
    text_updates = plan.get("board_text_updates", [])
    if not isinstance(fields, list) or len(fields) > 100:
        raise SystemExit("A bounded silkscreen plan may contain at most 100 reference_fields")
    if not isinstance(texts, list) or len(texts) > 20:
        raise SystemExit("A bounded silkscreen plan may contain at most 20 board_texts")
    if not isinstance(text_updates, list) or len(text_updates) > 20:
        raise SystemExit("A bounded silkscreen plan may contain at most 20 board_text_updates")
    if not fields and not texts and not text_updates:
        raise SystemExit("A silkscreen plan requires at least one reference field, board text, or board text update")
    refs: list[str] = []
    for item in fields:
        if not isinstance(item, dict) or not isinstance(item.get("reference"), str) or not item["reference"]:
            raise SystemExit("Each reference_fields item requires a non-empty reference")
        if not isinstance(item.get("visible"), bool):
            raise SystemExit("Each reference_fields item requires a boolean visible value")
        has_x = "x_mm" in item
        has_y = "y_mm" in item
        if has_x != has_y:
            raise SystemExit("Reference field coordinates require both x_mm and y_mm")
        refs.append(item["reference"])
    if len(set(refs)) != len(refs):
        raise SystemExit("Silkscreen plan references must be unique")
    for item in texts:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise SystemExit("Each board_texts item requires non-empty text")
        for key in ("x_mm", "y_mm", "size_mm", "thickness_mm"):
            if key not in item:
                raise SystemExit(f"Each board_texts item requires {key}")
        if float(item["size_mm"]) <= 0 or float(item["thickness_mm"]) <= 0:
            raise SystemExit("Board text size and thickness must be positive")
    update_names: list[str] = []
    for item in text_updates:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise SystemExit("Each board_text_updates item requires non-empty text")
        if "x_mm" not in item or "y_mm" not in item:
            raise SystemExit("Each board_text_updates item requires x_mm and y_mm")
        update_names.append(item["text"])
    if len(set(update_names)) != len(update_names):
        raise SystemExit("Board text update selectors must be unique")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--input", type=Path, help="Use an isolated candidate as the silkscreen input.")
    parser.add_argument("--output", type=Path, help="Write an isolated candidate instead of modifying the active board.")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "release"}:
        raise SystemExit("Silkscreen plans are allowed only during physical or release cleanup")
    source_path = args.input.resolve() if args.input else configured_board()
    board_path = args.output.resolve() if args.output else source_path
    if not source_path.is_file():
        raise SystemExit(f"Silkscreen input is unavailable: {source_path}")
    if args.output and (board_path.exists() or board_path == source_path):
        raise SystemExit("Isolated silkscreen output must be a new path")
    if args.input and not args.output:
        raise SystemExit("An isolated silkscreen input requires --output")
    plan_path = args.plan.resolve()
    plan = load_plan(plan_path)
    before = sha256(source_path)
    if plan.get("board_sha256") != before:
        raise SystemExit("Silkscreen plan board SHA-256 does not match the active candidate")

    ensure_kicad_python()
    import pcbnew

    board = pcbnew.LoadBoard(str(source_path))
    by_ref = {str(item.GetReference()): item for item in board.GetFootprints()}
    fields = plan.get("reference_fields", [])
    texts = plan.get("board_texts", [])
    text_updates = plan.get("board_text_updates", [])
    missing = sorted({str(item["reference"]) for item in fields} - set(by_ref))
    if missing:
        raise SystemExit("Silkscreen plan references are missing from the board: " + ", ".join(missing))
    changes = []
    for item in fields:
        reference = str(item["reference"])
        field = by_ref[reference].Reference()
        old = field.GetTextPos()
        after_x = float(item.get("x_mm", old.x / 1_000_000))
        after_y = float(item.get("y_mm", old.y / 1_000_000))
        after_rotation = float(item.get("rotation_deg", field.GetTextAngleDegrees())) % 360
        visible = bool(item["visible"])
        changes.append({
            "reference": reference,
            "before": {
                "visible": field.IsVisible(),
                "x_mm": old.x / 1_000_000,
                "y_mm": old.y / 1_000_000,
                "rotation_deg": field.GetTextAngleDegrees(),
            },
            "after": {
                "visible": visible,
                "x_mm": after_x,
                "y_mm": after_y,
                "rotation_deg": after_rotation,
            },
            "reason": str(item.get("reason", "")),
        })
        field.SetVisible(visible)
        field.SetTextPos(pcbnew.VECTOR2I(pcbnew.FromMM(after_x), pcbnew.FromMM(after_y)))
        field.SetTextAngleDegrees(after_rotation)

    added_texts = []
    for item in texts:
        text_item = pcbnew.PCB_TEXT(board)
        text_item.SetText(str(item["text"]))
        text_item.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(float(item["x_mm"])), pcbnew.FromMM(float(item["y_mm"]))))
        text_item.SetTextAngleDegrees(float(item.get("rotation_deg", 0)) % 360)
        size = pcbnew.FromMM(float(item["size_mm"]))
        text_item.SetTextSize(pcbnew.VECTOR2I(size, size))
        text_item.SetTextThickness(pcbnew.FromMM(float(item["thickness_mm"])))
        text_item.SetLayer(pcbnew.F_SilkS)
        board.Add(text_item)
        added_texts.append({
            "text": str(item["text"]),
            "x_mm": float(item["x_mm"]),
            "y_mm": float(item["y_mm"]),
            "rotation_deg": float(item.get("rotation_deg", 0)) % 360,
            "size_mm": float(item["size_mm"]),
            "thickness_mm": float(item["thickness_mm"]),
            "reason": str(item.get("reason", "")),
        })

    existing_texts: dict[str, list[object]] = {}
    for drawing in board.GetDrawings():
        if isinstance(drawing, pcbnew.PCB_TEXT) and drawing.GetLayer() == pcbnew.F_SilkS:
            existing_texts.setdefault(str(drawing.GetText()), []).append(drawing)
    updated_texts = []
    for item in text_updates:
        selector = str(item["text"])
        matches = existing_texts.get(selector, [])
        if len(matches) != 1:
            raise SystemExit(f"Board text selector must match exactly once: {selector!r} matched {len(matches)}")
        text_item = matches[0]
        old = text_item.GetPosition()
        after_x = float(item["x_mm"])
        after_y = float(item["y_mm"])
        after_rotation = float(item.get("rotation_deg", text_item.GetTextAngleDegrees())) % 360
        text_item.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(after_x), pcbnew.FromMM(after_y)))
        text_item.SetTextAngleDegrees(after_rotation)
        updated_texts.append({
            "text": selector,
            "before": {"x_mm": old.x / 1_000_000, "y_mm": old.y / 1_000_000},
            "after": {"x_mm": after_x, "y_mm": after_y, "rotation_deg": after_rotation},
            "reason": str(item.get("reason", "")),
        })

    run = create_run(stage, "kicad-silkscreen", args.run_id)
    board_path.parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(board_path), board)
    if args.output:
        for suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
            companion = source_path.with_suffix(suffix)
            if companion.is_file():
                shutil.copy2(companion, board_path.with_suffix(suffix))
        for name in ("fp-lib-table", "sym-lib-table"):
            companion = source_path.parent / name
            if companion.is_file():
                destination = board_path.parent / name
                text = companion.read_text(encoding="utf-8")

                def rebase(match: re.Match[str]) -> str:
                    target = (source_path.parent / match.group(1)).resolve()
                    relative_target = Path(os.path.relpath(target, board_path.parent)).as_posix()
                    return "${KIPRJMOD}/" + relative_target

                text = re.sub(r"\$\{KIPRJMOD\}/([^\")]+)", rebase, text)
                destination.write_text(text, encoding="utf-8", newline="\n")
    receipt = {
        "schema": 1,
        "kind": "kicad_silkscreen",
        "success": True,
        "board": relative(board_path),
        "source": relative(source_path),
        "isolated_candidate": bool(args.output),
        "before_sha256": before,
        "board_sha256": sha256(board_path),
        "plan": relative(plan_path),
        "reference_fields": changes,
        "board_texts": added_texts,
        "board_text_updates": updated_texts,
        "limits": "Reference-field presentation only; footprint geometry, electrical parity, DRC and physical review remain mandatory.",
    }
    evidence = run.evidence / "kicad-silkscreen.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={"receipt": relative(evidence), "plan": relative(plan_path)}, metrics={"reference_fields": len(changes), "board_texts": len(added_texts), "board_text_updates": len(updated_texts)})
    print(json.dumps({**receipt, "receipt": relative(evidence)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
