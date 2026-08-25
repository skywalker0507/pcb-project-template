#!/usr/bin/env python3
"""Restore no-paste connector pads lost by official EasyEDA import conversion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from artifact_store import create_run, finish_run, relative, sha256
from capture_easyeda_pcb import capture_payload, validate_capture
from easyeda_bridge_client import read_config, request_json
from easyeda_kirouting_contract import board_fingerprint
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
NO_PASTE_EXPANSION = -1000


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"Expected JSON object: {path}")
    return value


def operations_from(source: dict[str, Any], capture: dict[str, Any]) -> list[dict[str, Any]]:
    expected = {
        (component["reference"], pad["number"], pad.get("net"))
        for component in source["components"] for pad in component["pads"]
        if pad.get("pad_type") == "2"
    }
    operations = []
    for component in capture["components"]:
        reference = str(component["designator"])
        component_id = str(component.get("primitiveId", ""))
        for pad in component["pads"]:
            key = (reference, str(pad["number"]), str(pad["net"]) if pad.get("net") else None)
            if key not in expected:
                continue
            if pad.get("hole") is not None or int(pad.get("layer", 0)) not in {1, 2}:
                raise SystemExit(f"No-paste frozen pad is not a top/bottom SMD pad after import: {key}")
            old = pad.get("maskAndPasteExpansion")
            if not isinstance(old, dict):
                raise SystemExit(f"Imported pad has no explicit mask/paste state: {key}")
            paste_key = "topPasteMask" if int(pad["layer"]) == 1 else "bottomPasteMask"
            if old.get(paste_key) not in {0, NO_PASTE_EXPANSION}:
                raise SystemExit(f"Imported paste state is not the expected converter default: {key}")
            new = dict(old)
            new[paste_key] = NO_PASTE_EXPANSION
            operations.append({
                "reference": reference, "number": str(pad["number"]),
                "componentPrimitiveId": component_id, "padPrimitiveId": str(pad.get("primitiveId", "")),
                "pasteKey": paste_key, "oldExpansion": old, "newExpansion": new,
            })
            expected.remove(key)
    if expected:
        raise SystemExit(f"Frozen no-paste pads were not found after import: {sorted(expected)}")
    if not operations or any(not item["componentPrimitiveId"] or not item["padPrimitiveId"] for item in operations):
        raise SystemExit("No-paste repair has no complete primitive operation set")
    return operations


def apply(operations: list[dict[str, Any]], pcb_uuid: str, window_id: str, port: int) -> dict[str, Any]:
    code = f"""
const expectedPcb={json.dumps(pcb_uuid)};
const operations={json.dumps(operations, separators=(',', ':'))};
const pcb=await eda.dmt_Pcb.getCurrentPcbInfo();
if(!pcb||pcb.uuid!==expectedPcb){{throw new Error('Active PCB changed before no-paste repair');}}
const components=await eda.pcb_PrimitiveComponent.getAll()||[];
const componentIds=new Set(components.map(item=>String(item.primitiveId??item.id??'')));
const pads=new Map();
for(const component of components){{for(const pad of await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(String(component.primitiveId??component.id??''))||[]){{pads.set(String(pad.primitiveId??pad.id??''),pad);}}}}
const applied=[];
try{{
  for(const operation of operations){{
    const pad=pads.get(operation.padPrimitiveId);
    if(!pad||!componentIds.has(operation.componentPrimitiveId)){{throw new Error('No-paste primitive disappeared');}}
    const current=JSON.parse(JSON.stringify(pad.solderMaskAndPasteMaskExpansion));
    if(JSON.stringify(current)!==JSON.stringify(operation.oldExpansion)){{throw new Error('No-paste precondition changed');}}
    pad.setState_SolderMaskAndPasteMaskExpansion(operation.newExpansion);
    await pad.done();applied.push({{pad,old:operation.oldExpansion}});
  }}
  await eda.pcb_Document.save();
  return {{success:true,updated:applied.length,pcbUuid:pcb.uuid}};
}}catch(error){{
  for(let index=applied.length-1;index>=0;index--){{try{{applied[index].pad.setState_SolderMaskAndPasteMaskExpansion(applied[index].old);await applied[index].pad.done();}}catch(_){{}}}}
  try{{await eda.pcb_Document.save();}}catch(_){{}}throw error;
}}
"""
    response = request_json(
        f"http://127.0.0.1:{port}/execute", method="POST",
        payload={"code": code, "windowId": window_id}, timeout=60,
    )
    result = response.get("result")
    if response.get("success") is not True or not isinstance(result, dict) or result.get("success") is not True:
        raise SystemExit(f"EasyEDA no-paste repair failed: {response.get('error') or result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/easyeda-integration.json")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if str(current_stage().get("stage")) != "physical":
        raise SystemExit("EasyEDA import repair is allowed only during the physical stage")
    source_path, capture_path = args.source.resolve(), args.capture.resolve()
    source, captured = load(source_path), load(capture_path)
    operations = operations_from(source, captured)
    integration = read_config(args.config.resolve())
    code_path = (ROOT / "scripts/easyeda_headless_capture.js").resolve()
    fresh, health = capture_payload(code_path, integration)
    validate_capture(fresh)
    if board_fingerprint(fresh) != board_fingerprint(captured):
        raise SystemExit("Active EasyEDA PCB changed after the no-paste repair capture")
    pcb_uuid = str((captured.get("source", {}).get("pcb") or {}).get("uuid", ""))
    run = create_run("physical", "easyeda-import-paste-repair", args.run_id)
    plan_path = run.evidence / "easyeda-import-paste-repair-plan.json"
    plan_path.write_text(json.dumps({
        "schema": 1, "kind": "easyeda_import_paste_repair_plan",
        "source_manifest_sha256": sha256(source_path), "capture_sha256": sha256(capture_path),
        "pcb_uuid": pcb_uuid, "operation_count": len(operations), "operations": operations,
        "authority": "Official EasyEDA guidance uses -1000 paste expansion to remove unwanted paste apertures.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        result = apply(operations, pcb_uuid, str(health["active_window_id"]), int(health["port"]))
        verified, _ = capture_payload(code_path, integration)
        validate_capture(verified)
        verified_by_id = {pad.get("primitiveId"): pad for component in verified["components"] for pad in component["pads"]}
        for operation in operations:
            expansion = (verified_by_id.get(operation["padPrimitiveId"]) or {}).get("maskAndPasteExpansion")
            if not isinstance(expansion, dict) or expansion.get(operation["pasteKey"]) != NO_PASTE_EXPANSION:
                raise SystemExit(f"No-paste post-write verification failed: {operation['reference']}:{operation['number']}")
        receipt = {
            "schema": 1, "kind": "easyeda_import_paste_repair", "success": True,
            "pcb_uuid": pcb_uuid, "updated": result["updated"], "paste_expansion": NO_PASTE_EXPANSION,
            "source_manifest_sha256": sha256(source_path), "before_capture_sha256": sha256(capture_path),
            "plan": relative(plan_path),
            "limits": "Only frozen KiCad connector pads without F.Paste were changed; parity must be rerun.",
        }
        receipt_path = run.evidence / "easyeda-import-paste-repair.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finish_run(run, True, artifacts={"plan": relative(plan_path), "receipt": relative(receipt_path)}, metrics={"updated": result["updated"]})
        print(json.dumps(receipt, ensure_ascii=True, separators=(",", ":")))
        return 0
    except BaseException as exc:
        finish_run(run, False, metrics={"error": str(exc)[:500]})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
