#!/usr/bin/env python3
"""Start Konnect without its unrelated implicit Claude first-run install."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


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

    return subprocess.call([str(executable), *sys.argv[2:]])


if __name__ == "__main__":
    raise SystemExit(main())
