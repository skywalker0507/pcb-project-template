#!/usr/bin/env python3
"""Compact, cross-platform readiness check for the AI PCB toolchain."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import hashlib


def run(command: list[str], cwd: Path, timeout: int = 30) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command, cwd=cwd, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)


def add(items: list[dict[str, object]], name: str, ok: bool, detail: str, required: bool = True) -> None:
    items.append({"name": name, "ok": ok, "required": required, "detail": detail[:300]})


def git_head(path: Path, root: Path) -> tuple[bool, str]:
    if not (path / ".git").is_dir():
        return False, "not a Git checkout"
    return run(["git", "-C", str(path), "rev-parse", "HEAD"], root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--deep", action="store_true", help="also run KRT check and atopile build")
    args = parser.parse_args()
    root = args.project_root.resolve()
    checks: list[dict[str, object]] = []
    policy_path = root / "config/toolchain-policy.json"
    resolved_path = root / "build/toolchain-resolved.json"
    resolved = json.loads(resolved_path.read_text(encoding="utf-8")) if resolved_path.is_file() else {}

    for relative in ("ato.yaml", "config/pcb-flow.json", "config/kicad-routing-tools.json", "config/toolchain-policy.json"):
        add(checks, f"project:{relative}", (root / relative).is_file(), relative)
    add(checks, "project:resolved-toolchain", resolved_path.is_file(), str(resolved_path))
    mcp_configured = (root / ".codex/config.toml").is_file() or (root / ".codex/pcb-mcp.fragment.toml").is_file()
    add(checks, "project:mcp-config", mcp_configured, ".codex/config.toml or merge fragment")

    kicad = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
    add(checks, "kicad-cli", kicad.is_file(), str(kicad))
    add(checks, "git", shutil.which("git") is not None, shutil.which("git") or "not found")
    add(checks, "python", sys.version_info >= (3, 10), sys.version.split()[0])

    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    add(checks, "wsl", bool(wsl), wsl or "not found")
    if wsl:
        ok, output = run([wsl, "-d", args.distro, "--", "bash", "-lc", "command -v uv && command -v ato && ato --version | tail -n 1"], root)
        actual_ato = output.splitlines()[-1] if output.splitlines() else "missing"
        expected_ato = resolved.get("atopile", {}).get("version")
        add(checks, "wsl-atopile", ok and actual_ato == expected_ato, f"expected={expected_ato};actual={actual_ato}")
    else:
        add(checks, "wsl-atopile", False, "WSL unavailable")

    node = shutil.which("node")
    add(checks, "node", bool(node), node or "not found")
    houtini = root / "tools/houtini-lm"
    houtini_ok, houtini_head = git_head(houtini, root)
    expected_houtini = resolved.get("houtini_lm", {}).get("commit")
    add(checks, "houtini-commit", houtini_ok and houtini_head == expected_houtini, f"expected={expected_houtini};actual={houtini_head}")
    add(checks, "houtini-build", (houtini / "dist/index.js").is_file(), str(houtini / "dist/index.js"))
    key_present = bool(os.environ.get("HOUTINI_LM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))
    add(checks, "houtini-key", key_present, "present" if key_present else "missing", required=False)

    krt_repo = root / "tools/KiCadRoutingTools"
    krt_python = root / ".venv-routingtools/Scripts/python.exe"
    add(checks, "krt-repository", krt_repo.is_dir(), str(krt_repo))
    add(checks, "krt-python", krt_python.is_file(), str(krt_python))
    krt_ok, krt_head = git_head(krt_repo, root)
    expected_krt = resolved.get("kicad_routing_tools", {}).get("commit")
    add(checks, "krt-commit", krt_ok and krt_head == expected_krt, f"expected={expected_krt};actual={krt_head}")

    java = shutil.which("java")
    add(checks, "java", bool(java), java or "not found")
    freerouting_resolved = resolved.get("freerouting", {})
    freerouting = root / "tools/freerouting/freerouting-latest.jar"
    freerouting_hash = hashlib.sha256(freerouting.read_bytes()).hexdigest() if freerouting.is_file() else "missing"
    add(checks, "freerouting", freerouting_hash == freerouting_resolved.get("sha256"), f"expected={freerouting_resolved.get('sha256')};actual={freerouting_hash}")

    konnect_resolved = resolved.get("konnect")
    if konnect_resolved:
        konnect = root / "tools/konnect/konnect.exe"
        ok, output = run([str(konnect), "--version"], root) if konnect.is_file() else (False, "missing")
        binary_hash = hashlib.sha256(konnect.read_bytes()).hexdigest() if konnect.is_file() else "missing"
        add(
            checks, "konnect",
            ok
            and output == konnect_resolved.get("binary_version")
            and binary_hash == konnect_resolved.get("binary_sha256"),
            f"version={output};sha256={binary_hash}",
        )

    if args.deep and (root / "scripts/krt_flow.py").is_file():
        ok, output = run([sys.executable, "scripts/krt_flow.py", "check"], root, 120)
        add(checks, "krt-check", ok, output[-300:])
    if args.deep and (houtini / "package.json").is_file():
        npm = ["cmd.exe", "/d", "/s", "/c", "npm", "test"] if os.name == "nt" else ["npm", "test"]
        ok, output = run(npm, houtini, 180)
        add(checks, "houtini-tests", ok, output[-300:])
    if args.deep and wsl and (root / "ato.yaml").is_file():
        windows_root = str(root)
        linux_root = "/mnt/" + windows_root[0].lower() + windows_root[2:].replace("\\", "/") if len(windows_root) > 2 and windows_root[1] == ":" else windows_root
        ok, output = run([wsl, "-d", args.distro, "--", "bash", "-lc", f"cd {json.dumps(linux_root)} && ato --non-interactive build"], root, 300)
        add(checks, "atopile-build", ok, output[-300:])

    required_ok = all(item["ok"] for item in checks if item["required"])
    optional_ok = all(item["ok"] for item in checks if not item["required"])
    print(json.dumps({"ready": required_ok, "optional_ready": optional_ok, "checks": checks}, ensure_ascii=False, separators=(",", ":")))
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
