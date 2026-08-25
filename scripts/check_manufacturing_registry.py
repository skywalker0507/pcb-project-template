#!/usr/bin/env python3
"""Validate the compiled manufacturing registry or run one deterministic phase."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

from engine_runtime import activate

activate()

from pcb_template_engine import API_VERSION, __version__  # noqa: E402
from pcb_template_engine.manufacturing import (  # noqa: E402
    evaluate_gerbers,
    evaluate_pre_export,
    validate_registry,
)
from design_policy import DEFAULT_OUTPUT, MANUFACTURING_REGISTRY_ARTIFACT, require_policy  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


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


def source_identity(args: argparse.Namespace) -> tuple[str, str, str]:
    if args.dfm_candidate_receipt:
        candidate = load(args.dfm_candidate_receipt.resolve(), "DFM candidate receipt")
        if candidate.get("kind") != "jlcpcb_dfm_candidate" or candidate.get("success") is not True:
            raise SystemExit("A passing isolated JLCPCB DFM candidate receipt is required")
        archive = Path(str(candidate.get("gerber_zip", ""))).resolve()
        if not archive.is_file() or sha256(archive) != candidate.get("gerber_zip_sha256"):
            raise SystemExit("DFM candidate Gerber archive is missing or changed")
        return str(candidate.get("board_sha256")), str(candidate.get("gerber_zip_sha256")), "dfm_candidate"
    if args.board is None or args.gerber_zip is None:
        raise SystemExit("--board and --gerber-zip are required for a staged production export")
    board = args.board.resolve()
    archive = args.gerber_zip.resolve()
    if not board.is_file() or not archive.is_file():
        raise SystemExit("Production board and Gerber archive must both exist")
    return sha256(board), sha256(archive), "staged_production_export"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("validate", "pre_export", "gerber"), default="validate")
    parser.add_argument("--registry", type=Path, default=MANUFACTURING_REGISTRY_ARTIFACT)
    parser.add_argument("--design-policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gerber-dir", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dfm-candidate-receipt", type=Path)
    source.add_argument("--board", type=Path)
    parser.add_argument("--gerber-zip", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    policy = require_policy("release", args.design_policy.resolve())
    registry = load(args.registry.resolve(), "Manufacturing registry")
    validate_registry(registry, policy, engine_version=__version__, engine_api_version=API_VERSION)
    if args.phase == "validate":
        receipt: dict[str, object] = {
            "schema": 1,
            "kind": "manufacturing_registry_validation",
            "success": True,
            "manufacturing_registry_pass": True,
            "policy_sha256": policy["policy_sha256"],
            "registry_sha256": registry["registry_sha256"],
            "rule_count": len(registry.get("rules", [])),
        }
    elif args.phase == "pre_export":
        receipt = evaluate_pre_export(registry)
    else:
        if args.gerber_dir is None:
            raise SystemExit("--gerber-dir is required for the gerber phase")
        parser_contract = (registry.get("gerber") or {}).get("parser", {})
        package = str(parser_contract.get("package", ""))
        expected_version = str(parser_contract.get("version", ""))
        try:
            actual_version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise SystemExit(
                f"{package} is required; install the registry-pinned requirements before Gerber validation"
            ) from exc
        if actual_version != expected_version:
            raise SystemExit(f"{package} version mismatch: expected {expected_version}, actual {actual_version}")
        from gerbonara.rs274x import GerberFile
        board_hash, archive_hash, source_kind = source_identity(args)
        evaluated = evaluate_gerbers(
            registry,
            args.gerber_dir.resolve(),
            open_gerber=GerberFile.open,
            file_sha256=sha256,
        )
        receipt = {
            "schema": 2,
            "kind": "manufacturing_registry_gerber_gate",
            **evaluated,
            "board_sha256": board_hash,
            "gerber_zip_sha256": archive_hash,
            "source_kind": source_kind,
            "parser": {"name": package, "version": actual_version},
            "limits": "Registry rules are deterministic rejection gates. Conservative geometry failures require explicit evidence before any false-positive waiver.",
        }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": receipt.get("success"),
        "kind": receipt.get("kind"),
        "policy_sha256": receipt.get("policy_sha256"),
        "registry_sha256": receipt.get("registry_sha256"),
        "pending": receipt.get("pending", []),
        "failed_rules": [item.get("id") for item in receipt.get("rules", []) if isinstance(item, dict) and item.get("pass") is False],
        "output": str(args.output.resolve()) if args.output else None,
    }, separators=(",", ":")))
    return 0 if receipt.get("success") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
