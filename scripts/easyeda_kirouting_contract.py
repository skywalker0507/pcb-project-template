#!/usr/bin/env python3
"""Pure validation helpers for the EasyEDA-to-project-KRT transport."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/easyeda-integration.json"


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError(f"Unsupported EasyEDA integration config: {path}")
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def board_fingerprint(payload: dict[str, Any]) -> str:
    return canonical_sha256({
        key: payload.get(key, [] if key != "board" else {})
        for key in ("board", "components", "nets", "existing_tracks", "existing_vias")
    })


def _net_name(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("name"), str):
        return item["name"]
    return None


def validate_mvp_request(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Routing payload must be an object")
    route_config = payload.get("routing_config")
    if not isinstance(route_config, dict):
        raise ValueError("routing_config is required")
    nets = route_config.get("nets_to_route")
    if not isinstance(nets, list) or not nets or any(not isinstance(net, str) or not net.strip() for net in nets):
        raise ValueError("nets_to_route must contain explicit non-empty net names")
    nets = list(dict.fromkeys(net.strip() for net in nets))
    limits = config["routing_contract"]["mvp"]
    maximum = int(limits["maximum_requested_nets"])
    if len(nets) > maximum:
        raise ValueError(f"MVP allows at most {maximum} requested net(s)")
    if any("*" in net or "?" in net for net in nets):
        raise ValueError("MVP forbids wildcard net requests")
    known = {_net_name(item) for item in payload.get("nets", [])}
    missing = sorted(net for net in nets if net not in known)
    if missing:
        raise ValueError("Requested nets are absent from the EasyEDA snapshot: " + ", ".join(missing))
    existing_on_target: list[str] = []
    if limits.get("require_target_nets_without_existing_copper"):
        for collection in ("existing_tracks", "existing_vias"):
            for item in payload.get(collection, []):
                if isinstance(item, dict) and item.get("net") in nets:
                    existing_on_target.append(str(item.get("net")))
                    break
        if existing_on_target:
            raise ValueError("MVP target nets must have no existing copper: " + ", ".join(sorted(set(existing_on_target))))
    forbidden = config["routing_contract"].get("ui_rule_fields_forbidden", [])
    ignored = sorted(field for field in forbidden if field in route_config)
    return {
        "nets": nets,
        "profile": limits["profile"],
        "board_fingerprint": board_fingerprint(payload),
        "ignored_ui_rule_fields": ignored,
    }


def git_checkout_state(
    path: Path,
    expected_commit: str,
    *,
    require_clean: bool = True,
    required_paths: list[str] | None = None,
) -> dict[str, Any]:
    if not (path / ".git").is_dir():
        return {"ok": False, "path": str(path), "error": "not_git_checkout"}
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    actual = head.stdout.strip() if head.returncode == 0 else None
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else True
    path_checks: list[dict[str, Any]] = []
    for relative in required_paths or []:
        working = path / relative
        expected = subprocess.run(
            ["git", "-C", str(path), "rev-parse", f"HEAD:{relative}"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        actual_blob = subprocess.run(
            ["git", "-C", str(path), "hash-object", str(working)], capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        ) if working.is_file() else None
        expected_blob = expected.stdout.strip() if expected.returncode == 0 else None
        actual_value = actual_blob.stdout.strip() if actual_blob is not None and actual_blob.returncode == 0 else None
        path_checks.append({
            "path": relative,
            "ok": bool(expected_blob and actual_value == expected_blob),
            "expected_blob": expected_blob,
            "actual_blob": actual_value,
        })
    required_paths_ok = all(item["ok"] for item in path_checks)
    return {
        "ok": bool(actual == expected_commit and required_paths_ok and (not require_clean or not dirty)),
        "path": str(path),
        "expected_commit": expected_commit,
        "actual_commit": actual,
        "clean": not dirty,
        "clean_required": require_clean,
        "required_paths_ok": required_paths_ok,
        "required_paths": path_checks,
    }


def additive_route_diff(input_pcb: Any, output_pcb: Any, segment_key: Any, via_key: Any) -> dict[str, Any]:
    before_segments = {segment_key(item) for item in input_pcb.segments}
    after_segments = {segment_key(item) for item in output_pcb.segments}
    before_vias = {via_key(item) for item in input_pcb.vias}
    after_vias = {via_key(item) for item in output_pcb.vias}
    removed_segments = before_segments - after_segments
    removed_vias = before_vias - after_vias
    return {
        "additive_only": not removed_segments and not removed_vias,
        "segments_added": len(after_segments - before_segments),
        "vias_added": len(after_vias - before_vias),
        "segments_removed": len(removed_segments),
        "vias_removed": len(removed_vias),
    }
