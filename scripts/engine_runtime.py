"""Project-side adapter for the independently versioned template engine."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config/template-engine.json"


def _bootstrap_path() -> None:
    try:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        source = contract["source"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Template engine contract is unavailable or invalid: {CONTRACT}: {exc}") from exc
    if source.get("type") == "path":
        package_root = (ROOT / str(source.get("path"))) / "src"
        if str(package_root.resolve()) not in sys.path:
            sys.path.insert(0, str(package_root.resolve()))


def activate() -> dict[str, Any]:
    _bootstrap_path()
    from pcb_template_engine.project import activate as activate_engine
    return activate_engine(ROOT, CONTRACT)


_bootstrap_path()
