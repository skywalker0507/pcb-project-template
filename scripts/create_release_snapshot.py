#!/usr/bin/env python3
"""Create a hash-manifested, non-overwriting release evidence snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcb_policy import configured_board, selected_manufacturing_profile
from design_policy import DEFAULT_OUTPUT as DEFAULT_POLICY, require_policy
from export_jlcpcb_release import require_passing_physical_review
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def snapshot_name(board_hash: str) -> str:
    return f"release-{board_hash.lower()[:12]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, default=configured_board(ROOT))
    parser.add_argument("--package", type=Path, required=True, help="Checked JLCPCB export directory")
    parser.add_argument("--package-check", type=Path, required=True, help="Passing check_jlcpcb_release.py JSON receipt")
    parser.add_argument("--audit-json", type=Path, default=ROOT / "build/07-release/evidence/manual/final-routing-audit.json")
    parser.add_argument("--manufacturing-profile", type=Path, default=ROOT / "config/manufacturing-profile.json")
    parser.add_argument("--acceptance", type=Path, default=ROOT / "config/pcb-acceptance.json")
    parser.add_argument("--board-stack", type=Path, default=ROOT / "config/board-stack.json")
    parser.add_argument("--design-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--physical-review", type=Path, default=ROOT / "build/07-release/evidence/manual/physical-review.json")
    parser.add_argument("--assembly-parts", type=Path, default=ROOT / "config/jlcpcb-parts.json")
    parser.add_argument(
        "--review-asset",
        action="append",
        type=Path,
        default=[],
        help="Rendered review evidence to copy into the immutable snapshot.",
    )
    parser.add_argument("--output-root", type=Path, default=ROOT / "manufacturing/releases")
    parser.add_argument("--name", help="Optional safe suffix; default is board SHA based")
    args = parser.parse_args()
    require_operation("export")
    board = args.board.resolve()
    package = args.package.resolve()
    if not board.is_file() or not package.is_dir():
        raise SystemExit("Snapshot needs an existing board and export package directory")
    package_check = read_json(args.package_check.resolve(), "Package self-check")
    if package_check.get("success") is not True:
        raise SystemExit("Snapshot requires a successful JLCPCB package self-check")
    recorded_package = package_check.get("package")
    if not isinstance(recorded_package, str) or Path(recorded_package).resolve() != package:
        raise SystemExit("Package self-check does not belong to this export directory; rerun check_jlcpcb_release.py")
    audit = read_json(args.audit_json.resolve(), "Release audit")
    board_hash = sha256(board)
    if audit.get("release_pass") is not True or audit.get("board_sha256") != board_hash:
        raise SystemExit("Snapshot requires release_pass=true from an audit matching this exact board")
    design_policy = require_policy("release", args.design_policy.resolve())
    require_passing_physical_review(board, args.physical_review.resolve(), design_policy)
    assembly_parts = read_json(args.assembly_parts.resolve(), "Assembly parts")
    if assembly_parts.get("board_sha256") != board_hash:
        raise SystemExit("Assembly parts do not match the released board SHA-256")
    profile = selected_manufacturing_profile(args.manufacturing_profile.resolve())
    name = args.name or snapshot_name(board_hash)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name):
        raise SystemExit("Snapshot name must contain only letters, numbers, dot, underscore or dash")
    root = args.output_root.resolve()
    destination = root / name
    if destination.exists():
        raise SystemExit(f"Snapshot already exists and is immutable: {destination}")
    review_asset_sources = [source.resolve() for source in args.review_asset]
    missing_review_assets = [source for source in review_asset_sources if not source.is_file()]
    if missing_review_assets:
        raise SystemExit(f"Snapshot review asset is missing: {missing_review_assets[0]}")
    review_asset_names = [source.name for source in review_asset_sources]
    if len(set(review_asset_names)) != len(review_asset_names):
        raise SystemExit("Snapshot review asset names must be unique")
    evidence = {
        "board": board,
        "audit": args.audit_json.resolve(),
        "package_check": args.package_check.resolve(),
        "manufacturing_profile": args.manufacturing_profile.resolve(),
        "acceptance": args.acceptance.resolve(),
        "board_stack": args.board_stack.resolve(),
        "design_policy": args.design_policy.resolve(),
        "physical_review": args.physical_review.resolve(),
        "assembly_parts": args.assembly_parts.resolve(),
    }
    for label, source in evidence.items():
        if not source.is_file():
            raise SystemExit(f"Snapshot evidence is missing: {label}: {source}")
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{name}.staging-{os.getpid()}"
    if staging.exists():
        raise SystemExit(f"Snapshot staging path already exists: {staging}")
    try:
        shutil.copytree(package, staging / "package")
        copied: dict[str, str] = {}
        for label, source in evidence.items():
            target = staging / "evidence" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied[label] = target.relative_to(staging).as_posix()
        review_assets: list[str] = []
        for source in review_asset_sources:
            target = staging / "evidence" / "review-assets" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            review_assets.append(target.relative_to(staging).as_posix())
        hashes = {
            path.relative_to(staging).as_posix(): sha256(path)
            for path in sorted(staging.rglob("*")) if path.is_file()
        }
        manifest = {
            "schema": 1,
            "kind": "release_snapshot",
            "board_sha256": board_hash,
            "release_pass": True,
            "manufacturer": profile["manufacturer"],
            "manufacturing_source": profile["source"],
            "design_policy_sha256": design_policy["policy_sha256"],
            "evidence": copied,
            "review_assets": review_assets,
            "sha256": hashes,
            "note": "This directory is non-overwriting evidence. Re-verify hashes before relying on a copied snapshot.",
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            raise SystemExit(f"Snapshot already exists and is immutable: {destination}")
        os.replace(staging, destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(json.dumps({"success": True, "snapshot": str(destination), "manifest": str(destination / "manifest.json")}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
