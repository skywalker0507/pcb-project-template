#!/usr/bin/env python3
"""API-compatible EasyEDA KiRouting transport through the guarded project KRT flow.

The official converter/diff modules remain authoritative for interchange. This
service deliberately does not import the official routing runner: all routing
decisions pass through scripts/krt_flow.py and its independent candidate audit.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from artifact_store import create_run, finish_run, relative
from easyeda_kirouting_contract import (
    additive_route_diff,
    git_checkout_state,
    load_config,
    validate_mvp_request,
)
from stage_policy import current_stage


ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config()
UPSTREAM = (ROOT / CONFIG["kirouting"]["tool"]).resolve()
BRIDGE_MODULES = UPSTREAM / "bridge_server"
KRT_CONFIG = ROOT / CONFIG["routing_contract"]["router_config"]
POLICY_PATH = ROOT / CONFIG["routing_contract"]["policy_authority"]
KRT_FLOW = ROOT / CONFIG["routing_contract"]["router_entrypoint"]

_krt_settings = json.loads(KRT_CONFIG.read_text(encoding="utf-8"))["tool"]
KRT_REPOSITORY = (ROOT / _krt_settings["repository"]).resolve()
KRT_PY_ROUTER = (KRT_REPOSITORY / Path(_krt_settings["router_script"]).parent).resolve()

for module_path in (KRT_PY_ROUTER, KRT_REPOSITORY, BRIDGE_MODULES):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from models import PCBJsonData  # type: ignore  # official pinned module
from easyeda_to_kicad import convert  # type: ignore  # official pinned module
from kicad_diff import _segment_key, _via_key, extract_new_routes  # type: ignore
from kicad_parser import parse_kicad_pcb  # type: ignore


app = FastAPI(title="Project-Guarded EasyEDA KiRouting Bridge", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def parse_json_receipt(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("success" in value or "accepted" in value):
            return value
    return None


def tool_preflight() -> dict[str, Any]:
    # Endpoint-protection products may quarantine unused extension UI assets.
    # Production execution consumes only these pinned bridge modules, so verify
    # their Git blobs exactly instead of requiring the unrelated iframe tree.
    required_converter_paths = [
        f"bridge_server/{name}" for name in CONFIG["kirouting"]["converter_modules"]
    ]
    upstream = git_checkout_state(
        UPSTREAM,
        CONFIG["kirouting"]["required_commit"],
        require_clean=False,
        required_paths=required_converter_paths,
    )
    krt = json.loads(KRT_CONFIG.read_text(encoding="utf-8"))["tool"]
    krt_state = git_checkout_state((ROOT / krt["repository"]).resolve(), krt["commit"])
    required_modules = [BRIDGE_MODULES / name for name in CONFIG["kirouting"]["converter_modules"]]
    converter_ok = all(path.is_file() for path in required_modules)
    stage = str(current_stage().get("stage"))
    return {
        "ready": bool(upstream["ok"] and krt_state["ok"] and converter_ok and stage == "routing"),
        "stage": stage,
        "required_stage": "routing",
        "upstream": upstream,
        "krt": krt_state,
        "converter_modules_ready": converter_ok,
    }


def create_reviewed_project(board: Path) -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if policy.get("capabilities", {}).get("candidate_routing") is not True or policy.get("issues"):
        raise RuntimeError("Resolved design policy is not ready for candidate routing")
    routing = policy["routing"]["krt"]
    required = ("track_width", "clearance", "via_size", "via_drill")
    if any(routing.get(name) is None for name in required):
        raise RuntimeError("Resolved routing policy is incomplete")
    project = board.with_suffix(".kicad_pro")
    project.write_text(json.dumps({
        "board": {}, "cvpcb": {}, "erc": {}, "libraries": {},
        "meta": {"filename": project.name, "version": 1},
        "net_settings": {"classes": [{
            "name": "Default",
            "clearance": routing["clearance"],
            "track_width": routing["track_width"],
            "via_diameter": routing["via_size"],
            "via_drill": routing["via_drill"],
        }]},
    }, indent=2) + "\n", encoding="utf-8")
    return {"project": str(project), "project_sha256": file_sha256(project), "policy_sha256": policy["policy_sha256"]}


def compact_error(exc: BaseException) -> str:
    return str(exc).replace("\r", " ").replace("\n", " ")[:1000]


@dataclass
class Job:
    job_id: str
    payload: dict[str, Any]
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=lambda: {"status": "pending", "tracks": [], "vias": []})
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen[str] | None = None


jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
routing_lock = threading.Lock()
pending_extra: list[dict[str, Any]] = []


def run_job(job: Job) -> None:
    started = time.monotonic()
    capture = None
    try:
        preflight = tool_preflight()
        if not preflight["ready"]:
            raise RuntimeError("project_bridge_not_ready:" + json.dumps(preflight, separators=(",", ":")))
        contract = validate_mvp_request(job.payload, CONFIG)
        if job.cancelled.is_set():
            raise RuntimeError("routing_cancelled")
        if not routing_lock.acquire(timeout=1):
            raise RuntimeError("another_routing_job_is_active")
        try:
            capture_id = f"easyeda-capture-{job.job_id}"
            capture = create_run("routing", "easyeda-capture", capture_id)
            raw_path = capture.tmp / "easyeda-pcb.json"
            raw_path.write_text(json.dumps(job.payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            job.status = "converting"
            model = PCBJsonData(**job.payload)
            board = capture.tmp / "easyeda-input.kicad_pcb"
            board.write_text(convert(model), encoding="utf-8")
            project_receipt = create_reviewed_project(board)
            input_parsed = parse_kicad_pcb(str(board))
            job.status = "routing"
            route_id = f"easyeda-route-{job.job_id}"
            command = [
                sys.executable, str(KRT_FLOW), "route",
                "--profile", contract["profile"],
                "--input", str(board),
                "--nets", *contract["nets"],
                "--run-id", route_id,
            ]
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            job.process = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            output, _ = job.process.communicate()
            route_receipt = parse_json_receipt(output)
            if job.cancelled.is_set():
                raise RuntimeError("routing_cancelled")
            if job.process.returncode != 0 or not route_receipt or route_receipt.get("success") is not True:
                raise RuntimeError("guarded_krt_rejected_candidate:" + compact_error(output[-1500:]))
            candidate = (ROOT / str(route_receipt["output"])).resolve()
            if not candidate.is_file():
                raise RuntimeError("guarded_krt_candidate_missing")
            output_parsed = parse_kicad_pcb(str(candidate))
            additive = additive_route_diff(input_parsed, output_parsed, _segment_key, _via_key)
            if not additive["additive_only"]:
                raise RuntimeError("candidate_requires_destructive_writeback")
            net_map = input_parsed.net_id_to_name
            tracks, vias = extract_new_routes(
                str(board), str(candidate), net_map,
                units_mm=bool(model.routing_config.units_mm),
            )
            returned_nets = {item.net for item in [*tracks, *vias] if item.net}
            if not returned_nets.issubset(set(contract["nets"])):
                raise RuntimeError("candidate_diff_contains_unrequested_nets")
            if not tracks and not vias:
                raise RuntimeError("candidate_diff_is_empty")
            job.status = "converting_back"
            receipt = {
                "schema": 1,
                "kind": "easyeda_kirouting_candidate",
                "success": True,
                "job_id": job.job_id,
                "board_fingerprint": contract["board_fingerprint"],
                "requested_nets": contract["nets"],
                "ignored_ui_rule_fields": contract["ignored_ui_rule_fields"],
                "input_sha256": file_sha256(board),
                "candidate_sha256": file_sha256(candidate),
                "policy_sha256": project_receipt["policy_sha256"],
                "krt_report": route_receipt.get("report"),
                "candidate_audit_pass": bool((route_receipt.get("audit") or {}).get("baseline_pass")),
                "writeback_contract": additive,
            }
            receipt_path = capture.evidence / "easyeda-kirouting-candidate.json"
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            finish_run(capture, True, artifacts={
                "input": relative(board),
                "input_project": relative(board.with_suffix(".kicad_pro")),
                "candidate_receipt": relative(receipt_path),
                "krt_report": route_receipt.get("report"),
            }, metrics={
                "requested_net_count": len(contract["nets"]),
                "tracks_added": len(tracks),
                "vias_added": len(vias),
                "candidate_audit_pass": receipt["candidate_audit_pass"],
            })
            job.result = {
                "status": "completed",
                "stats": {
                    "nets_routed": len(returned_nets), "nets_failed": 0,
                    "tracks_added": len(tracks), "vias_added": len(vias),
                    "time_seconds": round(time.monotonic() - started, 2),
                },
                "tracks": [item.model_dump() for item in tracks],
                "vias": [item.model_dump() for item in vias],
                "units_mm": bool(model.routing_config.units_mm),
                "board_fingerprint": contract["board_fingerprint"],
                "receipt": relative(receipt_path),
            }
            job.status = "completed"
        finally:
            routing_lock.release()
    except BaseException as exc:
        if capture is not None:
            finish_run(capture, False, metrics={"error": compact_error(exc)})
        job.status = "cancelled" if job.cancelled.is_set() else "failed"
        job.result = {"status": job.status, "tracks": [], "vias": [], "error": compact_error(exc)}
        traceback.print_exc()
    finally:
        job.process = None


@app.get("/api/test")
async def api_test() -> dict[str, Any]:
    state = tool_preflight()
    return {
        "status": "ok" if state["ready"] else "not_ready",
        "version": "1.0.0-project-guarded",
        "ready": state["ready"],
        "stage": state["stage"],
        "upstream_commit": state["upstream"].get("actual_commit"),
        "krt_commit": state["krt"].get("actual_commit"),
    }


@app.get("/api/config/defaults")
async def defaults() -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))["routing"]["krt"]
    return {
        "policy_controlled": True,
        "track_width": policy["track_width"] / 0.0254,
        "clearance": policy["clearance"] / 0.0254,
        "via_size": policy["via_size"] / 0.0254,
        "via_drill": policy["via_drill"] / 0.0254,
        "layers_to_use": [1, 2],
    }


@app.post("/api/extra-components")
async def extra_components(request: Request) -> dict[str, Any]:
    value = await request.json()
    components = value.get("components", []) if isinstance(value, dict) else []
    if value.get("clear"):
        pending_extra.clear()
    pending_extra.extend(item for item in components if isinstance(item, dict))
    return {"status": "ok", "count": len(pending_extra)}


@app.post("/api/route")
async def submit(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        return {"status": "failed", "error": "request_must_be_object"}
    if pending_extra:
        payload.setdefault("components", []).extend(pending_extra)
        pending_extra.clear()
    try:
        validate_mvp_request(payload, CONFIG)
    except ValueError as exc:
        return {"status": "failed", "error": compact_error(exc)}
    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, payload)
    with jobs_lock:
        jobs[job_id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/status/{job_id}")
async def status(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    return {"job_id": job_id, "status": job.status if job else "not_found"}


@app.get("/api/result/{job_id}")
async def result(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    return job.result if job else {"status": "not_found", "tracks": [], "vias": [], "error": "job_not_found"}


@app.post("/api/cancel/{job_id}")
async def cancel(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    job.cancelled.set()
    if job.process is not None:
        job.process.terminate()
    return {"status": "cancelled"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=CONFIG["kirouting"].get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(CONFIG["kirouting"].get("port", 8765)))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    state = tool_preflight()
    if args.check:
        print(json.dumps(state, ensure_ascii=True, separators=(",", ":")))
        return 0 if state["upstream"]["ok"] and state["krt"]["ok"] and state["converter_modules_ready"] else 1
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Project KiRouting Bridge may listen on localhost only")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
