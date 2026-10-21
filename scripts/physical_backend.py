"""Configuration helpers for replaceable physical PCB backends."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/physical-backend.json"


def read_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Physical backend configuration is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise SystemExit("Physical backend configuration must use schema 1")
    contract = config.get("contract")
    if not isinstance(contract, dict) or contract.get("input_kind") != "electrical_freeze":
        raise SystemExit("Physical backend contract must consume an electrical_freeze")
    if contract.get("interchange_format") != "kicad_pcb":
        raise SystemExit("Physical backend contract must retain kicad_pcb interchange")
    if not isinstance(config.get("backends"), dict) or not config["backends"]:
        raise SystemExit("Physical backend configuration needs a non-empty backends object")
    return config


def selected_backend(config: dict[str, Any], requested: str | None = None) -> tuple[str, dict[str, Any]]:
    name = requested or config.get("active")
    backend = config.get("backends", {}).get(name) if isinstance(name, str) else None
    if not isinstance(backend, dict):
        raise SystemExit(f"Unknown physical backend: {name}")
    for key in ("role", "status", "integration", "capabilities"):
        if key not in backend:
            raise SystemExit(f"Physical backend {name!r} is missing {key!r}")
    if not isinstance(backend["capabilities"], list) or not backend["capabilities"]:
        raise SystemExit(f"Physical backend {name!r} needs non-empty capabilities")
    return str(name), backend
