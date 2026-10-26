#!/usr/bin/env python3
"""Validate an immutable electrical-freeze bundle and its release-lane layout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FREEZE_ROOT = ROOT / "build/04-electrical-freeze/release"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_release_freeze(receipt_path: Path, freeze_root: Path = DEFAULT_FREEZE_ROOT) -> tuple[dict[str, object], dict[str, Path]]:
    receipt_path = receipt_path.resolve()
    freeze_root = freeze_root.resolve()
    try:
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Electrical freeze receipt is unavailable or invalid: {receipt_path}: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != 2
        or value.get("kind") != "electrical_freeze"
        or value.get("success") is not True
        or value.get("freeze_pass") is not True
    ):
        raise SystemExit("A passing schema-2 electrical freeze receipt is required")
    scope = ROOT / "config/project-initialization.json"
    if scope.is_file() and value.get("project_initialization_sha256") != sha256(scope):
        raise SystemExit("Electrical freeze belongs to a different project initialization; inherited freezes cannot be adopted")

    bundle = receipt_path.parent
    try:
        relative_bundle = bundle.relative_to(freeze_root)
    except ValueError as exc:
        raise SystemExit(f"Electrical freeze must live below the immutable release root: {freeze_root}") from exc
    if len(relative_bundle.parts) != 1 or receipt_path.name != "electrical-freeze.json":
        raise SystemExit("Electrical freeze receipt must be <freeze-root>/<freeze-id>/electrical-freeze.json")
    if value.get("freeze_id") != bundle.name:
        raise SystemExit("Electrical freeze id does not match its immutable release directory")

    expected = {
        "frozen_board": bundle / "frozen.kicad_pcb",
        "frozen_manifest": bundle / "frozen-electrical-manifest.json",
        "source_manifest": bundle / "source-manifest.json",
    }
    hashes = {
        "frozen_board": "frozen_board_sha256",
        "frozen_manifest": "frozen_manifest_sha256",
        "source_manifest": "source_manifest_sha256",
    }
    for field, path in expected.items():
        recorded = Path(str(value.get(field, "")))
        recorded = recorded.resolve() if recorded.is_absolute() else (ROOT / recorded).resolve()
        if recorded != path.resolve():
            raise SystemExit(f"Electrical freeze {field} must be the exact sibling artifact in its release bundle")
        if not path.is_file():
            raise SystemExit(f"Immutable freeze artifact is unavailable: {field}: {path}")
        if sha256(path) != value.get(hashes[field]):
            raise SystemExit(f"Immutable freeze artifact SHA-256 mismatch: {field}")
    return value, expected
