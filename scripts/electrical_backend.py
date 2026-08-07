"""Shared configuration and receipts for replaceable electrical backends."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/electrical-backend.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def source_sha256(path: Path) -> str:
    """Hash source names and contents without including generated dependencies."""
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
        return digest.hexdigest().upper()
    if not path.is_dir():
        raise SystemExit(f"Electrical source root is unavailable: {path}")
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = item.relative_to(path).as_posix()
        if relative.startswith(("node_modules/", "dist/", ".git/")):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper()


def read_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Electrical backend configuration is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise SystemExit("Electrical backend configuration must use schema 1")
    backends = config.get("backends")
    if not isinstance(backends, dict) or not backends:
        raise SystemExit("Electrical backend configuration needs a non-empty backends object")
    return config


def selected_backend(config: dict[str, Any], requested: str | None = None) -> tuple[str, dict[str, Any]]:
    name = requested or config.get("active")
    if not isinstance(name, str) or not name:
        raise SystemExit("Electrical backend configuration needs an active backend")
    backend = (config.get("backends") or {}).get(name)
    if not isinstance(backend, dict):
        raise SystemExit(f"Unknown electrical backend: {name}")
    for key in ("driver", "source_root", "entry", "runtime", "platform", "status"):
        if not isinstance(backend.get(key), str) or not backend[key]:
            raise SystemExit(f"Electrical backend {name!r} needs a non-empty {key!r}")
    return name, backend


def workspace_path(value: str, label: str) -> Path:
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit(f"{label} must stay inside the workspace: {value}") from exc
    return path


def validate_backend(config: dict[str, Any], name: str, backend: dict[str, Any]) -> dict[str, Any]:
    driver = workspace_path(str(backend["driver"]), "Backend driver")
    source_root = workspace_path(str(backend["source_root"]), "Backend source_root")
    if not driver.is_file():
        raise SystemExit(f"Electrical backend {name!r} driver is unavailable: {driver}")
    if not source_root.exists():
        raise SystemExit(f"Electrical backend {name!r} source root is unavailable: {source_root}")
    return {
        "backend": name,
        "status": backend["status"],
        "platform": backend["platform"],
        "driver": str(driver.relative_to(ROOT).as_posix()),
        "source_root": str(source_root.relative_to(ROOT).as_posix()),
        "source_sha256": source_sha256(source_root),
        "config_sha256": sha256(DEFAULT_CONFIG),
    }
