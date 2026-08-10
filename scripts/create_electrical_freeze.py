#!/usr/bin/env python3
"""Create an immutable release-lane electrical-freeze bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from artifact_store import create_run, finish_run, relative
from electrical_backend import DEFAULT_CONFIG, ROOT, read_config, selected_backend, source_sha256


TOOLCHAIN_RECEIPT = ROOT / "build/00-shared/evidence/toolchain/toolchain-resolved.json"
PROVENANCE_INPUTS = {
    "requirements": ROOT / "config/requirements.json",
    "product_decisions": ROOT / "config/product-decisions.json",
    "selected_parts": ROOT / "config/selected-parts.json",
    "footprint_provenance": ROOT / "config/footprint-provenance.json",
    "electrical_backend": DEFAULT_CONFIG,
    "atopile_project": ROOT / "ato.yaml",
    "toolchain_receipt": TOOLCHAIN_RECEIPT,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def verify_contract_inputs(contract: dict[str, object], inputs: dict[str, Path]) -> None:
    recorded = contract.get("inputs")
    if not isinstance(recorded, dict):
        raise SystemExit("Electrical contract receipt does not bind its reviewed input files")
    for name in ("requirements", "product_decisions", "selected_parts"):
        value = recorded.get(name)
        if not isinstance(value, dict) or value.get("sha256") != sha256(inputs[name]):
            raise SystemExit(f"Electrical contract receipt does not match current {name} SHA-256")


def source_file_manifest(source_root: Path) -> list[dict[str, str]]:
    records = []
    for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
        records.append({"path": path.relative_to(source_root).as_posix(), "sha256": sha256(path)})
    if not records:
        raise SystemExit(f"Reviewed atopile source tree is empty: {source_root}")
    return records


def promote_bundle(bundle_tmp: Path, release_dir: Path) -> None:
    if release_dir.exists():
        raise SystemExit(f"Electrical freeze release already exists and is immutable: {release_dir}")
    release_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(bundle_tmp, release_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-receipt", type=Path, required=True)
    parser.add_argument("--contract-receipt", type=Path, required=True)
    parser.add_argument("--candidate-review", type=Path, required=True)
    parser.add_argument("--freeze-id", help="Immutable artifact id; defaults to a timestamped id")
    args = parser.parse_args()
    config = read_config()
    backend_name, backend = selected_backend(config)
    if backend_name != "atopile" or backend.get("status") != "primary":
        raise SystemExit("Production electrical freeze requires the configured primary atopile backend")

    build_path = args.build_receipt.resolve()
    contract_path = args.contract_receipt.resolve()
    review_path = args.candidate_review.resolve()
    build = load(build_path, "Electrical build receipt")
    contract = load(contract_path, "Electrical contract receipt")
    review = load(review_path, "Candidate review receipt")
    if build.get("success") is not True or build.get("backend") != backend_name:
        raise SystemExit("Electrical build receipt is not a successful build from the active primary backend")
    if contract.get("success") is not True or contract.get("contract_pass") is not True:
        raise SystemExit("Electrical contract validation did not pass")
    if review.get("review_pass") is not True:
        raise SystemExit("Electrical candidate review did not pass")

    board = Path(str(build.get("candidate_board", ""))).resolve()
    manifest = Path(str(contract.get("manifest", ""))).resolve()
    if not board.is_file() or not manifest.is_file():
        raise SystemExit("Electrical candidate board or deterministic source manifest is unavailable")
    board_hash = sha256(board)
    manifest_payload = load(manifest, "Electrical source manifest")
    if manifest_payload.get("schema") != 2 or manifest_payload.get("board_sha256") != board_hash:
        raise SystemExit("Electrical source manifest schema/SHA does not match the candidate board")
    if contract.get("manifest_sha256") != sha256(manifest):
        raise SystemExit("Electrical contract receipt does not match the source manifest SHA-256")
    for label, value in (("contract", contract), ("candidate review", review)):
        if Path(str(value.get("candidate_board", ""))).resolve() != board:
            raise SystemExit(f"{label} does not reference the built candidate board")
        if value.get("candidate_board_sha256") != board_hash:
            raise SystemExit(f"{label} does not match the candidate board SHA-256")

    inputs = {name: path.resolve() for name, path in PROVENANCE_INPUTS.items()}
    for name, path in inputs.items():
        if not path.is_file():
            raise SystemExit(f"Freeze provenance input is unavailable: {name}: {path}")
    verify_contract_inputs(contract, inputs)
    toolchain = load(inputs["toolchain_receipt"], "Resolved toolchain receipt")
    tool_identity = toolchain.get("atopile")
    build_toolchain = build.get("toolchain")
    if not isinstance(tool_identity, dict) or not tool_identity.get("version"):
        raise SystemExit("Resolved toolchain receipt has no atopile compiler identity")
    if not isinstance(build_toolchain, dict):
        raise SystemExit("Electrical build receipt is not bound to a resolved toolchain receipt")
    if build_toolchain.get("receipt_sha256") != sha256(inputs["toolchain_receipt"]):
        raise SystemExit("Electrical build receipt does not match the resolved toolchain receipt SHA-256")
    if build_toolchain.get("compiler_version") != tool_identity.get("version"):
        raise SystemExit("Electrical build compiler identity does not match the resolved toolchain receipt")

    contract_meta = build.get("contract") if isinstance(build.get("contract"), dict) else {}
    source_root = (ROOT / str(backend["source_root"])).resolve()
    current_source_hash = source_sha256(source_root)
    if contract_meta.get("source_sha256") != current_source_hash:
        raise SystemExit("Reviewed atopile source changed after the electrical build")
    if contract_meta.get("config_sha256") != sha256(DEFAULT_CONFIG):
        raise SystemExit("Electrical backend configuration changed after the electrical build")

    run = create_run("electrical_freeze", "electrical-freeze", args.freeze_id)
    bundle_tmp = run.tmp / "bundle"
    provenance = bundle_tmp / "provenance"
    try:
        provenance.mkdir(parents=True, exist_ok=False)
        shutil.copy2(board, bundle_tmp / "frozen.kicad_pcb")
        shutil.copy2(manifest, bundle_tmp / "frozen-electrical-manifest.json")
        evidence_files = {
            "build_receipt": build_path,
            "contract_receipt": contract_path,
            "candidate_review": review_path,
            **inputs,
        }
        frozen_inputs: dict[str, dict[str, str]] = {}
        for name, source in evidence_files.items():
            destination = provenance / f"{name.replace('_', '-')}{source.suffix}"
            shutil.copy2(source, destination)
            frozen_inputs[name] = {"path": relative(run.release / "provenance" / destination.name), "sha256": sha256(destination)}
        source_copy = provenance / "source"
        shutil.copytree(source_root, source_copy)
        source_manifest = {
            "schema": 1,
            "kind": "atopile_source_manifest",
            "source_root": str(source_root.relative_to(ROOT).as_posix()),
            "frozen_source_root": relative(run.release / "provenance/source"),
            "source_sha256": current_source_hash,
            "files": source_file_manifest(source_root),
        }
        (bundle_tmp / "source-manifest.json").write_text(
            json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        payload = {
            "schema": 2,
            "kind": "electrical_freeze",
            "success": True,
            "freeze_pass": True,
            "freeze_id": run.run_id,
            "backend": backend_name,
            "source_sha256": current_source_hash,
            "frozen_board": relative(run.release / "frozen.kicad_pcb"),
            "frozen_board_sha256": board_hash,
            "frozen_manifest": relative(run.release / "frozen-electrical-manifest.json"),
            "frozen_manifest_sha256": sha256(manifest),
            "source_manifest": relative(run.release / "source-manifest.json"),
            "source_manifest_sha256": sha256(bundle_tmp / "source-manifest.json"),
            "provenance": frozen_inputs,
            "compiler": {"name": "atopile", "version": tool_identity["version"]},
            "invariant": "Only frozen_board and frozen_manifest may enter physical work. Any electrical identity or connectivity change requires a new atopile build, validation, review, and immutable freeze bundle.",
        }
        receipt = bundle_tmp / "electrical-freeze.json"
        receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        promote_bundle(bundle_tmp, run.release)
        finish_run(
            run, True,
            artifacts={
                "freeze_receipt": relative(run.release / "electrical-freeze.json"),
                "frozen_board": payload["frozen_board"],
                "frozen_manifest": payload["frozen_manifest"],
            },
            metrics={"board_sha256": board_hash, "source_sha256": current_source_hash},
        )
    except BaseException:
        finish_run(run, False)
        raise
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
