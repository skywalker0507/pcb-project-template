#!/usr/bin/env python3
"""Rebase an unrouted physical board onto a newer passing electrical freeze."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path
from artifact_store import create_run, finish_run, relative
from stage_policy import current_stage

ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()

def ensure_kicad_python() -> None:
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(KICAD_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))

def fpid(footprint) -> str:
    value = footprint.GetFPID()
    return f"{value.GetLibNickname()}:{value.GetLibItemName()}"

def pads_by_number(footprint) -> dict[str, list[object]]:
    result: dict[str, list[object]] = {}
    for pad in footprint.Pads():
        result.setdefault(str(pad.GetNumber()), []).append(pad)
    return result

def pad_geometry(pad) -> tuple[object, ...]:
    size, drill = pad.GetSize(), pad.GetDrillSize()
    return str(pad.GetAttribute()), str(pad.GetShape()), size.x, size.y, drill.x, drill.y

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical", type=Path, required=True)
    parser.add_argument("--electrical-freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if current_stage().get("stage") != "physical":
        raise SystemExit("Electrical rebase is allowed only during the physical stage")
    physical, freeze_path, output = args.physical.resolve(), args.electrical_freeze.resolve(), args.output.resolve()
    if not physical.is_file() or output == physical:
        raise SystemExit("Rebase requires an existing physical input and a separate output")
    try:
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Electrical freeze receipt is unavailable or invalid: {exc}") from exc
    frozen = (ROOT / str(freeze.get("frozen_board", ""))).resolve()
    if freeze.get("kind") != "electrical_freeze" or freeze.get("freeze_pass") is not True or not frozen.is_file() or sha256(frozen) != freeze.get("frozen_board_sha256"):
        raise SystemExit("Rebase requires a passing, hash-matching electrical freeze")
    output.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
        companion = physical.with_suffix(suffix)
        if companion.is_file():
            shutil.copy2(companion, output.with_suffix(suffix))
    for name in ("fp-lib-table", "sym-lib-table"):
        companion = physical.parent / name
        if companion.is_file():
            text = companion.read_text(encoding="utf-8")
            def rebase(match: re.Match[str]) -> str:
                target = (physical.parent / match.group(1)).resolve()
                return "${KIPRJMOD}/" + Path(os.path.relpath(target, output.parent)).as_posix()
            (output.parent / name).write_text(re.sub(r'\$\{KIPRJMOD\}/([^\")]+)', rebase, text), encoding="utf-8", newline="\n")
    ensure_kicad_python()
    import pcbnew
    board, authority = pcbnew.LoadBoard(str(physical)), pcbnew.LoadBoard(str(frozen))
    if len(board.GetTracks()):
        raise SystemExit("Electrical rebase refuses a board with tracks or vias")
    physical_refs = {str(item.GetReference()): item for item in board.GetFootprints()}
    frozen_refs = {str(item.GetReference()): item for item in authority.GetFootprints()}
    if set(physical_refs) != set(frozen_refs):
        raise SystemExit("Physical and frozen boards have different reference sets")
    required_nets = {str(pad.GetNetname()) for fp in authority.GetFootprints() for pad in fp.Pads() if str(pad.GetNetname())}
    nets = {name: board.FindNet(name) for name in required_nets}
    for name in sorted(required_nets):
        if nets[name] is None:
            nets[name] = pcbnew.NETINFO_ITEM(board, name)
            board.Add(nets[name])
    changes = []
    for reference in sorted(physical_refs):
        target, source = physical_refs[reference], frozen_refs[reference]
        target_pads, source_pads = pads_by_number(target), pads_by_number(source)
        if fpid(target) != fpid(source) or set(target_pads) != set(source_pads):
            raise SystemExit(f"Footprint or pad identity changed at {reference}")
        for number in sorted(target_pads):
            left, right = target_pads[number], source_pads[number]
            if len(left) != len(right) or sorted(map(pad_geometry, left)) != sorted(map(pad_geometry, right)):
                raise SystemExit(f"Pad geometry changed at {reference}:{number}")
            names = {str(pad.GetNetname()) for pad in right}
            if len(names) != 1:
                raise SystemExit(f"Frozen duplicate pads disagree at {reference}:{number}")
            new_name = next(iter(names))
            for pad in left:
                old_name = str(pad.GetNetname())
                if old_name != new_name:
                    pad.SetNet(nets[new_name] if new_name else None)
                    changes.append({"pad": f"{reference}:{number}", "before": old_name or None, "after": new_name or None})
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(output), board)
    run = create_run("physical", "kicad-electrical-rebase", args.run_id)
    receipt = {"schema": 1, "kind": "kicad_electrical_rebase", "success": True,
        "physical_source": relative(physical), "physical_source_sha256": sha256(physical),
        "electrical_freeze": relative(freeze_path), "freeze_id": freeze.get("freeze_id"),
        "frozen_board_sha256": freeze.get("frozen_board_sha256"), "candidate": relative(output),
        "candidate_sha256": sha256(output), "footprint_count": len(physical_refs),
        "changed_pads": changes,
        "limits": "Unrouted-board pad-net rebase only; parity, DRC, placement semantics, visual review and promotion remain mandatory."}
    evidence = run.evidence / "kicad-electrical-rebase.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(run, True, artifacts={"receipt": relative(evidence), "candidate": relative(output)}, metrics={"changed_pads": len(changes)})
    print(json.dumps({**receipt, "receipt": relative(evidence)}, separators=(",", ":")))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
