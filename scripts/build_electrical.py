#!/usr/bin/env python3
"""Run the selected electrical backend through the common candidate contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from artifact_store import create_run, finish_run, relative
from electrical_backend import DEFAULT_CONFIG, ROOT, read_config, selected_backend, validate_backend, workspace_path
from electrical_candidate_contract import require_candidate_integrity
import electrical_source_preflight as source_preflight
import source_references
from electrical_build_identity import compiler_inputs, recorded_compiler_inputs, require_compiler_reuse


TOOLCHAIN_RECEIPT = ROOT / "build/00-shared/evidence/toolchain/toolchain-resolved.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def canonical_sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def review_context() -> dict[str, object]:
    """Bounded, informational snapshot; never an acceptance or reuse input."""
    context: dict[str, object] = {"informational_only": True}
    for key, filename in (("requirements", "requirements.json"),
                          ("selected_parts", "selected-parts.json"),
                          ("assembly_registry", "jlcpcb-parts.json")):
        try:
            payload = json.loads((ROOT / "config" / filename).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
        except (OSError, ValueError):
            context[key] = {"availability": "unavailable", "status": None}
            continue
        status = payload.get("status")
        entry: dict[str, object] = {"availability": "available",
                                   "status": status[:80] if isinstance(status, str) else None}
        if key == "requirements":
            unknowns = payload.get("unknowns")
            ids = [item["id"][:80] for item in unknowns
                   if isinstance(item, dict) and isinstance(item.get("id"), str)] if isinstance(unknowns, list) else []
            entry.update(declared_unknown_count=len(unknowns) if isinstance(unknowns, list) else None,
                         unknown_ids=ids[:12], unknown_ids_truncated=len(ids) > 12)
        if key == "selected_parts":
            parts = payload.get("parts")
            valid = isinstance(parts, list) and all(
                isinstance(part, dict) and isinstance(part.get("refs"), list)
                and all(isinstance(ref, str) and ref for ref in part["refs"]) for part in parts)
            entry["declared_unique_ref_count"] = len({ref for part in parts for ref in part["refs"]}) if valid else None
        context[key] = entry
    return context


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
    dependency_paths = [Path(__file__).resolve(), ROOT / "scripts/electrical_backend.py", driver,
                        ROOT / "scripts/check_electrical_candidate_integrity.py",
                        ROOT / "scripts/check_kicad_footprint_equivalence.py",
                        ROOT / "scripts/electrical_candidate_contract.py",
                        ROOT / "scripts/electrical_build_identity.py", ROOT / "scripts/source_references.py"]
    scope = ROOT / "config/project-initialization.json"
    if scope.is_file():
        dependency_paths.append(scope)
    if name == "atopile":
        dependency_paths.extend([ROOT / "ato.yaml", ROOT / "scripts/validate_atopile_source.py",
                                 ROOT / "scripts/normalize_atopile_candidate.py",
                                 ROOT / "scripts/extract_kicad_parity_manifest.py",
                                 ROOT / "scripts/check_easyeda_import_parity.py"])
    preflight_inputs = source_preflight.inputs(backend, ROOT)
    if preflight_inputs is not None:
        dependency_paths.extend([ROOT / "scripts/electrical_source_preflight.py",
                                 *(ROOT / item["path"] for item in preflight_inputs["files"])])
    references = source_references.inputs(ROOT)
    if references is not None:
        dependency_paths.extend([ROOT / references["manifest"]["path"], *(ROOT / item["path"] for item in references["files"])])
    dependencies = [
        {"path": relative(path), "sha256": sha256(path)}
        for path in sorted(set(dependency_paths), key=lambda item: relative(item))
    ]
    core: dict[str, object] = {
        "schema": 2,
        "backend": name,
        "build": build_name,
        "project": json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))["project"]["name"],
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
    core["compiler_input"] = compiler_inputs(core)
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
        try:
            require_candidate_integrity(payload, ROOT)
            source_preflight.require(payload, build_input.get("backend_config", {}), ROOT)
            source_references.require(payload, ROOT)
        except (ValueError, OSError):
            continue
        return {
            "receipt": relative(receipt),
            "candidate_board": relative(candidate),
            "candidate_board_sha256": candidate_hash,
        }
    return None


def find_matching_compilation(build_input: dict) -> dict | None:
    """Only unchanged compiler inputs and an intact, previously passing raw output qualify."""
    evidence = ROOT / "build/03-electrical/evidence"
    if not evidence.is_dir():
        return None
    expected = build_input["compiler_input"]
    for receipt in sorted(evidence.glob("*/electrical-build.json"), reverse=True):
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if payload.get("success") is not True or payload.get("backend") != build_input["backend"]:
                continue
            if recorded_compiler_inputs(payload.get("build_input")) != expected:
                continue
            require_candidate_integrity(payload, ROOT)
            raw = Path(payload.get("compiler_candidate_board", payload["candidate_board"])).resolve()
            raw.relative_to((ROOT / "build/03-electrical/tmp").resolve())
            raw_hash = payload.get("compiler_candidate_board_sha256", payload.get("candidate_board_sha256"))
            if not raw.is_file() or sha256(raw) != raw_hash:
                continue
            origin = require_compiler_reuse(payload, ROOT) or receipt
            return {"receipt": relative(receipt), "receipt_sha256": sha256(receipt),
                    "origin_receipt": relative(origin), "origin_receipt_sha256": sha256(origin),
                    "compiler_candidate": relative(raw), "compiler_candidate_sha256": raw_hash,
                    "compiler_input_fingerprint": expected["fingerprint"]}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", help="Configured backend name; defaults to config active")
    parser.add_argument("--build", default="default")
    parser.add_argument("--output", type=Path, help="Build artifact DIRECTORY;not a plan JSON file")
    parser.add_argument("--report", type=Path, help="Plan JSON under build/;default build/03-electrical/build-plan.json")
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
    if args.plan and (args.output or args.receipt):
        parser.error('--plan saves JSON with --report;--output/--receipt belong to actual builds')
    if args.report and not args.plan:
        parser.error('--report is for --plan;actual builds use --receipt')
    if not args.build.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise SystemExit("Build name must contain only letters, numbers, dot, dash, or underscore")

    config = read_config(DEFAULT_CONFIG)
    name, backend = selected_backend(config, args.backend)
    status = validate_backend(config, name, backend)
    try:
        references = source_references.inputs(ROOT)
        preflight = source_preflight.run(backend, ROOT, status)
    except (OSError, ValueError) as exc:
        print(json.dumps({"success": False, "error": "source_preflight_configuration_invalid",
                          "evidence": str(exc)}, separators=(",", ":")))
        return 1
    if preflight is not None and preflight["success"] is not True:
        print(json.dumps({"success": False, "error": "electrical_source_preflight_failed",
                          "source_preflight": preflight, "compiler_invoked": False}, separators=(",", ":")))
        return 1
    if args.check:
        print(json.dumps({"success": True, "checked": status, "source_preflight": preflight}, separators=(",", ":")))
        return 0

    toolchain = production_toolchain(name, status)
    build_input = build_input_manifest(name, backend, status, args.build, toolchain)
    scope = {"validation_scope": "modeled_components_only", "review_context": review_context()}
    if preflight is not None:
        scope["source_preflight"] = preflight
    if args.plan:
        matching = find_matching_build(build_input)
        compilation = None if matching else find_matching_compilation(build_input)
        build_required = matching is None and compilation is None
        plan = {
            "success": True,
            "action": "electrical-build-plan",
            "backend": name,
            "build": args.build,
            "build_required": build_required,
            "reason": "verified_matching_build" if matching else "matching_compilation_requires_revalidation" if compilation else "no_matching_verified_build",
            "input_fingerprint": build_input["fingerprint"],
            "matching_build": matching,
            "matching_compilation": compilation,
            "validation_required": matching is None,
            **scope,
            "next_action": "build_electrical_candidate" if build_required else "revalidate_electrical_candidate" if compilation else "review_matching_candidate",
            "next_command": (
                f"py -3.14 scripts/pcbctl.py build --backend {name} --build {args.build}"
                if matching is None else None
            ),
        }
        report = (args.report or ROOT / 'build/03-electrical/build-plan.json').resolve()
        try:
            report.relative_to((ROOT / 'build').resolve())
        except ValueError as exc:
            raise SystemExit('Plan report must stay under build/') from exc
        if report.suffix != '.json':
            raise SystemExit('Plan report must be a JSON file')
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps(plan, separators=(",", ":")))
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
            **scope,
            "next_action": "review_matching_candidate",
            "next_command": None,
            "override": "--force-rebuild",
        }, separators=(",", ":")))
        return 2

    compilation = None if args.force_rebuild else find_matching_compilation(build_input)

    run = None
    if args.output is None and args.receipt is None:
        run = create_run("electrical", "electrical-revalidation" if compilation else "electrical-build")
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
    if compilation:
        original_receipt = ROOT / compilation["receipt"]
        origin_receipt = ROOT / compilation["origin_receipt"]
        if sha256(original_receipt) != compilation["receipt_sha256"] or sha256(origin_receipt) != compilation["origin_receipt_sha256"]:
            raise SystemExit("Cached compiler evidence changed before revalidation")
        payload = json.loads(original_receipt.read_text(encoding="utf-8"))
        output.mkdir(parents=True, exist_ok=False)
        raw = ROOT / compilation["compiler_candidate"]
        copied = output / "candidate.kicad_pcb"
        shutil.copy2(raw, copied)
        if sha256(raw) != compilation["compiler_candidate_sha256"] or sha256(copied) != compilation["compiler_candidate_sha256"]:
            raise SystemExit("Raw compiler candidate changed while copying for revalidation")
        origin_copy = receipt.parent / "compiler-origin.json"
        shutil.copy2(origin_receipt, origin_copy)
        for key in ("normalization", "candidate_integrity", "source_preflight", "compiler_reuse"):
            payload.pop(key, None)
        payload.update(candidate_board=str(copied), compiler_candidate_board=str(copied),
                       compiler_candidate_board_sha256=compilation["compiler_candidate_sha256"],
                       compiler_invoked=False, compiler_reuse={
                           "origin_receipt": relative(origin_copy), "origin_receipt_sha256": sha256(origin_copy),
                           "compiler_candidate_sha256": compilation["compiler_candidate_sha256"],
                           "compiler_input_fingerprint": compilation["compiler_input_fingerprint"]})
        receipt.write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
        process = subprocess.CompletedProcess(command, 0, "Cached raw compiler output copied;current normalization and integrity gates still required.\n")
    else:
        process = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if process.returncode:
        try:
            failure = json.loads(receipt.read_text(encoding="utf-8")).get("failure")
        except (OSError, ValueError):
            failure = {"class": "backend_runtime_failure", "evidence": process.stdout.splitlines()[-6:]}
        if run:
            finish_run(run, False, artifacts={"receipt": relative(receipt), "log": relative(log)}, metrics={"exit_code": process.returncode})
        print(json.dumps({"success": False, "backend": name, "exit_code": process.returncode, "failure": failure, "receipt": relative(receipt), "log": relative(log)}, separators=(",", ":")))
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
    preflight_stale = False
    if preflight is not None:
        try:
            source_preflight.require({"source_preflight": preflight, "contract": post_status}, backend, ROOT)
        except (OSError, ValueError):
            preflight_stale = True
    if post_build_input["fingerprint"] != build_input["fingerprint"] or preflight_stale:
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
    if name == "atopile":
        normalization_path = receipt.parent / f"{receipt.stem}-normalization.json"
        normalized = candidate_board.with_name(f"{candidate_board.stem}-normalized.kicad_pcb")
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/normalize_atopile_candidate.py"),
            "--input", str(candidate_board), "--output", str(normalized),
            "--source-root", str(ROOT / backend["source_root"]),
            "--compiler-version", str(payload.get("compiler_version", "")), "--receipt", str(normalization_path),
        ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        try:
            normalization = json.loads(normalization_path.read_text(encoding="utf-8"))
            if not isinstance(normalization, dict):
                raise ValueError("Normalization receipt must be a JSON object")
            if result.returncode or normalization.get("success") is not True or not normalized.is_file():
                raise ValueError("Candidate normalization did not pass")
            if normalization.get("compiler_candidate_sha256") != sha256(candidate_board) or normalization.get("candidate_board_sha256") != sha256(normalized) or normalization.get("source_sha256") != post_status["source_sha256"]:
                raise ValueError("Candidate normalization is not bound to these exact inputs and output")
        except (OSError, ValueError) as exc:
            payload["success"] = False
            payload["failure"] = {"class": "atopile_candidate_normalization_failed", "evidence": str(exc).splitlines()[-3:],
                                  "normalization_receipt": relative(normalization_path),
                                  "next": "Review the bounded normalization evidence; do not freeze the raw compiler candidate."}
            receipt.write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
            if run:
                finish_run(run, False, artifacts={"receipt": relative(receipt), "log": relative(log)}, metrics={"failure_class": payload["failure"]["class"]})
            print(json.dumps({"success": False, "failure": payload["failure"], "receipt": relative(receipt)}, separators=(",", ":")))
            return 1
        payload["compiler_candidate_board"] = str(candidate_board)
        payload["compiler_candidate_board_sha256"] = sha256(candidate_board)
        payload["normalization"] = {"receipt": relative(normalization_path), "sha256": sha256(normalization_path)}
        candidate_board = normalized
        payload["candidate_board"] = str(candidate_board)
    payload["candidate_board_sha256"] = sha256(candidate_board)
    payload["build_input"] = build_input
    payload["contract"] = post_status
    payload["compiler_invoked"] = compilation is None
    payload["source_references"] = references
    payload.update(validation_scope="modeled_components_only", review_context=review_context())
    if preflight is not None:
        payload["source_preflight"] = preflight
    audit_path = receipt.parent / f"{receipt.stem}-candidate-integrity.json"
    audit_process = subprocess.run([
        sys.executable, str(ROOT / "scripts/check_electrical_candidate_integrity.py"),
        "--board", str(candidate_board), "--source-root", str(ROOT / backend["source_root"]),
        "--output", str(audit_path),
    ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    audit_payload = {}
    try:
        decoded_audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(decoded_audit, dict):
            raise ValueError("Candidate integrity receipt must be a JSON object")
        audit_payload = decoded_audit
        payload["candidate_integrity"] = {"receipt": relative(audit_path), "sha256": sha256(audit_path)}
        require_candidate_integrity(payload, ROOT)
        if audit_process.returncode:
            raise ValueError("Candidate integrity checker exited unsuccessfully")
    except (OSError, ValueError) as exc:
        payload["success"] = False
        payload["failure"] = {
            "class": "electrical_candidate_integrity_failed", "evidence": str(exc).splitlines()[-3:],
            "audit_receipt": relative(audit_path),
            "failed_refs": audit_payload.get("failed_refs", [])[:8],
            "audit_failure": audit_payload.get("failure"),
            "next": "Review the candidate integrity receipt. Restore native KiCad if unavailable; otherwise repair the source/exporter. Do not freeze or adopt this candidate.",
        }
        receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if run:
            finish_run(run, False, artifacts={"receipt": relative(receipt), "log": relative(log)}, metrics={"failure_class": payload["failure"]["class"]})
        print(json.dumps({"success": False, "backend": name, "failure": payload["failure"], "receipt": relative(receipt)}, separators=(",", ":")))
        return 1
    # Check the whole validation interval too,including normalization/native audit.
    try:
        final_status = validate_backend(config, name, backend)
        final_inputs = build_input_manifest(name, backend, final_status, args.build, production_toolchain(name, final_status))
        if final_inputs["fingerprint"] != build_input["fingerprint"]:
            raise ValueError("Producer or validation inputs changed during candidate validation")
        source_preflight.require(payload, backend, ROOT)
        source_references.require(payload, ROOT)
    except (OSError, ValueError) as exc:
        payload["success"] = False
        payload["failure"] = {"class": "electrical_validation_inputs_mutated", "evidence": str(exc)}
        receipt.write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
        if run:
            finish_run(run, False, artifacts={"receipt": relative(receipt), "log": relative(log)})
        print(json.dumps({"success": False, "failure": payload["failure"], "receipt": relative(receipt)}, separators=(",", ":")))
        return 1
    receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pointer = ROOT / 'build/03-electrical/BUILD.json'
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({'schema': 1, 'receipt': receipt.relative_to(ROOT).as_posix(),
                                  'sha256': sha256(receipt)}, indent=2) + '\n', encoding='utf-8')
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
        "compiler_invoked": payload["compiler_invoked"],
        "validation_scope": payload["validation_scope"],
        "review_context": payload["review_context"],
        "log": relative(log),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
