"""Run an explicitly declared, read-only project source check before compiling."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

from electrical_backend import sha256, source_sha256
from source_references import durable_files


def inputs(backend: dict, root: Path) -> dict | None:
    declaration = backend.get("source_preflight")
    if declaration is None:
        return None
    if (not isinstance(declaration, dict) or set(declaration) - {"script", "inputs", "timeout_seconds"}
            or not isinstance(declaration.get("script"), str)
            or not isinstance(declaration.get("inputs"), list)
            or not declaration["inputs"]):
        raise ValueError("source_preflight needs a workspace Python script and non-empty explicit inputs list")
    timeout = declaration.get("timeout_seconds", 60)
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError("source_preflight timeout_seconds must be an integer from 1 to 60")
    paths = []
    for value in [declaration["script"], *declaration["inputs"]]:
        if not isinstance(value, str) or not value:
            raise ValueError("source_preflight input paths must be non-empty strings")
        path = (root / value).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("source_preflight inputs must stay inside the workspace") from exc
        if not path.is_file():
            raise ValueError(f"source_preflight input is unavailable: {value}")
        if path in paths:
            raise ValueError(f"source_preflight input is declared twice: {value}")
        paths.append(path)
    if paths[0].suffix != ".py":
        raise ValueError("source_preflight script must be a Python file")
    durable_files(root, paths)
    return {
        "script": paths[0].relative_to(root.resolve()).as_posix(),
        "timeout_seconds": timeout,
        "files": [{"path": path.relative_to(root.resolve()).as_posix(), "sha256": sha256(path)}
                  for path in paths],
    }


def run(backend: dict, root: Path, status: dict) -> dict | None:
    identity = inputs(backend, root)
    if identity is None:
        return None
    # A preflight is evidence only. It must not change stage adoption pointers.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    temporary = root / "build/03-electrical/tmp" / (stamp + "-source-preflight")
    evidence = root / "build/03-electrical/evidence" / (stamp + "-source-preflight")
    temporary.mkdir(parents=True)
    evidence.mkdir(parents=True)
    report = temporary / "project-result.json"
    log = temporary / "project-check.log"
    problems = []
    result = {}
    exit_code = None
    try:
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.run(
                [sys.executable, str(root / identity["script"]), "--output", str(report)],
                cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                timeout=identity["timeout_seconds"], check=False,
            )
        exit_code = process.returncode
        if exit_code:
            problems.append("project_check_nonzero_exit")
    except subprocess.TimeoutExpired:
        problems.append("project_check_timeout")
    except OSError:
        problems.append("project_check_launch_failed")
    try:
        if report.stat().st_size > 262144:
            raise ValueError("Project result exceeds the compact result limit")
        result = json.loads(report.read_text(encoding="utf-8"))
        if (not isinstance(result, dict) or result.get("schema") != 1
                or result.get("success") is not True or result.get("issues") != []):
            problems.append("project_check_result_not_passing")
    except (OSError, ValueError):
        result = {}
        problems.append("project_check_result_unavailable_or_invalid")
    try:
        unchanged = (inputs(backend, root) == identity
                     and source_sha256(root / backend["source_root"]) == status["source_sha256"]
                     and sha256(root / "config/electrical-backend.json") == status["config_sha256"])
    except (OSError, ValueError, SystemExit):
        unchanged = False
    if not unchanged:
        problems.append("project_check_inputs_mutated")
    receipt = evidence / "source-preflight.json"
    payload = {
        "schema": 1, "kind": "electrical_source_preflight", "success": not problems,
        "issues": problems, "source_sha256": status["source_sha256"],
        "config_sha256": status["config_sha256"], "inputs": identity,
        "runner_sha256": sha256(Path(__file__)), "read_only_inputs_unchanged": unchanged,
        "exit_code": exit_code, "log": log.relative_to(root).as_posix(),
        "project_result": result, "scope": "Declared project checks only; no physical or manufacturing approval",
    }
    receipt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"success": payload["success"], "receipt": receipt.relative_to(root).as_posix(),
            "sha256": sha256(receipt), "issues": problems}


def require(build: dict, backend: dict, root: Path) -> Path | None:
    """Fail closed on stale project checks before copying a freeze bundle."""
    identity = inputs(backend, root)
    if identity is None:
        return None
    binding = build.get("source_preflight")
    if not isinstance(binding, dict) or binding.get("success") is not True:
        raise ValueError("Build has no passing source_preflight evidence; rebuild with the current gate")
    path = (root / str(binding.get("receipt", ""))).resolve()
    path.relative_to((root / "build/03-electrical/evidence").resolve())
    if not path.is_file() or sha256(path) != binding.get("sha256"):
        raise ValueError("source_preflight receipt is unavailable or changed")
    payload = json.loads(path.read_text(encoding="utf-8"))
    contract = build.get("contract") or {}
    if (not isinstance(payload, dict) or payload.get("kind") != "electrical_source_preflight"
            or payload.get("success") is not True or payload.get("issues") != []
            or payload.get("read_only_inputs_unchanged") is not True
            or payload.get("source_sha256") != contract.get("source_sha256")
            or payload.get("config_sha256") != contract.get("config_sha256")
            or payload.get("inputs") != identity or payload.get("runner_sha256") != sha256(Path(__file__))):
        raise ValueError("source_preflight evidence is stale or not passing for these exact inputs")
    return path
