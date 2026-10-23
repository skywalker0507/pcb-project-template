#!/usr/bin/env python3
"""Apply a SHA-guarded KiCad layer and manufacturer stackup plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from artifact_store import create_run, finish_run, relative
from pcb_policy import configured_board
from stage_policy import current_stage

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = kicad_python()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def ensure_kicad_python() -> None:
    ensure_host_kicad_python()


def sexpr_block(text: str, marker: str) -> tuple[int, int, str]:
    """Return one balanced S-expression without parsing the rest of the PCB."""
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"KiCad board is missing {marker}")
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return start, index + 1, text[start:index + 1]
    raise SystemExit(f"KiCad board contains an unterminated {marker}")


def number(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) <= 0:
        raise SystemExit(f"Stackup numeric value must be positive: {value!r}")
    return format(float(value), ".10g")


def quoted(value: Any) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in '"\\\r\n'):
        raise SystemExit(f"Stackup text value is unsafe or empty: {value!r}")
    return f'"{value}"'


def render_dielectric(group: dict[str, Any]) -> list[str]:
    layer_name = quoted(group.get("name"))
    layer_type = quoted(group.get("type"))
    sublayers = group.get("sublayers")
    if not isinstance(sublayers, list) or not sublayers:
        raise SystemExit(f"Stackup dielectric group has no sublayers: {group.get('name')!r}")
    lines = [f'\t\t\t(layer {layer_name}', f'\t\t\t\t(type {layer_type})']
    for index, sublayer in enumerate(sublayers):
        if not isinstance(sublayer, dict):
            raise SystemExit("Stackup dielectric sublayer must be an object")
        if index:
            lines.append("\t\t\t\taddsublayer")
        lines.extend([
            f'\t\t\t\t(thickness {number(sublayer.get("thickness_mm"))} locked)',
            f'\t\t\t\t(material {quoted(sublayer.get("material"))})',
            f'\t\t\t\t(epsilon_r {number(sublayer.get("epsilon_r"))})',
            f'\t\t\t\t(loss_tangent {number(sublayer.get("loss_tangent"))})',
        ])
    lines.append("\t\t\t)")
    return lines


def render_manufacturer_stackup(
    evidence: dict[str, Any], manufacturing_profile: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    stackup = evidence.get("stackup")
    if not isinstance(stackup, dict) or stackup.get("template") != "JLC04161H-3313A":
        raise SystemExit("Manufacturer evidence must select JLC04161H-3313A")
    physical = stackup.get("physical_layers")
    expected_order = [
        "F.Cu", "dielectric 1.1", "dielectric 1.2", "In1.Cu", "dielectric 2",
        "In2.Cu", "dielectric 3.1", "dielectric 3.2", "B.Cu",
    ]
    if not isinstance(physical, list) or [item.get("name") for item in physical] != expected_order:
        raise SystemExit("Manufacturer evidence physical-layer order is missing or unexpected")
    copper = {
        item["name"]: item["thickness_mm"]
        for item in physical
        if isinstance(item, dict) and item.get("kind") == "copper"
    }
    if list(copper) != ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]:
        raise SystemExit("Manufacturer evidence copper-layer order is invalid")
    groups = stackup.get("kicad_dielectric_groups")
    if not isinstance(groups, list) or [item.get("name") for item in groups] != [
        "dielectric 1", "dielectric 2", "dielectric 3"
    ]:
        raise SystemExit("Manufacturer evidence KiCad dielectric groups are invalid")
    order_profile = manufacturing_profile.get("order_profile")
    fabrication = order_profile.get("fabrication") if isinstance(order_profile, dict) else None
    if not isinstance(order_profile, dict) or order_profile.get("status") != "reviewed" or not isinstance(fabrication, dict):
        raise SystemExit("A reviewed manufacturing order profile is required for the KiCad stackup")
    mask_color = fabrication.get("solder_mask_color")
    copper_finish = fabrication.get("surface_finish")
    if not isinstance(mask_color, str) or not mask_color or not isinstance(copper_finish, str) or not copper_finish:
        raise SystemExit("Manufacturing order profile lacks solder-mask color or surface finish")

    lines = [
        "\t\t(stackup",
        '\t\t\t(layer "F.SilkS"',
        '\t\t\t\t(type "Top Silk Screen")',
        "\t\t\t)",
        '\t\t\t(layer "F.Paste"',
        '\t\t\t\t(type "Top Solder Paste")',
        "\t\t\t)",
        '\t\t\t(layer "F.Mask"',
        '\t\t\t\t(type "Top Solder Mask")',
        f'\t\t\t\t(color {quoted(mask_color.title())})',
        "\t\t\t\t(thickness 0.01)",
        '\t\t\t\t(material "Solder mask")',
        "\t\t\t\t(epsilon_r 3.3)",
        "\t\t\t\t(loss_tangent 0)",
        "\t\t\t)",
        '\t\t\t(layer "F.Cu"',
        '\t\t\t\t(type "copper")',
        f'\t\t\t\t(thickness {number(copper["F.Cu"])})',
        "\t\t\t)",
    ]
    lines.extend(render_dielectric(groups[0]))
    lines.extend([
        '\t\t\t(layer "In1.Cu"',
        '\t\t\t\t(type "copper")',
        f'\t\t\t\t(thickness {number(copper["In1.Cu"])})',
        "\t\t\t)",
    ])
    lines.extend(render_dielectric(groups[1]))
    lines.extend([
        '\t\t\t(layer "In2.Cu"',
        '\t\t\t\t(type "copper")',
        f'\t\t\t\t(thickness {number(copper["In2.Cu"])})',
        "\t\t\t)",
    ])
    lines.extend(render_dielectric(groups[2]))
    lines.extend([
        '\t\t\t(layer "B.Cu"',
        '\t\t\t\t(type "copper")',
        f'\t\t\t\t(thickness {number(copper["B.Cu"])})',
        "\t\t\t)",
        '\t\t\t(layer "B.Mask"',
        '\t\t\t\t(type "Bottom Solder Mask")',
        f'\t\t\t\t(color {quoted(mask_color.title())})',
        "\t\t\t\t(thickness 0.01)",
        '\t\t\t\t(material "Solder mask")',
        "\t\t\t\t(epsilon_r 3.3)",
        "\t\t\t\t(loss_tangent 0)",
        "\t\t\t)",
        '\t\t\t(layer "B.Paste"',
        '\t\t\t\t(type "Bottom Solder Paste")',
        "\t\t\t)",
        '\t\t\t(layer "B.SilkS"',
        '\t\t\t\t(type "Bottom Silk Screen")',
        "\t\t\t)",
        f'\t\t\t(copper_finish {quoted(copper_finish)})',
        "\t\t\t(dielectric_constraints yes)",
        "\t\t)",
    ])
    dielectric_total = sum(
        float(sub["thickness_mm"])
        for group in groups for sub in group["sublayers"]
    )
    summary = {
        "template": stackup["template"],
        "template_access_id": stackup.get("template_access_id"),
        "laminated_construction_code": stackup.get("laminated_construction_code"),
        "copper_thickness_mm": copper,
        "dielectric_groups": groups,
        "surface_finish": copper_finish,
        "solder_mask_color": mask_color.lower(),
        "total_copper_thickness_mm": sum(float(value) for value in copper.values()),
        "total_dielectric_thickness_mm": dielectric_total,
    }
    return "\n".join(lines), summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-sha256", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--stack", type=Path, default=ROOT / "config/board-stack.json")
    parser.add_argument(
        "--manufacturer-stack",
        type=Path,
        default=ROOT / "config/jlcpcb-impedance-evidence.json",
    )
    parser.add_argument(
        "--manufacturing-profile",
        type=Path,
        default=ROOT / "config/manufacturing-profile.json",
    )
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if current_stage().get("stage") != "physical":
        raise SystemExit("Layer-stack changes are allowed only during the physical stage")
    board_path = configured_board(ROOT).resolve()
    before = sha256(board_path)
    if args.board_sha256 != before:
        raise SystemExit("Layer-stack plan board SHA-256 does not match the active candidate")
    stack = json.loads(args.stack.resolve().read_text(encoding="utf-8"))
    if args.profile != stack.get("active"):
        raise SystemExit("Requested profile must be the reviewed active board-stack profile")
    profile = stack.get("profiles", {}).get(args.profile)
    if not isinstance(profile, dict):
        raise SystemExit("Requested board-stack profile is missing")
    expected = profile.get("copper_layers")
    if expected != ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]:
        raise SystemExit("This bounded adapter currently supports only the reviewed four-layer stack")
    manufacturer_path = args.manufacturer_stack.resolve()
    manufacturer = json.loads(manufacturer_path.read_text(encoding="utf-8"))
    manufacturing_profile_path = args.manufacturing_profile.resolve()
    manufacturing_profile = json.loads(manufacturing_profile_path.read_text(encoding="utf-8"))
    rendered_stackup, manufacturer_summary = render_manufacturer_stackup(manufacturer, manufacturing_profile)

    ensure_kicad_python()
    import pcbnew

    board = pcbnew.LoadBoard(str(board_path))
    before_count = board.GetCopperLayerCount()
    board.SetCopperLayerCount(4)
    board.SetLayerName(pcbnew.In1_Cu, "In1.Cu")
    board.SetLayerName(pcbnew.In2_Cu, "In2.Cu")
    board.SetLayerType(pcbnew.F_Cu, pcbnew.LT_SIGNAL)
    board.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
    board.SetLayerType(pcbnew.In2_Cu, pcbnew.LT_POWER)
    board.SetLayerType(pcbnew.B_Cu, pcbnew.LT_SIGNAL)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    run = create_run("physical", "kicad-stack", args.run_id)
    pcbnew.SaveBoard(str(board_path), board)
    saved_text = board_path.read_text(encoding="utf-8")
    stack_start, stack_end, _ = sexpr_block(saved_text, "\t\t(stackup")
    board_path.write_text(
        saved_text[:stack_start] + rendered_stackup + saved_text[stack_end:],
        encoding="utf-8",
        newline="\n",
    )
    # Loading and saving proves that the exact stackup is accepted by the
    # installed KiCad parser and normalizes the bare `addsublayer` tokens.
    verified_board = pcbnew.LoadBoard(str(board_path))
    if verified_board.GetCopperLayerCount() != 4:
        raise SystemExit("KiCad did not preserve the reviewed four-layer stackup")
    pcbnew.SaveBoard(str(board_path), verified_board)
    _, _, verified_stackup = sexpr_block(board_path.read_text(encoding="utf-8"), "\t\t(stackup")
    for token in ("In1.Cu", "In2.Cu", "addsublayer", "0.107", "0.0994", "1.065", "4.38"):
        if token not in verified_stackup:
            raise SystemExit(f"KiCad stackup round-trip lost required token: {token}")
    receipt = {
        "schema": 2,
        "kind": "kicad_stack",
        "success": True,
        "board": relative(board_path),
        "before_sha256": before,
        "board_sha256": sha256(board_path),
        "profile": args.profile,
        "before_copper_layer_count": before_count,
        "after_copper_layer_count": verified_board.GetCopperLayerCount(),
        "copper_layers": expected,
        "layer_types": {"F.Cu": "signal", "In1.Cu": "power", "In2.Cu": "power", "B.Cu": "signal"},
        "manufacturer_stack": manufacturer_summary,
        "manufacturer_evidence": relative(manufacturer_path),
        "manufacturer_evidence_sha256": sha256(manufacturer_path),
        "manufacturing_profile": relative(manufacturing_profile_path),
        "manufacturing_profile_sha256": sha256(manufacturing_profile_path),
        "stackup_sha256": hashlib.sha256(verified_stackup.encode("utf-8")).hexdigest().upper(),
        "limits": "This receipt applies the reviewed physical stack. USB geometry remains separately bound to the impedance calculator receipt and routed-board return-path checks.",
    }
    evidence = run.evidence / "kicad-stack.json"
    evidence.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(
        run,
        True,
        artifacts={
            "receipt": relative(evidence),
            "stack": relative(args.stack.resolve()),
            "manufacturer_stack": relative(manufacturer_path),
        },
        metrics={"copper_layers": 4, "dielectric_sublayers": 5},
    )
    print(json.dumps({**receipt, "receipt": relative(evidence)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
