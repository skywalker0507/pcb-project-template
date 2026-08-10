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
    parser.add_argument("--distro", default="Ubuntu-24.04")
    parser.add_argument("--tier", choices=("minimal", "standard", "deep"), default="standard")
    parser.add_argument("--deep", action="store_true", help="Deprecated alias for --tier deep.")
    args = parser.parse_args()
    tier = "deep" if args.deep else args.tier
    root = args.project_root.resolve()
    checks: list[dict[str, object]] = []
    policy_path = root / "config/toolchain-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
    resolved_path = root / "build/00-shared/evidence/toolchain/toolchain-resolved.json"
    resolved = json.loads(resolved_path.read_text(encoding="utf-8")) if resolved_path.is_file() else {}
    backend_path = root / "config/electrical-backend.json"
    backend_config = json.loads(backend_path.read_text(encoding="utf-8")) if backend_path.is_file() else {}
    active_backend = backend_config.get("active")
    active_backend_config = (backend_config.get("backends") or {}).get(active_backend, {})
    if not isinstance(active_backend, str) or not isinstance(active_backend_config, dict):
        active_backend = "invalid"
        active_backend_config = {}

    for relative in ("config/electrical-backend.json", "config/backend-architecture.json", "config/physical-backend.json", "config/manufacturing-backend.json", "config/pcb-flow.json", "config/kicad-routing-tools.json", "config/toolchain-policy.json", "config/board-stack.json", "config/pcb-acceptance.json", "config/requirements.json", "handoff/stages/CURRENT_STAGE.json"):
        add(checks, f"project:{relative}", (root / relative).is_file(), relative)
    source_root = active_backend_config.get("source_root")
    entry = active_backend_config.get("entry")
    source_ok = isinstance(source_root, str) and isinstance(entry, str) and (root / source_root).exists()
    if active_backend == "tscircuit" and source_ok:
        source_ok = (root / source_root / "package.json").is_file() and (root / source_root / entry).is_file()
    elif active_backend == "atopile" and source_ok:
        entry_file = entry.split(":", 1)[0]
        source_ok = (root / source_root / entry_file).is_file() and (root / "ato.yaml").is_file()
    add(checks, "project:active-electrical-source", source_ok, f"backend={active_backend};source={source_root};entry={entry}")
    add(checks, "project:resolved-toolchain", resolved_path.is_file(), str(resolved_path))
    mcp_configured = (root / ".codex/config.toml").is_file() or (root / ".codex/pcb-mcp.fragment.toml").is_file()
    add(checks, "project:mcp-config", mcp_configured, ".codex/config.toml or merge fragment")

    kicad = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
    add(checks, "kicad-cli", kicad.is_file(), str(kicad))
    add(checks, "git", shutil.which("git") is not None, shutil.which("git") or "not found")
    add(checks, "python", sys.version_info >= (3, 10), sys.version.split()[0])

    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if active_backend != "atopile":
        add(checks, "wsl", True, "not selected", required=False)
        add(checks, "wsl-atopile", True, "not selected", required=False)
    elif tier == "minimal":
        add(checks, "wsl", bool(wsl), wsl or "WSL unavailable", required=False)
        add(checks, "wsl-atopile", True, "not probed at minimal tier", required=False)
    elif wsl:
        add(checks, "wsl", True, wsl, required=True)
        ok, output = run([wsl, "-d", args.distro, "--", "bash", "-lc", "command -v uv && command -v ato && ato --version | tail -n 1"], root)
        actual_ato = output.replace("\x00", "").strip().splitlines()[-1] if output.replace("\x00", "").strip().splitlines() else "missing"
        expected_ato = resolved.get("atopile", {}).get("version")
        add(checks, "wsl-atopile", ok and actual_ato == expected_ato, f"expected={expected_ato};actual={actual_ato}", required=active_backend == "atopile")
    else:
        add(checks, "wsl", False, "WSL unavailable", required=True)
        add(checks, "wsl-atopile", False, "WSL unavailable", required=active_backend == "atopile")

    node = shutil.which("node")
    add(checks, "node", bool(node), node or "not found")
    bun = shutil.which("bun")
    tscircuit_root = root / "tscircuit"
    add(checks, "bun", bool(bun), bun or "not found", required=active_backend == "tscircuit")
    add(checks, "tscircuit-lockfile", (tscircuit_root / "bun.lock").is_file(), str(tscircuit_root / "bun.lock"), required=active_backend == "tscircuit")
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
    expected_freerouting_hash = str(freerouting_resolved.get("sha256", "")).lower()
    add(checks, "freerouting", freerouting_hash.lower() == expected_freerouting_hash, f"expected={freerouting_resolved.get('sha256')};actual={freerouting_hash}")

    konnect_resolved = resolved.get("konnect")
    konnect_disabled = isinstance(konnect_resolved, dict) and konnect_resolved.get("disabled") is True
    if not konnect_disabled and (konnect_resolved or policy.get("konnect", {}).get("enabled_by_default", False)):
        konnect = root / "tools/konnect/konnect.exe"
        ok, output = run([str(konnect), "--version"], root) if konnect.is_file() else (False, "missing")
        binary_hash = hashlib.sha256(konnect.read_bytes()).hexdigest() if konnect.is_file() else "missing"
        add(
            checks, "konnect",
            bool(konnect_resolved)
            and ok
            and output == konnect_resolved.get("binary_version")
            and binary_hash.lower() == str(konnect_resolved.get("binary_sha256", "")).lower(),
            f"version={output};sha256={binary_hash}",
        )

    if tier == "deep" and (root / "scripts/krt_flow.py").is_file():
        ok, output = run([sys.executable, "scripts/krt_flow.py", "check"], root, 120)
        add(checks, "krt-check", ok, output[-300:])
    if tier == "deep" and (houtini / "package.json").is_file():
        npm = ["cmd.exe", "/d", "/s", "/c", "npm", "test"] if os.name == "nt" else ["npm", "test"]
        ok, output = run(npm, houtini, 180)
        add(checks, "houtini-tests", ok, output[-300:])
    if tier == "deep" and active_backend == "atopile" and wsl and (root / "ato.yaml").is_file():
        ok, output = run([
            sys.executable, "scripts/build_electrical.py", "--backend", "atopile", "--build", "default",
        ], root, 300)
        add(checks, "atopile-build", ok, output[-300:])
    if tier == "deep" and active_backend == "tscircuit" and bun and tscircuit_root.is_dir():
        ok, output = run([bun, "run", "typecheck"], tscircuit_root, 180)
        add(checks, "tscircuit-typecheck", ok, output[-300:])

    required_by_tier = {
        "minimal": {
            "project:config/electrical-backend.json", "project:config/backend-architecture.json", "project:config/physical-backend.json", "project:config/manufacturing-backend.json", "project:active-electrical-source", "project:config/pcb-flow.json", "project:config/kicad-routing-tools.json",
            "project:config/toolchain-policy.json", "project:config/board-stack.json", "project:config/pcb-acceptance.json", "project:config/requirements.json",
            "project:handoff/stages/CURRENT_STAGE.json", "project:mcp-config", "python",
        },
        "standard": {
            "project:config/electrical-backend.json", "project:config/backend-architecture.json", "project:config/physical-backend.json", "project:config/manufacturing-backend.json", "project:active-electrical-source", "project:config/pcb-flow.json", "project:config/kicad-routing-tools.json",
            "project:config/toolchain-policy.json", "project:config/board-stack.json", "project:config/pcb-acceptance.json", "project:config/requirements.json",
            "project:handoff/stages/CURRENT_STAGE.json", "project:resolved-toolchain", "project:mcp-config",
            "kicad-cli", "git", "python", "node", "houtini-commit",
            "houtini-build", "krt-repository", "krt-python", "krt-commit", "java", "freerouting",
        },
        "deep": {item["name"] for item in checks if item["required"]},
    }
    for item in checks:
        item["required"] = item["name"] in required_by_tier[tier]
    if active_backend == "tscircuit":
        for item in checks:
            if item["name"] in {"bun", "tscircuit-lockfile"}:
                item["required"] = tier in {"standard", "deep"}
    elif active_backend == "atopile":
        for item in checks:
            if item["name"] in {"wsl", "wsl-atopile"}:
                item["required"] = tier in {"standard", "deep"}
    required_ok = all(item["ok"] for item in checks if item["required"])
    optional_ok = all(item["ok"] for item in checks if not item["required"])
    names = {item["name"] for item in checks if not item["ok"]}
    suggestions: list[str] = []
    if "project:mcp-config" in names:
        suggestions.append("Run: python scripts/init_project.py --project-name <name>")
    if "project:resolved-toolchain" in names:
        suggestions.append("After installing tools manually, run: python scripts/record_toolchain_receipt.py")
    if active_backend == "atopile" and ("wsl" in names or "wsl-atopile" in names):
        suggestions.append(f"Install atopile manually in WSL {args.distro}, then verify: wsl.exe -d {args.distro} -- bash -lc 'ato --version'")
    if active_backend == "tscircuit" and ("bun" in names or "tscircuit-lockfile" in names):
        suggestions.append("Install Bun natively, then run bun install in tscircuit/ to restore the locked tscircuit toolchain.")
    if "houtini-commit" in names and not houtini_ok:
        suggestions.append("Clone the configured Houtini fork to tools/houtini-lm.")
    if "houtini-build" in names:
        suggestions.append("Run npm ci and npm run build in tools/houtini-lm.")
    if "krt-repository" in names:
        suggestions.append("Clone the configured KRT fork to tools/KiCadRoutingTools and check out codex/repository-support.")
    if "krt-commit" in names and krt_ok and expected_krt:
        suggestions.append("Check out the KRT commit recorded in build/00-shared/evidence/toolchain/toolchain-resolved.json, or record a receipt for the reviewed local commit.")
    if "krt-python" in names:
        suggestions.append("Create .venv-routingtools, install tools/KiCadRoutingTools/requirements.txt, then run its build_router.py manually.")
    if "freerouting" in names:
        suggestions.append("Download the reviewed FreeRouting JAR manually to tools/freerouting/freerouting-latest.jar.")
    if "konnect" in names:
        suggestions.append("Download Konnect manually to tools/konnect/konnect.exe, or disable it with init_project.py --no-konnect.")
    # Windows consoles can still use a legacy code page; JSON escapes keep this
    # machine-readable even when a tool log includes characters such as emoji.
    print(json.dumps({"tier": tier, "ready": required_ok, "optional_ready": optional_ok, "checks": checks, "manual_suggestions": suggestions}, ensure_ascii=True, separators=(",", ":")))
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
