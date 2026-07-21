#!/usr/bin/env python3
"""Initialize the pinned AI PCB toolchain on Windows with WSL atopile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config/toolchain-lock.json"


def run(command: list[str], *, cwd: Path = ROOT, timeout: int = 600) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if result.returncode:
        tail = result.stdout[-4000:]
        raise SystemExit(f"Command failed ({result.returncode}): {command!r}\n{tail}")
    return result.stdout.strip()


def windows_to_wsl(path: Path) -> str:
    value = str(path.resolve())
    if len(value) > 2 and value[1] == ":":
        return f"/mnt/{value[0].lower()}{value[2:].replace(chr(92), '/')}"
    return value.replace("\\", "/")


def npm_command(*arguments: str) -> list[str]:
    if os.name == "nt":
        return ["cmd.exe", "/d", "/s", "/c", "npm", *arguments]
    return ["npm", *arguments]


def install_download(url: str, sha256: str, destination: Path) -> None:
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == sha256:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".download")
    request = urllib.request.Request(url, headers={"User-Agent": "pcb-project-template"})
    with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
    if actual != sha256:
        temporary.unlink(missing_ok=True)
        raise SystemExit(f"Download hash mismatch for {destination}: {actual}")
    temporary.replace(destination)


def checkout(name: str, repository: str, commit: str, destination: Path) -> None:
    if destination.exists():
        if not (destination / ".git").exists():
            raise SystemExit(f"{name} destination is not a Git checkout: {destination}")
        dirty = run(["git", "-C", str(destination), "status", "--porcelain"])
        if dirty:
            raise SystemExit(f"Refusing to change dirty {name} checkout: {destination}")
        current = run(["git", "-C", str(destination), "rev-parse", "HEAD"])
        if current == commit:
            return
        run(["git", "-C", str(destination), "fetch", "origin", commit])
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", repository, str(destination)])
        run(["git", "-C", str(destination), "fetch", "origin", commit])
    run(["git", "-C", str(destination), "checkout", "--detach", commit])


def configure_project(project_name: str, distro: str, force: bool) -> None:
    flow_path = ROOT / "config/pcb-flow.json"
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["project"]["name"] = project_name
    flow["manufacturing"]["output"] = f"manufacturing/jlcpcb/{project_name}-rev-a"
    flow_path.write_text(json.dumps(flow, indent=2) + "\n", encoding="utf-8")

    root_windows = str(ROOT)
    root_wsl = windows_to_wsl(ROOT)
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    houtini = ROOT / "tools/houtini-lm/dist/index.js"
    config = f'''[mcp_servers.atopile]
type = "stdio"
command = 'C:\\Windows\\System32\\wsl.exe'
args = ['-d', '{distro}', '--', 'bash', '-lc', 'cd {root_wsl} && exec ./scripts/start-atopile-mcp-wsl.sh']
cwd = '{root_windows}'
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.houtini-lm]
type = "stdio"
command = '{node}'
args = ['{houtini}']
cwd = '{root_windows}'
enabled = true
enabled_tools = ["delegate"]
env_vars = ["HOUTINI_LM_API_KEY", "DEEPSEEK_API_KEY"]
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.houtini-lm.env]
HOUTINI_LM_ENDPOINT_URL = "https://api.deepseek.com"
HOUTINI_LM_PROVIDER = "deepseek"
HOUTINI_LM_MODEL = "deepseek-v4-pro"
HOUTINI_LM_ORCHESTRATOR = "codex"
HOUTINI_LM_DEEPSEEK_THINKING = "auto"
HOUTINI_LM_AUTO_MAX_TOKENS = "6000"
HOUTINI_LM_MAX_INPUT_CHARS = "120000"
HOUTINI_LM_RESPONSE_METADATA = "none"
HOUTINI_LM_ALLOWED_ROOTS = '{root_windows}'
'''
    target = ROOT / ".codex/config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        existing = target.read_text(encoding="utf-8")
        if existing != config:
            fragment = ROOT / ".codex/pcb-mcp.fragment.toml"
            fragment.write_text(config, encoding="utf-8")
            print(f"Existing config preserved; merge {fragment}")
            return
    target.write_text(config, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default=ROOT.name)
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--no-wsl", action="store_true")
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--force-config", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args()
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    if not args.no_wsl:
        run([
            "wsl.exe", "-d", args.distro, "--", "bash",
            f"{windows_to_wsl(ROOT)}/scripts/bootstrap-wsl.sh",
        ])

    if not args.no_tools:
        houtini_lock = lock["houtini_lm"]
        houtini = ROOT / "tools/houtini-lm"
        checkout("Houtini-LM", houtini_lock["repository"], houtini_lock["commit"], houtini)
        if not (shutil.which("npm.cmd") or shutil.which("npm")):
            raise SystemExit("Node.js/npm 18+ is required")
        run(npm_command("ci"), cwd=houtini)
        run(npm_command("run", "build"), cwd=houtini)

        krt_lock = lock["kicad_routing_tools"]
        krt = ROOT / "tools/KiCadRoutingTools"
        checkout("KiCadRoutingTools", krt_lock["repository"], krt_lock["commit"], krt)
        venv = ROOT / ".venv-routingtools"
        if not (venv / "Scripts/python.exe").is_file():
            run([sys.executable, "-m", "venv", str(venv)])
        router_python = str(venv / "Scripts/python.exe")
        run([router_python, "-m", "pip", "install", "-r", str(krt / "requirements.txt")])
        run([
            router_python,
            str(krt / "build_router.py"),
            "--tag",
            krt_lock["native_release"],
        ])

        krt_config_path = ROOT / "config/kicad-routing-tools.json"
        krt_config = json.loads(krt_config_path.read_text(encoding="utf-8"))
        krt_config["tool"]["commit"] = krt_lock["commit"]
        krt_config_path.write_text(json.dumps(krt_config, indent=2) + "\n", encoding="utf-8")

        freerouting = lock["freerouting"]
        install_download(
            freerouting["url"],
            freerouting["sha256"],
            ROOT / f"tools/freerouting/freerouting-{freerouting['version']}.jar",
        )

    configure_project(args.project_name, args.distro, args.force_config)
    check_exit = None
    if not args.skip_check:
        check_exit = subprocess.run([
            sys.executable, str(ROOT / "scripts/check_pcb_environment.py"),
            "--project-root", str(ROOT), "--distro", args.distro,
        ]).returncode
    print(json.dumps({
        "success": check_exit in (None, 0),
        "project": args.project_name,
        "root": str(ROOT),
        "check_exit": check_exit,
        "restart_codex": True,
    }, separators=(",", ":")))
    return 0 if check_exit in (None, 0) else check_exit


if __name__ == "__main__":
    raise SystemExit(main())
