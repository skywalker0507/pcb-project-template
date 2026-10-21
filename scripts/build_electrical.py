#!/usr/bin/env python3
"""Run the selected electrical backend through the common candidate contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from electrical_backend import DEFAULT_CONFIG, ROOT, read_config, selected_backend, validate_backend, workspace_path


TOOLCHAIN_RECEIPT = ROOT / "build/00-shared/evidence/toolchain/toolchain-resolved.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def canonical_sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def production_toolchain(name: str, status: dict[str, object]) -> tuple[dict[str, object], str] | None:
    if status.get("status") != "primary":
        return None
    try:
        payload = json.loads(TOOLCHAIN_RECEIPT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Primary electrical builds require a resolved toolchain receipt: {TOOLCHAIN_RECEIPT}: {exc}"
        ) from exc
    selected = payload.get("electrical_backend")
    if not isinstance(selected, dict) or selected.get("active") != name:
        raise SystemExit("Resolved toolchain receipt does not select the active production electrical backend")
    backend_identity = payload.get(name)
    if not isinstance(backend_identity, dict) or not backend_identity.get("version"):
        raise SystemExit(f"Resolved toolchain receipt has no compiler identity for {name}")
    return payload, hashlib.sha256(TOOLCHAIN_RECEIPT.read_bytes()).hexdigest().upper()


def build_input_manifest(
    name: str,
    backend: dict[str, object],
    status: dict[str, object],
    build_name: str,
    toolchain: tuple[dict[str, object], str] | None,
) -> dict[str, object]:
    """Describe every reviewed or local producer input needed to reuse a build."""
    driver = workspace_path(str(backend["driver"]), "Backend driver")
    dependency_paths = [Path(__file__).resolve(), ROOT / "scripts/electrical_backend.py", driver]
    if name == "atopile":
        dependency_paths.append(ROOT / "ato.yaml")
    dependencies = [
        {"path": relative(path), "sha256": sha256(path)}
        for path in sorted(set(dependency_paths), key=lambda item: relative(item))
    ]
    core: dict[str, object] = {
        "schema": 1,
        "backend": name,
        "build": build_name,
        "backend_config": {
            key: backend.get(key)
            for key in sorted(backend)
        },
        "contract": {
            "source_sha256": status["source_sha256"],
            "config_sha256": status["config_sha256"],
        },
        "producer_dependencies": dependencies,
        "toolchain": None,
    }
    if toolchain:
        payload, receipt_hash = toolchain
        identity = payload.get(name)
        core["toolchain"] = {
            "receipt": relative(TOOLCHAIN_RECEIPT),
            "receipt_sha256": receipt_hash,
            "identity": identity,
        }
    return {**core, "fingerprint": canonical_sha256(core)}


def find_matching_build(build_input: dict[str, object]) -> dict[str, object] | None:
    """Return a matching receipt only while its exact candidate still exists."""
    evidence = ROOT / "build/03-electrical/evidence"
    if not evidence.is_dir():
        return None
    expected = build_input.get("fingerprint")
    for directory in sorted((item for item in evidence.iterdir() if item.is_dir()), reverse=True):
        receipt = directory / "electrical-build.json"
        if not receipt.is_file():
            continue
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        recorded = payload.get("build_input")
        if payload.get("success") is not True or not isinstance(recorded, dict):
            continue
        if recorded.get("fingerprint") != expected:
            continue
        candidate_value = payload.get("candidate_board")
        candidate_hash = payload.get("candidate_board_sha256")
        if not isinstance(candidate_value, str) or not isinstance(candidate_hash, str):
            continue
        candidate = Path(candidate_value).resolve()
        try:
            candidate.relative_to((ROOT / "build").resolve())
        except ValueError:
            continue
        if not candidate.is_file() or sha256(candidate) != candidate_hash:
            continue
        return {
            "receipt": relative(receipt),
            "candidate_board": relative(candidate),
            "candidate_board_sha256": candidate_hash,
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", help="Configured backend name; defaults to config active")
    parser.add_argument("--build", default="default")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate backend wiring without invoking a build")
    parser.add_argument(
        "--plan", action="store_true",
        help="Compare exact reviewed and producer inputs with a verified prior build without invoking the compiler",
    )
    parser.add_argument(
        "--force-rebuild", action="store_true",
        help="Recompile even when an exact verified candidate survives; intended only for reproducibility checks",
    )
    args = parser.parse_args()
    if not args.build.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise SystemExit("Build name must contain only letters, numbers, dot, dash, or underscore")

    config = read_config(DEFAULT_CONFIG)
    name, backend = selected_backend(config, args.backend)
    status = validate_backend(config, name, backend)
    if args.check:
        print(json.dumps({"success": True, "checked": status}, separators=(",", ":")))
        return 0

    toolchain = production_toolchain(name, status)
    build_input = build_input_manifest(name, backend, status, args.build, toolchain)
    if args.plan:
        matching = find_matching_build(build_input)
        build_required = matching is None
        print(json.dumps({
            "success": True,
            "action": "electrical-build-plan",
            "backend": name,
            "build": args.build,
            "build_required": build_required,
            "reason": "verified_matching_build" if matching else "no_matching_verified_build",
            "input_fingerprint": build_input["fingerprint"],
            "matching_build": matching,
            "next_action": "build_electrical_candidate" if build_required else "review_or_freeze_matching_candidate",
            "next_command": (
                f"py -3.14 scripts/pcbctl.py build --backend {name} --build {args.build}"
                if build_required else None
            ),
        }, separators=(",", ":")))
        return 0

    matching = find_matching_build(build_input)
    if matching and not args.force_rebuild:
        print(json.dumps({
            "success": False,
            "error": "duplicate_verified_electrical_build",
            "backend": name,
            "build": args.build,
            "input_fingerprint": build_input["fingerprint"],
            "matching_build": matching,
            "next_action": "review_or_freeze_matching_candidate",
            "next_command": None,
            "override": "--force-rebuild",
        }, separators=(",", ":")))
        return 2

    run = None
    if args.output is None and args.receipt is None:
        run = create_run("electrical", "electrical-build")
    default_output = (
        run.tmp / "generated" / name / f"{args.build}-{status['source_sha256'][:12].lower()}"
        if run else ROOT / "build/03-electrical/tmp/compat" / name / f"{args.build}-{status['source_sha256'][:12].lower()}"
    )
    output = (args.output or default_output).resolve()
    try:
        output.relative_to(ROOT / "build")
    except ValueError as exc:
        raise SystemExit("Electrical candidate output must stay under build/") from exc
    receipt = (args.receipt or (run.evidence / "electrical-build.json" if run else output / "electrical-build.json")).resolve()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    log = (run.tmp / "electrical-build.log" if run else output / "electrical-build.log").resolve()
    driver = workspace_path(str(backend["driver"]), "Backend driver")
    command = [
        sys.executable, str(driver), "--build", args.build,
        "--output", str(output), "--receipt", str(receipt),
    ]
    if backend.get("platform") == "wsl-native" and isinstance(backend.get("distro"), str):
        command.extend(["--distro", str(backend["distro"]), "--entry", str(backend["entry"])])
        if backend.get("network_mode") in {"direct", "inherit"}:
            command.extend(["--network-mode", str(backend["network_mode"])])
    process = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if process.returncode:
        if run:
            finish_run(run, False, artifacts={"log": relative(log)}, metrics={"exit_code": process.returncode})
        print(json.dumps({"success": False, "backend": name, "exit_code": process.returncode, "log": relative(log)}, separators=(",", ":")))
        return process.returncode
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Backend completed without a valid receipt: {receipt}: {exc}") from exc
    if payload.get("success") is not True or payload.get("backend") != name:
        raise SystemExit("Backend receipt does not prove a successful build for the selected backend")
    post_status = validate_backend(config, name, backend)
    source_mutated = post_status["source_sha256"] != status["source_sha256"]
    config_mutated = post_status["config_sha256"] != status["config_sha256"]
    if source_mutated or config_mutated:
        payload["success"] = False
        payload["failure"] = {
            "class": "reviewed_inputs_mutated_during_build",
            "source_sha256_before": status["source_sha256"],
            "source_sha256_after": post_status["source_sha256"],
            "config_sha256_before": status["config_sha256"],
            "config_sha256_after": post_status["config_sha256"],
            "next": (
                "Review the tool-generated source/config changes, then rerun. A passing "
                "build must start and finish with identical reviewed inputs."
            ),
        }
        receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if run:
            finish_run(
                run,
                False,
                artifacts={"receipt": relative(receipt), "log": relative(log)},
                metrics={
                    "source_sha256_before": status["source_sha256"],
                    "source_sha256_after": post_status["source_sha256"],
                },
            )
        print(json.dumps({
            "success": False,
            "backend": name,
            "failure": payload["failure"],
            "receipt": relative(receipt),
            "log": relative(log),
        }, separators=(",", ":")))
        return 1
    post_toolchain = production_toolchain(name, post_status)
    post_build_input = build_input_manifest(name, backend, post_status, args.build, post_toolchain)
    if post_build_input["fingerprint"] != build_input["fingerprint"]:
        payload["success"] = False
        payload["failure"] = {
            "class": "electrical_build_inputs_mutated_during_build",
            "input_fingerprint_before": build_input["fingerprint"],
            "input_fingerprint_after": post_build_input["fingerprint"],
            "next": "Review producer, project configuration, or toolchain changes, then rerun the build.",
        }
        receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if run:
            finish_run(
                run, False,
                artifacts={"receipt": relative(receipt), "log": relative(log)},
                metrics={"failure_class": payload["failure"]["class"]},
            )
        print(json.dumps({
            "success": False,
            "backend": name,
            "failure": payload["failure"],
            "receipt": relative(receipt),
            "log": relative(log),
        }, separators=(",", ":")))
        return 1
    if toolchain:
        toolchain_payload, toolchain_hash = toolchain
        expected_version = toolchain_payload[name]["version"]
        if payload.get("compiler_version") != expected_version:
            raise SystemExit(
                f"Build compiler version {payload.get('compiler_version')!r} does not match resolved toolchain {expected_version!r}"
            )
        if hashlib.sha256(TOOLCHAIN_RECEIPT.read_bytes()).hexdigest().upper() != toolchain_hash:
            raise SystemExit("Resolved toolchain receipt changed during the electrical build")
        payload["toolchain"] = {
            "receipt": str(TOOLCHAIN_RECEIPT.resolve()),
            "receipt_sha256": toolchain_hash,
            "backend": name,
            "compiler_version": expected_version,
        }
    candidate_value = payload.get("candidate_board")
    if not isinstance(candidate_value, str):
        raise SystemExit("Backend receipt has no candidate_board")
    candidate_board = Path(candidate_value).resolve()
    try:
        candidate_board.relative_to((ROOT / "build").resolve())
    except ValueError as exc:
        raise SystemExit("Backend candidate_board must stay under build/") from exc
    if not candidate_board.is_file():
        raise SystemExit(f"Backend candidate_board is unavailable: {candidate_board}")
    payload["candidate_board_sha256"] = sha256(candidate_board)
    payload["build_input"] = build_input
    payload["contract"] = post_status
    receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if run:
        finish_run(
            run, True,
            artifacts={"receipt": relative(receipt), "log": relative(log)},
            metrics={"source_sha256": status["source_sha256"], "backend": name},
        )
    print(json.dumps({
        "success": True,
        "backend": name,
        "receipt": relative(receipt),
        "candidate_board": payload.get("candidate_board"),
        "log": relative(log),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
