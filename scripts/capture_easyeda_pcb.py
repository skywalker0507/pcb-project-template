#!/usr/bin/env python3
"""Capture the active EasyEDA PCB to a bulk artifact and print a compact receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from artifact_store import create_run, finish_run, relative, sha256
from easyeda_bridge_client import compact_health, read_config, request_json
from easyeda_kirouting_contract import board_fingerprint
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CODE = ROOT / "scripts/easyeda_headless_capture.js"


def capture_payload(code_path: Path, integration: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    health = compact_health(integration)
    if not health.get("success"):
        raise RuntimeError("EasyEDA API Bridge has no connected active window")
    code = code_path.read_text(encoding="utf-8")
    host = integration["api_bridge"].get("host", "127.0.0.1")
    response = request_json(
        f"http://{host}:{health['port']}/execute", method="POST",
        payload={"code": code, "windowId": health.get("active_window_id")}, timeout=35,
    )
    result = response.get("result")
    if response.get("success") is not True or not isinstance(result, dict):
        raise RuntimeError(str(response.get("error") or "EasyEDA capture returned no object"))
    return result, health


def validate_capture(payload: dict[str, Any]) -> dict[str, int]:
    for name in ("board", "components", "nets", "existing_tracks", "existing_vias"):
        expected = dict if name == "board" else list
        if not isinstance(payload.get(name), expected):
            raise RuntimeError(f"EasyEDA capture lacks {name}")
    if not payload["components"] or not payload["nets"]:
        raise RuntimeError("EasyEDA capture is empty")
    outline_count = len(payload["board"].get("outlineLines", [])) + len(payload["board"].get("outlineArcs", []))
    return {
        "components": len(payload["components"]), "nets": len(payload["nets"]),
        "tracks": len(payload["existing_tracks"]), "vias": len(payload["existing_vias"]),
        "outline_primitives": outline_count,
        "pads": sum(len(item.get("pads", [])) for item in payload["components"] if isinstance(item, dict)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/easyeda-integration.json")
    parser.add_argument("--code", type=Path, default=DEFAULT_CODE)
    parser.add_argument("--run-id")
    parser.add_argument("--nets", nargs="*")
    args = parser.parse_args()
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "routing"}:
        raise SystemExit(f"EasyEDA PCB capture is forbidden during stage {stage}")
    integration = read_config(args.config.resolve())
    run = create_run(stage, "easyeda-capture", args.run_id)
    try:
        payload, health = capture_payload(args.code.resolve(), integration)
        if args.nets is not None:
            payload.setdefault("routing_config", {})["nets_to_route"] = args.nets
        counts = validate_capture(payload)
        bulk = run.tmp / "easyeda-pcb.json"
        bulk.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        receipt = {
            "schema": 1, "kind": "easyeda_pcb_capture", "success": True,
            "stage": stage, "window_id": health.get("active_window_id"),
            "board_fingerprint": board_fingerprint(payload), "bulk_sha256": sha256(bulk),
            "counts": counts, "bulk": relative(bulk),
        }
        receipt_path = run.evidence / "easyeda-capture.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finish_run(run, True, artifacts={"capture": relative(bulk), "receipt": relative(receipt_path)}, metrics=counts)
        print(json.dumps(receipt, ensure_ascii=True, separators=(",", ":")))
        return 0
    except BaseException as exc:
        finish_run(run, False, metrics={"error": str(exc)[:500]})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
