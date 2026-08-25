"""Load and verify the project's pinned template-engine contract."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sys
from typing import Any


def read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def source_digest(source_root: Path) -> str:
    """Hash only distributable engine sources, independent of caches/build output."""
    files = [source_root / "pyproject.toml"]
    files.extend(sorted((source_root / "src").rglob("*.py")))
    digest = hashlib.sha256()
    for path in files:
        if not path.is_file():
            raise RuntimeError(f"Template engine source file is missing: {path}")
        relative = path.relative_to(source_root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest().upper()


def activate(project_root: Path, contract_path: Path) -> dict[str, Any]:
    contract = read_object(contract_path, "Template engine contract")
    if contract.get("schema") != 1:
        raise RuntimeError("Template engine contract must use schema 1")
    source = contract.get("source")
    if not isinstance(source, dict):
        raise RuntimeError("Template engine contract needs a source object")
    source_type = source.get("type")
    source_root: Path | None = None
    if source_type == "path":
        relative = source.get("path")
        if not isinstance(relative, str) or not relative:
            raise RuntimeError("Path template engine source needs a non-empty path")
        source_root = (project_root / relative).resolve()
        package_root = source_root / "src"
        if not package_root.is_dir():
            raise RuntimeError(f"Template engine package source is missing: {package_root}")
        if str(package_root) not in sys.path:
            sys.path.insert(0, str(package_root))
        expected_digest = source.get("sha256")
        actual_digest = source_digest(source_root)
        if expected_digest != actual_digest:
            raise RuntimeError(
                "Template engine source digest mismatch; review the engine change and update "
                "config/template-engine.json intentionally"
            )
    elif source_type != "installed":
        raise RuntimeError(f"Unsupported template engine source type: {source_type!r}")
    package_name = str(contract.get("package", "pcb_template_engine"))
    module = importlib.import_module(package_name)
    expected_version = str(contract.get("version", ""))
    expected_api = contract.get("api_version")
    if getattr(module, "__version__", None) != expected_version:
        raise RuntimeError(
            f"Template engine version mismatch: expected {expected_version}, "
            f"actual {getattr(module, '__version__', None)}"
        )
    if getattr(module, "API_VERSION", None) != expected_api:
        raise RuntimeError(
            f"Template engine API mismatch: expected {expected_api}, "
            f"actual {getattr(module, 'API_VERSION', None)}"
        )
    return {
        "schema": 1,
        "success": True,
        "package": package_name,
        "version": expected_version,
        "api_version": expected_api,
        "source_type": source_type,
        "source_root": str(source_root) if source_root else None,
        "source_sha256": source.get("sha256") if source_type == "path" else None,
    }
