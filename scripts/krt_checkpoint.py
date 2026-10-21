#!/usr/bin/env python3
"""Start, inspect and finalize one KRT run without holding the caller open."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from artifact_store import ROOT, relative, safe_name, stage_directory
from stage_policy import current_stage


STATE_ROOT = ROOT / "build/00-shared/krt-async"


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def state_path(run_id: str) -> Path:
    return STATE_ROOT / f"{safe_name(run_id, 'Run id')}.json"


def load_state(run_id: str) -> dict[str, Any]:
    path = state_path(run_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"KRT async state not found or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SystemExit(f"Unsupported KRT async state: {path}")
    return value


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def classify_state(state: dict[str, Any], manifest: dict[str, Any] | None, alive: bool) -> dict[str, Any]:
    if manifest and manifest.get("status") in {"complete", "failed"}:
        return {
            "state": str(manifest["status"]),
            "terminal": True,
            "success": manifest.get("success"),
            "next": "finalize",
        }
    if manifest and manifest.get("status") == "in_progress" and alive:
        return {"state": "running", "terminal": False, "success": None, "next": "status"}
    if alive:
        return {"state": "starting", "terminal": False, "success": None, "next": "status"}
    return {
        "state": "orphaned",
        "terminal": True,
        "success": False,
        "next": "inspect launcher stderr; do not restart with the same run-id",
    }


def status_receipt(run_id: str) -> dict[str, Any]:
    state = load_state(run_id)
    manifest_path = ROOT / str(state["manifest"])
    manifest = read_manifest(manifest_path)
    result = classify_state(state, manifest, process_exists(int(state.get("pid", 0))))
    return {
        "schema": 1,
        "kind": "krt_async_checkpoint",
        "success": result["success"],
        "run_id": run_id,
        "state": result["state"],
        "terminal": result["terminal"],
        "pid": state.get("pid"),
        "manifest": relative(manifest_path),
        "report": state.get("report"),
        "stdout": state.get("stdout"),
        "stderr": state.get("stderr"),
        "next": result["next"],
    }


def command_start(args: argparse.Namespace) -> int:
    run_id = safe_name(args.run_id, "Run id")
    if state_path(run_id).exists():
        raise SystemExit(f"KRT async run already exists: {run_id}; use status/finalize")
    if not args.route_args:
        raise SystemExit("start requires the normal KRT route arguments after --run-id")
    if "--run-id" in args.route_args:
        raise SystemExit("Pass --run-id to krt_checkpoint start only once")
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "routing"}:
        raise SystemExit(f"KRT async start is unavailable in stage: {stage}")
    stage_root = stage_directory(stage)
    manifest = stage_root / "evidence" / run_id / "run.json"
    report = stage_root / "evidence" / run_id / "krt-report.json"
    launcher = stage_root / "tmp" / ".async" / run_id
    launcher.mkdir(parents=True, exist_ok=False)
    stdout_path = launcher / "stdout.log"
    stderr_path = launcher / "stderr.log"
    command = [
        sys.executable,
        str(ROOT / "scripts/krt_flow.py"),
        "--config", str(args.config.resolve()),
        "route", *args.route_args,
        "--run-id", run_id,
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            text=True,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    payload = {
        "schema": 1,
        "kind": "krt_async_process",
        "run_id": run_id,
        "stage": stage,
        "pid": process.pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "manifest": relative(manifest),
        "report": relative(report),
        "stdout": relative(stdout_path),
        "stderr": relative(stderr_path),
    }
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    state_path(run_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = status_receipt(run_id)
    receipt["started"] = True
    print(compact(receipt))
    return 0


def command_status(args: argparse.Namespace) -> int:
    receipt = status_receipt(args.run_id)
    print(compact(receipt))
    return 0 if receipt["state"] != "orphaned" else 2


def command_finalize(args: argparse.Namespace) -> int:
    receipt = status_receipt(args.run_id)
    if not receipt["terminal"]:
        receipt["finalized"] = False
        print(compact(receipt))
        return 3
    report_path = ROOT / str(receipt["report"])
    if report_path.is_file():
        result = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts/krt_flow.py"),
                "--config", str(args.config.resolve()),
                "report", "--path", str(report_path),
                "--max-items", str(args.max_items),
            ],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        rows = [line for line in result.stdout.splitlines() if line.lstrip().startswith("{")]
        receipt["result"] = json.loads(rows[-1]) if rows else None
    else:
        stderr_path = ROOT / str(receipt["stderr"])
        receipt["diagnostic_tail"] = (
            stderr_path.read_text(encoding="utf-8", errors="replace")[-1000:]
            if stderr_path.is_file() else ""
        )
    receipt["finalized"] = True
    print(compact(receipt))
    return 0 if receipt.get("success") is True else 2


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/kicad-routing-tools.json")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Launch one detached KRT route with a unique run-id")
    start.add_argument("--run-id", required=True)
    start.add_argument("route_args", nargs=argparse.REMAINDER)
    start.set_defaults(handler=command_start)
    status = commands.add_parser("status", help="Read process and artifact status without waiting")
    status.add_argument("--run-id", required=True)
    status.set_defaults(handler=command_status)
    finalize = commands.add_parser("finalize", help="Return the compact terminal KRT report without rerunning")
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--max-items", type=int, default=8)
    finalize.set_defaults(handler=command_finalize)
    args, unknown = parser.parse_known_args(argv)
    if args.command == "start":
        forwarded = [*unknown, *args.route_args]
        if forwarded and forwarded[0] == "--":
            forwarded.pop(0)
        args.route_args = forwarded
    elif unknown:
        parser.error("unrecognized arguments: " + " ".join(unknown))
    return args


def main() -> int:
    args = parse_cli()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
