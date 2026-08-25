#!/usr/bin/env python3
"""Maintain compact pointers to the latest release inputs.

The pointed receipts remain immutable evidence.  This index is deliberately
mutable: it prevents project configuration from being edited every time an
exact-board review or external DFM observation is refreshed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "build/07-release/INPUTS.json"
DEFAULT_READINESS = ROOT / "build/07-release/tmp/release-readiness/current.json"
EXPECTED_KINDS = {
    "final_electrical_parity": "final_electrical_parity",
    "jlc_dfm": "manufacturing_dfm",
    "physical_review": "physical_review",
    "jlcpcb_dfm_candidate": "jlcpcb_dfm_candidate",
    "release_review_packet": "release_review_packet",
    "release_visual_review": "pcb_visual_review",
    "kicad_drc_review": "kicad_drc_review",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def resolve_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def valid_record(name: str, record: object) -> tuple[Path, dict[str, Any]] | None:
    if not isinstance(record, dict):
        return None
    path = resolve_path(record.get("path"))
    if path is None or not path.is_file() or sha256(path) != record.get("sha256"):
        return None
    payload = load_json(path)
    expected = EXPECTED_KINDS.get(name)
    if payload is None or (expected is not None and payload.get("kind") != expected):
        return None
    return path, payload


def load_index(path: Path = DEFAULT_INDEX) -> dict[str, Any]:
    value = load_json(path.resolve())
    if not isinstance(value, dict) or value.get("kind") != "release_input_index":
        return {"schema": 1, "kind": "release_input_index", "inputs": {}}
    if not isinstance(value.get("inputs"), dict):
        value["inputs"] = {}
    return value


def register_release_input(
    name: str, path: Path, payload: dict[str, Any] | None = None,
    *, index_path: Path = DEFAULT_INDEX,
) -> dict[str, Any]:
    if name not in EXPECTED_KINDS:
        raise ValueError(f"unsupported release input: {name}")
    source = path.resolve()
    value = payload or load_json(source)
    if value is None or value.get("kind") != EXPECTED_KINDS[name]:
        raise ValueError(f"release input {name} has the wrong receipt kind")
    index = load_index(index_path)
    inputs = dict(index.get("inputs") or {})
    inputs[name] = {
        "path": relative(source),
        "sha256": sha256(source),
        "receipt_kind": value.get("kind"),
        "success": value.get("success"),
        "board_sha256": value.get("board_sha256", value.get("final_board_sha256")),
        "policy_sha256": value.get("policy_sha256"),
        "registry_sha256": value.get("registry_sha256"),
    }
    index = {"schema": 1, "kind": "release_input_index", "inputs": dict(sorted(inputs.items()))}
    target = index_path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return index


def configured_fallback(flow: dict[str, Any], name: str) -> Path | None:
    manufacturing = flow.get("manufacturing") or {}
    audit = flow.get("audit") or {}
    if name == "physical_review":
        return resolve_path(audit.get("physical_review"))
    gates = manufacturing.get("gate_receipts") or {}
    return resolve_path(gates.get(name))


def resolve_release_input(
    flow: dict[str, Any], name: str, *, index_path: Path = DEFAULT_INDEX,
) -> tuple[Path | None, dict[str, Any] | None, str]:
    index = load_index(index_path)
    record = (index.get("inputs") or {}).get(name)
    valid = valid_record(name, record)
    if valid is not None:
        return valid[0], valid[1], "index"
    if record is not None:
        path = resolve_path(record.get("path")) if isinstance(record, dict) else None
        return path, None, "invalid_index"
    fallback = configured_fallback(flow, name)
    payload = load_json(fallback) if fallback is not None else None
    return fallback, payload, "config_fallback"


def dependency(path: Path) -> dict[str, str]:
    return {"path": relative(path), "sha256": sha256(path)}


def adopted_routing_board(root: Path = ROOT) -> Path:
    pointer = root / "build/06-routing/CURRENT.json"
    value = load_json(pointer)
    if value is None:
        raise SystemExit("Release requires an adopted routing CURRENT.json")
    artifact = Path(str(value.get("artifact", "")))
    artifact = artifact.resolve() if artifact.is_absolute() else (root / artifact).resolve()
    if not artifact.is_file():
        raise SystemExit("Adopted routing artifact is missing")
    if value.get("artifact_sha256") != sha256(artifact):
        raise SystemExit("Adopted routing artifact does not match routing CURRENT.json")
    return artifact


def dependencies_fresh(values: object) -> bool:
    if isinstance(values, dict):
        values = [{"path": path, "sha256": digest} for path, digest in values.items()]
    if not isinstance(values, list):
        return False
    for item in values:
        if not isinstance(item, dict):
            return False
        path = resolve_path(item.get("path"))
        if path is None or not path.is_file() or sha256(path) != item.get("sha256"):
            return False
    return True


def fresh_release_readiness(
    board: Path, path: Path = DEFAULT_READINESS,
) -> dict[str, Any] | None:
    value = load_json(path.resolve())
    if value is None or value.get("kind") != "release_readiness":
        return None
    if not board.is_file() or value.get("board_sha256") != sha256(board.resolve()):
        return None
    dependencies = value.get("dependencies")
    if dependencies is None:
        manifest_path = resolve_path(value.get("dependency_manifest"))
        if (
            manifest_path is None or not manifest_path.is_file()
            or sha256(manifest_path) != value.get("dependency_manifest_sha256")
        ):
            return None
        manifest = load_json(manifest_path)
        dependencies = (manifest or {}).get("dependencies")
    return value if dependencies_fresh(dependencies) else None
