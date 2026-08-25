#!/usr/bin/env python3
"""Compact client for the official EasyEDA API Skill Bridge.

Only compact JSON is printed. Bulk PCB payloads must be written to project
artifacts by a reviewed operation instead of being returned to model context.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/easyeda-integration.json"


def read_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SystemExit(f"Unsupported EasyEDA integration config: {path}")
    return value


def direct_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 2.0) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with direct_opener().open(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    if not isinstance(value, dict):
        raise RuntimeError("Bridge response must be a JSON object")
    return value


def discover(config: dict[str, Any], timeout: float = 0.8) -> tuple[int | None, dict[str, Any] | None]:
    bridge = config["api_bridge"]
    start, end = bridge["port_range"]
    expected_service = bridge["service"]
    host = bridge.get("host", "127.0.0.1")
    for port in range(int(start), int(end) + 1):
        try:
            health = request_json(f"http://{host}:{port}/health", timeout=timeout)
        except RuntimeError:
            continue
        if health.get("service") == expected_service:
            return port, health
    return None, None


def compact_health(config: dict[str, Any]) -> dict[str, Any]:
    port, health = discover(config)
    if port is None or health is None:
        return {"success": False, "service_found": False, "eda_connected": False, "window_count": 0}
    return {
        "success": bool(health.get("edaConnected")),
        "service_found": True,
        "service": health.get("service"),
        "port": port,
        "eda_connected": bool(health.get("edaConnected")),
        "window_count": int(health.get("edaWindowCount") or 0),
        "active_window_id": health.get("activeWindowId"),
        "pending_requests": int(health.get("pendingRequests") or 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    commands.add_parser("windows")
    select = commands.add_parser("select")
    select.add_argument("window_id")
    args = parser.parse_args()
    config = read_config(args.config.resolve())
    health = compact_health(config)
    if args.command == "health":
        result = health
    elif not health["service_found"]:
        result = health
    else:
        base = f"http://{config['api_bridge'].get('host', '127.0.0.1')}:{health['port']}"
        if args.command == "windows":
            value = request_json(base + "/eda-windows")
            windows = value.get("windows") if isinstance(value.get("windows"), list) else []
            result = {
                "success": bool(windows),
                "count": len(windows),
                "active_window_id": value.get("activeWindowId"),
                "windows": [
                    {"window_id": item.get("windowId"), "active": bool(item.get("active")), "connected": bool(item.get("connected"))}
                    for item in windows[:8] if isinstance(item, dict)
                ],
                "truncated": len(windows) > 8,
            }
        else:
            value = request_json(base + "/eda-windows/select", method="POST", payload={"windowId": args.window_id})
            result = {"success": bool(value.get("success")), "active_window_id": value.get("activeWindowId") or args.window_id}
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
