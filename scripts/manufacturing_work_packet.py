#!/usr/bin/env python3
"""Create and verify a compact, hash-bound manufacturing review work packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


SCHEMA = 1
KIND = "manufacturing_work_packet"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def object_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def _copy_record(source: Path, destination: Path, root: Path) -> dict[str, Any]:
    source = source.resolve()
    if not source.is_file():
        raise SystemExit(f"Manufacturing work-packet input is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "path": destination.relative_to(root).as_posix(),
        "sha256": sha256(destination),
        "bytes": destination.stat().st_size,
    }


def create_work_packet(
    root: Path,
    *,
    board: Path,
    audit: Path,
    gerber_zip: Path,
    bom: Path,
    cpl: Path,
    vendor: str,
    quote_currency: str,
    order_parameters: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Copy exact review inputs into one packet and write a stable manifest."""
    root = root.resolve()
    files = {
        "board": _copy_record(board, root / "source/review-board.kicad_pcb", root),
        "candidate_audit": _copy_record(audit, root / "evidence/candidate-audit.json", root),
        "gerber_zip": _copy_record(gerber_zip, root / "upload/candidate-gerber.zip", root),
        "bom": _copy_record(bom, root / "upload/BOM_JLCPCB.csv", root),
        "cpl": _copy_record(cpl, root / "upload/CPL_JLCPCB.csv", root),
    }
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": KIND,
        "vendor": vendor,
        "quote_currency": quote_currency,
        "files": files,
        "order_parameters": order_parameters,
        "upload_instructions": {
            "gerber": files["gerber_zip"]["path"],
            "bom": files["bom"]["path"],
            "cpl": files["cpl"]["path"],
            "rule": "Upload only files from upload/. PCB, BOM and CPL must remain from this same manifest.",
        },
    }
    manifest["content_sha256"] = object_sha256(manifest)
    path = root / "dfm-work-packet.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, manifest


def verify_work_packet(path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    path = path.resolve()
    manifest = load_object(path, "Manufacturing work-packet manifest")
    if manifest.get("schema") != SCHEMA or manifest.get("kind") != KIND:
        raise SystemExit("Manufacturing work-packet schema or kind mismatch")
    if not isinstance(manifest.get("vendor"), str) or not str(manifest.get("vendor")).strip():
        raise SystemExit("Manufacturing work-packet vendor is missing")
    if not isinstance(manifest.get("quote_currency"), str) or not str(manifest.get("quote_currency")).strip():
        raise SystemExit("Manufacturing work-packet quote currency is missing")
    expected = manifest.get("content_sha256")
    stable = dict(manifest)
    stable.pop("content_sha256", None)
    if expected != object_sha256(stable):
        raise SystemExit("Manufacturing work-packet content hash mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise SystemExit("Manufacturing work-packet lacks files")
    required = {"board", "candidate_audit", "gerber_zip", "bom", "cpl"}
    if set(files) != required:
        raise SystemExit("Manufacturing work-packet file roles do not match the required set")
    resolved: dict[str, Path] = {}
    for role in sorted(required):
        record = files.get(role)
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise SystemExit(f"Manufacturing work-packet file record is invalid: {role}")
        candidate = (path.parent / record["path"]).resolve()
        if path.parent not in candidate.parents:
            raise SystemExit(f"Manufacturing work-packet path escapes its root: {role}")
        if not candidate.is_file():
            raise SystemExit(f"Manufacturing work-packet file is missing: {role}: {candidate}")
        if record.get("sha256") != sha256(candidate) or record.get("bytes") != candidate.stat().st_size:
            raise SystemExit(f"Manufacturing work-packet file hash or size mismatch: {role}")
        resolved[role] = candidate
    return manifest, resolved


def write_observation_skeleton(root: Path, manifest: dict[str, Any]) -> Path:
    skeleton = {
        "schema": 2,
        "kind": "manufacturing_dfm_observation",
        "source": "jlc_dfm_report_review",
        "work_packet_content_sha256": manifest["content_sha256"],
        "order_parameters": manifest["order_parameters"],
        "analysis_modules": {
            "pcb": {"executed": False, "pass": False, "reported_result": "not_run"},
            "smt": {"executed": False, "pass": False, "reported_result": "not_run"},
        },
        "findings_summary": {
            "errors": None,
            "warnings": None,
            "accepted_exceptions": [],
            "blocking_findings": [],
            "unresolved_items": [],
        },
        "review": {
            "status": "pending",
            "reviewer": None,
            "method": "web_ai_or_operator_structured_from_vendor_report",
            "note": "Set confirmed only after checking the complete PCB and SMT DFM report.",
        },
    }
    path = root.resolve() / "dfm-observation.json"
    path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_quote_skeleton(root: Path, manifest: dict[str, Any]) -> Path:
    files = manifest["files"]
    skeleton = {
        "schema": 2,
        "kind": "manufacturing_quote_observation",
        "source": {
            "provider": manifest["vendor"],
            "channel": "manual_until_official_api_access_is_approved",
            "quote_id": None,
        },
        "observed_at": None,
        "expires_at": None,
        "currency": manifest["quote_currency"],
        "quantity": None,
        "total": None,
        "breakdown_scope": "complete",
        "work_packet_content_sha256": manifest["content_sha256"],
        "artifacts": {
            role: {"sha256": files[role]["sha256"]}
            for role in ("board", "gerber_zip", "bom", "cpl")
        },
        "order_parameters": manifest["order_parameters"],
        "approval": {
            "status": "pending",
            "reviewer": None,
            "reason": "Review every fee line and the complete order configuration before accepting.",
        },
        "line_items": [],
    }
    path = root.resolve() / "quote-observation.json"
    path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
