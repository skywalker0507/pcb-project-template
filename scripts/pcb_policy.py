"""Small, explicit loaders for machine-readable PCB workflow policy."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


def read_object(path: Path, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{label} must contain a JSON object: {path}")
    return data


def configured_board(root: Path, flow_path: Path | None = None) -> Path:
    """Return the single project board selected by the existing PCB flow config."""
    flow = read_object(flow_path or root / "config/pcb-flow.json", "PCB flow")
    project = flow.get("project")
    value = project.get("board") if isinstance(project, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise SystemExit("PCB flow does not select project.board")
    board = Path(value)
    return board.resolve() if board.is_absolute() else (root / board).resolve()


def load_acceptance(path: Path, section: str, required_rules: set[str]) -> dict[str, object]:
    policy = read_object(path, "Acceptance policy")
    rules = policy.get(section)
    if not isinstance(rules, dict):
        raise SystemExit(f"Acceptance policy has no {section!r} section")
    configured = rules.get("requires")
    if not isinstance(configured, list) or not required_rules.issubset(set(configured)):
        missing = sorted(required_rules - set(configured or []))
        raise SystemExit(f"Acceptance policy is missing required {section} rules: {', '.join(missing)}")
    return rules


def selected_manufacturing_profile(path: Path) -> dict[str, object]:
    profile = read_object(path, "Manufacturing profile")
    if profile.get("selected") is not True or not profile.get("manufacturer") or not profile.get("source"):
        raise SystemExit("Manufacturing profile is not selected with manufacturer and source")
    return profile


def valid_source(value: str, base: Path) -> bool:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return True
    return (base / value).resolve().is_file() or Path(value).is_file()


def reviewed_routing_rules(path: Path) -> dict[str, object]:
    profile = selected_manufacturing_profile(path)
    rules = profile.get("routing_rules")
    if not isinstance(rules, dict) or rules.get("reviewed") is not True:
        raise SystemExit("Manufacturing profile has no reviewed routing rules; run apply_reviewed_fab_values.py after reviewing the source")
    values = rules.get("values")
    evidence = rules.get("evidence")
    if not isinstance(values, dict) or not values or not isinstance(evidence, list) or not evidence:
        raise SystemExit("Reviewed routing rules need explicit values and evidence")
    return profile


def require_no_prohibited_evidence(rules: dict[str, object], evidence: set[str]) -> None:
    prohibited = set(rules.get("prohibits", []))
    if "route_ok_as_release_evidence" in prohibited and "route_ok" in evidence:
        raise SystemExit("Acceptance policy prohibits route_ok as release evidence")


def relative_to(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)
