#!/usr/bin/env python3
"""Apply a bounded, SHA-guarded footprint placement plan to the KiCad candidate."""

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
        raise SystemExit(f"Placement plan is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("schema") != 1 or plan.get("kind") != "kicad_placement_plan":
        raise SystemExit("Placement plan must be schema-1 kicad_placement_plan")
    placements = plan.get("placements", [])
    translations = plan.get("translations", [])
    lock_references = plan.get("lock_references", [])
    if not isinstance(placements, list) or len(placements) > 20:
        raise SystemExit("A bounded placement plan may contain at most 20 placements")
    if not isinstance(translations, list) or len(translations) > 12:
        raise SystemExit("A bounded placement plan may contain at most 12 translations")
    if not isinstance(lock_references, list) or len(lock_references) > 100:
        raise SystemExit("A bounded placement plan may contain at most 100 lock_references")
    translated_refs = []
    for item in translations:
        if not isinstance(item, dict) or not isinstance(item.get("references"), list):
            raise SystemExit("Every placement translation requires a references list")
        refs = item["references"]
        if not 1 <= len(refs) <= 40 or any(not isinstance(ref, str) or not ref for ref in refs):
            raise SystemExit("Every placement translation requires 1-40 non-empty references")
        float(item.get("dx_mm", 0)); float(item.get("dy_mm", 0))
        translated_refs.extend(refs)
    if len(translated_refs) > 100 or len(set(translated_refs)) != len(translated_refs):
        raise SystemExit("Translated references must be unique and total at most 100")
    if not placements and not translations and not lock_references:
        raise SystemExit("A placement plan requires placements, translations or lock_references")
    if any(not isinstance(ref, str) or not ref for ref in lock_references):
        raise SystemExit("lock_references must contain non-empty strings")
    if len(set(lock_references)) != len(lock_references):
        raise SystemExit("lock_references must be unique")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--input", type=Path, help="Use an isolated candidate as the placement input.")
    parser.add_argument(
        "--output", type=Path,
        help="Write an isolated candidate instead of modifying the configured board.",
    )
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if current_stage().get("stage") != "physical":
        raise SystemExit("Placement plans are allowed only during the physical stage")
    # Re-enter under KiCad's Python before creating files or artifact runs.
    # The Windows launcher executes this script a second time; any side effect
    # before this point would be duplicated by the child process.
    ensure_kicad_python()
    source_path = args.input.resolve() if args.input else configured_board()
    board_path = args.output.resolve() if args.output else source_path
    plan_path = args.plan.resolve()
    plan = load_plan(plan_path)
    before = sha256(source_path)
    if plan.get("board_sha256") != before:
        raise SystemExit("Placement plan board SHA-256 does not match the active candidate")
    # Reserve the artifact run before creating an isolated output. This makes
    # the natural build/<stage>/tmp/<run-id>/candidate.kicad_pcb layout safe
    # and prevents an orphan candidate when a duplicate run id is rejected.
    run = create_run("physical", "kicad-placement", args.run_id)
    if args.output:
        if board_path == source_path:
            raise SystemExit("Isolated placement output must differ from the configured board")
        board_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, board_path)
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

    import pcbnew

    board = pcbnew.LoadBoard(str(board_path))
    by_ref = {str(item.GetReference()): item for item in board.GetFootprints()}
    placements = plan.get("placements", [])
    translations = plan.get("translations", [])
    lock_references = plan.get("lock_references", [])
    refs = [str(item.get("reference", "")) for item in placements if isinstance(item, dict)]
    if len(refs) != len(placements) or len(set(refs)) != len(refs):
        raise SystemExit("Placement references must be non-empty and unique")
    translated_refs = [ref for item in translations for ref in item["references"]]
    if set(refs) & set(translated_refs):
        raise SystemExit("A reference may not appear in both placements and translations")
    missing = sorted((set(refs) | set(translated_refs)) - set(by_ref))
    if missing:
        raise SystemExit("Placement plan references are missing from the board: " + ", ".join(missing))
    missing_locks = sorted(set(lock_references) - set(by_ref))
    if missing_locks:
        raise SystemExit("Placement lock references are missing from the board: " + ", ".join(missing_locks))
    changes = []
    for item in translations:
        dx = float(item.get("dx_mm", 0))
        dy = float(item.get("dy_mm", 0))
        reason = str(item.get("reason", ""))
        for reference in item["references"]:
            footprint = by_ref[reference]
            old = footprint.GetPosition()
            x = old.x / 1_000_000 + dx
            y = old.y / 1_000_000 + dy
            changes.append({
                "reference": reference,
                "before": {"x_mm": old.x / 1_000_000, "y_mm": old.y / 1_000_000,
                           "rotation_deg": footprint.GetOrientationDegrees(), "locked": footprint.IsLocked()},
                "after": {"x_mm": x, "y_mm": y, "rotation_deg": footprint.GetOrientationDegrees(), "locked": False},
                "reason": reason,
            })
            footprint.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
            footprint.SetLocked(False)
    for item in placements:
        x = float(item["x_mm"])
        y = float(item["y_mm"])
        rotation = float(item.get("rotation_deg", 0)) % 360
        locked = bool(item.get("locked", False))
        footprint = by_ref[str(item["reference"])]
        old = footprint.GetPosition()
        changes.append({
            "reference": str(item["reference"]),
            "before": {"x_mm": old.x / 1_000_000, "y_mm": old.y / 1_000_000,
                       "rotation_deg": footprint.GetOrientationDegrees(), "locked": footprint.IsLocked()},
            "after": {"x_mm": x, "y_mm": y, "rotation_deg": rotation, "locked": locked},
            "reason": str(item.get("reason", "")),
        })
        footprint.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        footprint.SetOrientationDegrees(rotation)
        footprint.SetLocked(locked)
    locked_changes = []
    for reference in lock_references:
        footprint = by_ref[reference]
        before_locked = footprint.IsLocked()
        footprint.SetLocked(True)
        locked_changes.append({"reference": reference, "before_locked": before_locked, "after_locked": True})
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(board_path), board)
    receipt = {
        "schema": 1, "kind": "kicad_placement", "success": True,
        "board": relative(board_path), "before_sha256": before,
        "source": relative(source_path), "isolated_candidate": bool(args.output),
        "board_sha256": sha256(board_path), "plan": relative(plan_path),
        "placements": changes,
        "locked_references": locked_changes,
        "limits": "Placement geometry only; final electrical parity, DRC, return-path and physical review remain mandatory.",
    }
    evidence = run.evidence / "kicad-placement.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={"receipt": relative(evidence), "plan": relative(plan_path)}, metrics={"placements": len(changes), "locked_references": len(locked_changes)})
    print(json.dumps({
        "schema": 1,
        "kind": "kicad_placement",
        "success": True,
        "board": relative(board_path),
        "board_sha256": receipt["board_sha256"],
        "isolated_candidate": bool(args.output),
        "moved_components": len(changes),
        "locked_references": len(locked_changes),
        "receipt": relative(evidence),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
