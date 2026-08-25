#!/usr/bin/env python3
"""Apply an audited additive route plan to EasyEDA with rollback and verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from artifact_store import create_run, finish_run, relative, sha256
from capture_easyeda_pcb import capture_payload
from easyeda_bridge_client import read_config, request_json
from easyeda_kirouting_contract import board_fingerprint
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]


def geometry_key(item: dict[str, Any], kind: str) -> tuple[Any, ...]:
    if kind == "track":
        return (
            str(item.get("net", "")), int(item.get("layer", 0)),
            round(float(item.get("startX", 0)), 4), round(float(item.get("startY", 0)), 4),
            round(float(item.get("endX", 0)), 4), round(float(item.get("endY", 0)), 4),
            round(float(item.get("width", item.get("lineWidth", 0))), 4),
        )
    return (
        str(item.get("net", "")), round(float(item.get("x", 0)), 4), round(float(item.get("y", 0)), 4),
        round(float(item.get("holeDiameter", 0)), 4), round(float(item.get("diameter", 0)), 4),
    )


def normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("status") != "completed" or not isinstance(plan.get("tracks"), list) or not isinstance(plan.get("vias"), list):
        raise ValueError("Route plan must be a completed project-bridge result")
    if plan.get("units_mm") is not False:
        raise ValueError("MVP writeback accepts mil-unit route plans only")
    nets = {str(item.get("net", "")) for item in [*plan["tracks"], *plan["vias"]] if isinstance(item, dict)}
    nets.discard("")
    if len(nets) != 1:
        raise ValueError("MVP writeback requires exactly one routed net")
    if not plan["tracks"] and not plan["vias"]:
        raise ValueError("Route plan has no geometry")
    return {"net": next(iter(nets)), "tracks": plan["tracks"], "vias": plan["vias"]}


def execute(integration: dict[str, Any], health: dict[str, Any], code: str) -> dict[str, Any]:
    host = integration["api_bridge"].get("host", "127.0.0.1")
    response = request_json(
        f"http://{host}:{health['port']}/execute", method="POST",
        payload={"code": code, "windowId": health.get("active_window_id")}, timeout=35,
    )
    if response.get("success") is not True:
        raise RuntimeError(str(response.get("error") or "EasyEDA execute failed"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("EasyEDA execute returned no result object")
    return result


def apply_code(route: dict[str, Any]) -> str:
    data = json.dumps(route, ensure_ascii=False, separators=(",", ":"))
    return (
        f"const plan={data};const madeLines=[];const madeVias=[];"
        "try{for(const t of plan.tracks){const v=await eda.pcb_PrimitiveLine.create(t.net,t.layer,Math.round(t.startX),Math.round(t.startY),Math.round(t.endX),Math.round(t.endY),Math.round(t.width),false);if(!v){throw new Error('line_create_failed');}madeLines.push(v);}"
        "for(const v of plan.vias){const x=await eda.pcb_PrimitiveVia.create(v.net,v.x,v.y,v.holeDiameter,v.diameter);if(!x){throw new Error('via_create_failed');}madeVias.push(x);}"
        "return {success:true,tracksCreated:madeLines.length,viasCreated:madeVias.length};}"
        "catch(e){if(madeLines.length){await eda.pcb_PrimitiveLine.delete(madeLines);}if(madeVias.length){await eda.pcb_PrimitiveVia.delete(madeVias);}throw e;}"
    )


def rollback_code(net: str) -> str:
    value = json.dumps(net)
    return (
        f"const net={value};const lines=(await eda.pcb_PrimitiveLine.getAll()||[]).filter((x)=>x.net===net);"
        "const vias=(await eda.pcb_PrimitiveVia.getAll()||[]).filter((x)=>x.net===net);"
        "const lineOk=lines.length?await eda.pcb_PrimitiveLine.delete(lines):true;"
        "const viaOk=vias.length?await eda.pcb_PrimitiveVia.delete(vias):true;"
        "return {success:!!lineOk&&!!viaOk,tracksRemoved:lines.length,viasRemoved:vias.length};"
    )


def verify_geometry(payload: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    net = route["net"]
    actual_tracks = [item for item in payload["existing_tracks"] if item.get("net") == net]
    actual_vias = [item for item in payload["existing_vias"] if item.get("net") == net]
    expected_track_keys = {geometry_key({**item, "startX": round(item["startX"]), "startY": round(item["startY"]), "endX": round(item["endX"]), "endY": round(item["endY"]), "width": round(item["width"])}, "track") for item in route["tracks"]}
    actual_track_keys = {geometry_key(item, "track") for item in actual_tracks}
    expected_via_keys = {geometry_key(item, "via") for item in route["vias"]}
    actual_via_keys = {geometry_key(item, "via") for item in actual_vias}
    return {
        "success": expected_track_keys == actual_track_keys and expected_via_keys == actual_via_keys,
        "expected_tracks": len(expected_track_keys), "actual_tracks": len(actual_track_keys),
        "expected_vias": len(expected_via_keys), "actual_vias": len(actual_via_keys),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/easyeda-integration.json")
    parser.add_argument("--capture-code", type=Path, default=ROOT / "scripts/easyeda_headless_capture.js")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    require_operation("route")
    integration = read_config(args.config.resolve())
    plan_path = args.plan.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    route = normalize_plan(plan)
    run = create_run("routing", "easyeda-writeback", args.run_id)
    mutated = False
    try:
        before, health = capture_payload(args.capture_code.resolve(), integration)
        before_fingerprint = board_fingerprint(before)
        if before_fingerprint != plan.get("board_fingerprint"):
            raise RuntimeError("EasyEDA board changed after routing capture; refusing stale writeback")
        if any(item.get("net") == route["net"] for item in [*before["existing_tracks"], *before["existing_vias"]]):
            raise RuntimeError("MVP target net gained existing copper before writeback")
        plan_copy = run.tmp / "route-plan.json"
        plan_copy.write_text(json.dumps(plan, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        applied = execute(integration, health, apply_code(route))
        mutated = True
        after, _ = capture_payload(args.capture_code.resolve(), integration)
        verification = verify_geometry(after, route)
        if not verification["success"]:
            rollback = execute(integration, health, rollback_code(route["net"]))
            mutated = False
            raise RuntimeError("EasyEDA writeback verification failed; rollback=" + json.dumps(rollback, separators=(",", ":")))
        drc = execute(integration, health, "return {pass:await eda.pcb_Drc.check(true,true,false)};")
        receipt = {
            "schema": 1, "kind": "easyeda_route_writeback", "success": True,
            "net": route["net"], "source_plan_sha256": sha256(plan_path),
            "before_fingerprint": before_fingerprint, "after_fingerprint": board_fingerprint(after),
            "applied": applied, "verification": verification,
            "easyeda_drc_pass": bool(drc.get("pass")),
            "note": "A false EasyEDA DRC result may include other still-unrouted nets; final release still requires a passing full-board DRC.",
        }
        receipt_path = run.evidence / "easyeda-writeback.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finish_run(run, True, artifacts={"plan": relative(plan_copy), "receipt": relative(receipt_path)}, metrics={"tracks": verification["actual_tracks"], "vias": verification["actual_vias"], "easyeda_drc_pass": receipt["easyeda_drc_pass"]})
        print(json.dumps(receipt, ensure_ascii=True, separators=(",", ":")))
        return 0
    except BaseException as exc:
        if mutated:
            try:
                _, health = capture_payload(args.capture_code.resolve(), integration)
                execute(integration, health, rollback_code(route["net"]))
            except BaseException:
                pass
        finish_run(run, False, metrics={"error": str(exc)[:500]})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
