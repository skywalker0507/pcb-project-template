#!/usr/bin/env python3
"""Start Konnect without its unrelated implicit Claude first-run install."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def windows_short_path(path: Path) -> str:
    """Return the Windows short path used by KiCad's named-pipe endpoint."""
    if os.name != "nt":
        return str(path)
    get_short_path = ctypes.windll.kernel32.GetShortPathNameW
    size = get_short_path(str(path), None, 0)
    if not size:
        return str(path)
    buffer = ctypes.create_unicode_buffer(size)
    if not get_short_path(str(path), buffer, size):
        return str(path)
    return buffer.value


def discover_kicad_api_socket() -> str | None:
    """Resolve KiCad's stable IPC endpoint without hard-coding a user profile."""
    configured = os.environ.get("KICAD_API_SOCKET", "").strip()
    if configured:
        return configured
    if os.name != "nt":
        return None
    pipe_root = Path("\\\\.\\pipe\\\\")
    try:
        names = sorted(
            (item.name for item in pipe_root.iterdir()),
            key=lambda value: ("api-" in value.lower(), value.lower()),
        )
    except OSError:
        names = []
    candidates = [
        name for name in names
        if name.lower().endswith(r"\kicad\api.sock")
        or (r"\kicad\api-" in name.lower() and name.lower().endswith(".sock"))
    ]
    if candidates:
        return "ipc://" + candidates[0]
    # KiCad 10 uses this stable named-pipe name for its first PCB editor.
    # Supplying it before KiCad starts is safe: Konnect connects on demand.
    temp = windows_short_path(Path(tempfile.gettempdir()))
    return "ipc://" + str(Path(temp) / "kicad" / "api.sock")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: start_konnect_mcp.py <konnect.exe> [arguments ...]")

    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"Konnect executable not found: {executable}")

    # Konnect silently installs Claude skills, agents and hooks on its first MCP
    # launch. Codex does not use them. The marker suppresses that side effect;
    # `konnect init` remains available for an intentional Claude installation.
    marker = Path.home() / ".konnect/.installed"
    if not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("codex-mcp\n", encoding="utf-8")

    socket = discover_kicad_api_socket()
    if socket:
        os.environ["KICAD_API_SOCKET"] = socket

    return subprocess.call([str(executable), *sys.argv[2:]])


if __name__ == "__main__":
    raise SystemExit(main())
