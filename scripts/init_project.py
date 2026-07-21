#!/usr/bin/env python3
"""Install the latest compatible AI PCB toolchain and record what resolved."""

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
import zipfile


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/toolchain-policy.json"
RESOLVED_PATH = ROOT / "build/toolchain-resolved.json"


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


def download(url: str, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".download")
    request = urllib.request.Request(url, headers={"User-Agent": "pcb-project-template"})
    with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
    temporary.replace(destination)
    return actual


def install_konnect(policy: dict[str, object]) -> dict[str, str]:
    release = fetch_json(str(policy["latest_release_api"]))
    suffix = str(policy["windows_asset_suffix"])
    assets = release.get("assets", [])
    asset = next((item for item in assets if item.get("name", "").endswith(suffix)), None)
    if not asset:
        raise SystemExit(f"Latest Konnect release has no *{suffix} asset")

    install_dir = ROOT / "tools/konnect"
    archive = install_dir / "konnect-latest.zip"
    archive_hash = download(asset["browser_download_url"], archive)
    with zipfile.ZipFile(archive) as package:
        executable = next(
            (item for item in package.infolist() if Path(item.filename).name.lower() == "konnect.exe"),
            None,
        )
        if executable is None:
            raise SystemExit("Konnect release archive contains no konnect.exe")
        target = install_dir / "konnect.exe"
        with package.open(executable) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
    archive.unlink()
    version = run([str(target), "--version"])
    binary_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "version": str(release["tag_name"]),
        "binary_version": version,
        "asset": asset["name"],
        "asset_sha256": archive_hash,
        "binary_sha256": binary_hash,
    }


def update_checkout(name: str, repository: str, ref: str, destination: Path) -> str:
    if destination.exists():
        if not (destination / ".git").exists():
            raise SystemExit(f"{name} destination is not a Git checkout: {destination}")
        dirty = run(["git", "-C", str(destination), "status", "--porcelain"])
        if dirty:
            raise SystemExit(f"Refusing to change dirty {name} checkout: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", repository, str(destination)])
    run(["git", "-C", str(destination), "fetch", "origin", ref])
    commit = run(["git", "-C", str(destination), "rev-parse", "FETCH_HEAD"])
    run(["git", "-C", str(destination), "checkout", "--detach", commit])
    return commit


def fetch_json(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": "pcb-project-template"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def atopile_version(distro: str) -> str:
    return run([
        "wsl.exe", "-d", distro, "--", "bash", "-lc",
        "export PATH=\"$HOME/.local/bin:$PATH\"; ato --version | tail -n 1",
    ]).splitlines()[-1].strip()


def update_ato_requirement(version: str) -> None:
    path = ROOT / "ato.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    replacement = f'requires-atopile: ">={version}"'
    if lines and lines[0].startswith("requires-atopile:"):
        lines[0] = replacement
    else:
        lines.insert(0, replacement)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def configure_project(project_name: str, distro: str, force: bool, with_konnect: bool) -> None:
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
    if with_konnect:
        konnect = ROOT / "tools/konnect/konnect.exe"
        konnect_config = ROOT / "build/konnect/config.toml"
        konnect_config.parent.mkdir(parents=True, exist_ok=True)
        konnect_config.write_text(f'''kicad_cli = 'C:\\Program Files\\KiCad\\10.0\\bin\\kicad-cli.exe'
kicad_binary = 'C:\\Program Files\\KiCad\\10.0\\bin\\kicad.exe'
project_dir = '{root_windows}'
ipc_address = ''
transport = 'stdio'
jlcpcb_db_path = '{ROOT / "build/konnect/jlcpcb.sqlite"}'
log_level = 'warn'
''', encoding="utf-8")
        config += f'''
[mcp_servers.konnect]
type = "stdio"
command = '{sys.executable}'
args = ['{ROOT / "scripts/start_konnect_mcp.py"}', '{konnect}', '--config', '{konnect_config}']
cwd = '{root_windows}'
enabled = true
env_vars = ["KICAD_API_SOCKET"]
startup_timeout_sec = 30
tool_timeout_sec = 300
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
    konnect_group = parser.add_mutually_exclusive_group()
    konnect_group.add_argument(
        "--with-konnect", dest="konnect", action="store_true",
        help="install Konnect even if project policy disables it",
    )
    konnect_group.add_argument(
        "--no-konnect", dest="konnect", action="store_false",
        help="do not install the default Konnect replacement for KiCAD-MCP-Server",
    )
    parser.set_defaults(konnect=None)
    parser.add_argument("--force-config", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    want_konnect = (
        bool(policy["konnect"].get("enabled_by_default", True))
        if args.konnect is None else args.konnect
    )
    resolved: dict[str, object] = {"schema": 1, "mode": "latest"}

    if not args.no_wsl:
        run([
            "wsl.exe", "-d", args.distro, "--", "bash",
            f"{windows_to_wsl(ROOT)}/scripts/bootstrap-wsl.sh",
        ])
    ato_version = atopile_version(args.distro)
    update_ato_requirement(ato_version)
    resolved["atopile"] = {"version": ato_version}

    if not args.no_tools:
        houtini_policy = policy["houtini_lm"]
        houtini = ROOT / "tools/houtini-lm"
        houtini_commit = update_checkout(
            "Houtini-LM", houtini_policy["repository"], houtini_policy["ref"], houtini,
        )
        if not (shutil.which("npm.cmd") or shutil.which("npm")):
            raise SystemExit("Node.js/npm 18+ is required")
        run(npm_command("ci"), cwd=houtini)
        run(npm_command("run", "build"), cwd=houtini)
        houtini_version = json.loads((houtini / "package.json").read_text(encoding="utf-8"))["version"]
        resolved["houtini_lm"] = {"commit": houtini_commit, "version": houtini_version}

        krt_policy = policy["kicad_routing_tools"]
        krt = ROOT / "tools/KiCadRoutingTools"
        krt_commit = update_checkout(
            "KiCadRoutingTools", krt_policy["repository"], krt_policy["ref"], krt,
        )
        venv = ROOT / ".venv-routingtools"
        if not (venv / "Scripts/python.exe").is_file():
            run([sys.executable, "-m", "venv", str(venv)])
        router_python = str(venv / "Scripts/python.exe")
        run([router_python, "-m", "pip", "install", "-r", str(krt / "requirements.txt")])
        run([router_python, str(krt / "build_router.py")])

        krt_config_path = ROOT / "config/kicad-routing-tools.json"
        krt_config = json.loads(krt_config_path.read_text(encoding="utf-8"))
        if krt_config["tool"].get("commit") != krt_commit:
            krt_config["tool"]["commit"] = krt_commit
            krt_config_path.write_text(json.dumps(krt_config, indent=2) + "\n", encoding="utf-8")
        resolved["kicad_routing_tools"] = {"commit": krt_commit}

        freerouting_policy = policy["freerouting"]
        release = fetch_json(freerouting_policy["latest_release_api"])
        suffix = freerouting_policy["jar_asset_suffix"]
        assets = release.get("assets", [])
        asset = next((item for item in assets if item.get("name", "").endswith(suffix)), None)
        if not asset:
            raise SystemExit(f"Latest FreeRouting release has no {suffix} asset")
        freerouting_path = ROOT / "tools/freerouting/freerouting-latest.jar"
        freerouting_hash = download(asset["browser_download_url"], freerouting_path)
        resolved["freerouting"] = {
            "version": release["tag_name"],
            "asset": asset["name"],
            "sha256": freerouting_hash,
        }

        if want_konnect:
            resolved["konnect"] = install_konnect(policy["konnect"])

    RESOLVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESOLVED_PATH.write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")

    konnect_enabled = want_konnect and (ROOT / "tools/konnect/konnect.exe").is_file()
    configure_project(args.project_name, args.distro, args.force_config, konnect_enabled)
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
