#!/usr/bin/env python3
"""Configure project MCP paths without downloading, cloning or compiling tools."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/toolchain-policy.json"


def windows_to_wsl(path: Path) -> str:
    value = str(path.resolve())
    if len(value) > 2 and value[1] == ":":
        return f"/mnt/{value[0].lower()}{value[2:].replace(chr(92), '/')}"
    return value.replace("\\", "/")


def configure_board_stack(layers: int) -> None:
    stack_path = ROOT / "config/board-stack.json"
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
    active = "two_layer" if layers == 2 else "four_layer"
    stack["active"] = active
    stack_path.write_text(json.dumps(stack, indent=2) + "\n", encoding="utf-8")

    krt_path = ROOT / "config/kicad-routing-tools.json"
    krt = json.loads(krt_path.read_text(encoding="utf-8"))
    profile = stack["profiles"][active]
    routing = krt["profiles"]["outer_low_risk"]["routing"]
    # KRT's routing layer list is an allow-list. Plane layers must not be
    # included and then merely discouraged with a negative cost.
    signal_layers = [layer for layer in profile["copper_layers"] if layer not in profile["forbidden_signal_layers"]]
    routing["layers"] = signal_layers
    routing["layer_costs"] = [1.0] if len(signal_layers) == 1 else [1.0, 3.0]
    krt["guardrails"]["forbidden_signal_layers"] = profile["forbidden_signal_layers"]
    krt_path.write_text(json.dumps(krt, indent=2) + "\n", encoding="utf-8")


def ensure_kicad_project() -> Path:
    """Create a minimal KiCad project container without inventing board rules."""
    board = ROOT / "elec/layout/default/default.kicad_pcb"
    project = board.with_suffix(".kicad_pro")
    if board.is_file() and not project.exists():
        project.write_text(json.dumps({
            "board": {}, "cvpcb": {}, "erc": {}, "libraries": {},
            "meta": {"filename": project.name, "version": 1}, "net_settings": {},
        }, indent=2) + "\n", encoding="utf-8")
    return project


def ensure_stage_bootstrap(project_name: str, preserve: bool = False) -> Path:
    path = ROOT / "handoff/stages/CURRENT_STAGE.json"
    if not preserve or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "project": project_name,
            "stage": "requirements", "stage_number": 1, "stage_label": "01-requirements", "status": "not_started",
            "objective": "Capture requirements, interfaces, rails, mechanics, and manufacturer choice.",
            "stage_rules": {"required_evidence": ["requirements recorded with explicit unknowns"], "forbidden": ["routing", "manufacturing_export", "inventing electrical or fabrication values"]},
            "open_items": ["Record requirements and explicit unknowns."],
            "next_actions": ["Read config/stage-checklist.json and begin requirements capture."],
            "read_first": ["handoff/stages/CURRENT_STAGE.json"],
            "authoritative_docs": ["AGENTS.md", "config/stage-checklist.json", "config/pcb-acceptance.json", "config/board-stack.json"],
            "completion_verified": False,
            "completion_basis": None,
            "completion_receipts": [],
        }, indent=2) + "\n", encoding="utf-8")
        markdown = path.with_suffix(".md")
        markdown.write_text(
            f"# {project_name} — Stage 01-requirements\n\n"
            "- Status: not_started\n"
            "- Objective: Capture requirements, interfaces, rails, mechanics, and manufacturer choice.\n\n"
            "Machine-readable handoff: `handoff/stages/CURRENT_STAGE.json`\n",
            encoding="utf-8",
        )
    return path


def configure_project(project_name: str, distro: str, force: bool, with_konnect: bool, layers: int, electrical_backend: str, preserve_existing: bool = False) -> str:
    flow_path = ROOT / "config/pcb-flow.json"
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    flow["project"]["name"] = project_name
    if not preserve_existing:
        flow["project"]["board"] = "elec/layout/default/default.kicad_pcb"
    flow["manufacturing"]["output"] = f"manufacturing/jlcpcb/{project_name}-rev-a"
    flow_path.write_text(json.dumps(flow, indent=2) + "\n", encoding="utf-8")
    if not preserve_existing:
        configure_board_stack(layers)
    backend_path = ROOT / "config/electrical-backend.json"
    backend_config = json.loads(backend_path.read_text(encoding="utf-8"))
    if electrical_backend not in backend_config.get("backends", {}):
        raise SystemExit(f"Unknown electrical backend: {electrical_backend}")
    backend_config["active"] = electrical_backend
    backend_path.write_text(json.dumps(backend_config, indent=2) + "\n", encoding="utf-8")
    project_file = ensure_kicad_project()
    stage_file = ensure_stage_bootstrap(project_name, preserve_existing)

    root_windows = str(ROOT)
    root_wsl = windows_to_wsl(ROOT)
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    houtini = ROOT / "tools/houtini-lm/dist/index.js"
    tscircuit_enabled = "true" if electrical_backend == "tscircuit" else "false"
    config = f'''[mcp_servers.atopile]
type = "stdio"
command = 'C:\\Windows\\System32\\wsl.exe'
args = ['-d', '{distro}', '--', 'bash', '-lc', 'cd {root_wsl} && exec ./scripts/start-atopile-mcp-wsl.sh']
cwd = '{root_windows}'
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.tscircuit]
type = "stdio"
command = '{node}'
args = ['{ROOT / ".codex/mcp/tscircuit-server.mjs"}']
cwd = '{root_windows}'
enabled = {tscircuit_enabled}
env_vars = ["PCB_PYTHON", "BUN_BINARY"]
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
HOUTINI_LM_MODEL = "deepseek-v4-flash"
HOUTINI_LM_ORCHESTRATOR = "codex"
HOUTINI_LM_DEEPSEEK_THINKING = "disabled"
HOUTINI_LM_AUTO_MAX_TOKENS = "800"
HOUTINI_LM_MAX_INPUT_CHARS = "24000"
HOUTINI_LM_RESPONSE_METADATA = "none"
HOUTINI_LM_ALLOWED_ROOTS = '{root_windows}'
'''
    if with_konnect:
        konnect = ROOT / "tools/konnect/konnect.exe"
        konnect_config = ROOT / ".codex/konnect/config.toml"
        konnect_config.parent.mkdir(parents=True, exist_ok=True)
        konnect_config.write_text(f'''kicad_cli = 'C:\\Program Files\\KiCad\\10.0\\bin\\kicad-cli.exe'
kicad_binary = 'C:\\Program Files\\KiCad\\10.0\\bin\\kicad.exe'
project_dir = '{root_windows}'
ipc_address = ''
transport = 'stdio'
jlcpcb_db_path = '{ROOT / "build/00-shared/tmp/konnect/jlcpcb.sqlite"}'
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
    if target.exists() and not force and target.read_text(encoding="utf-8") != config:
        fragment = ROOT / ".codex/pcb-mcp.fragment.toml"
        fragment.write_text(config, encoding="utf-8")
        return f"Existing config preserved; merge {fragment}"
    target.write_text(config, encoding="utf-8")
    return f"Wrote {target}; ensured {project_file} and {stage_file}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default=ROOT.name)
    parser.add_argument("--distro", default="Ubuntu-24.04")
    parser.add_argument("--electrical-backend", default="atopile", help="Backend from config/electrical-backend.json; atopile is the production default and tscircuit is experimental.")
    parser.add_argument("--force-config", action="store_true")
    parser.add_argument("--preserve-existing", action="store_true", help="Keep the existing handoff, active board path, and board stack when reconfiguring a live project.")
    parser.add_argument("--check", action="store_true", help="Run the local readiness check after configuration.")
    parser.add_argument("--layers", type=int, choices=(2, 4), default=2, help="New-project copper layer preset; no fabrication values are implied.")
    konnect_group = parser.add_mutually_exclusive_group()
    konnect_group.add_argument("--with-konnect", dest="konnect", action="store_true")
    konnect_group.add_argument("--no-konnect", dest="konnect", action="store_false")
    parser.set_defaults(konnect=None)
    args = parser.parse_args()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    with_konnect = bool(policy["konnect"].get("enabled_by_default", True)) if args.konnect is None else args.konnect
    message = configure_project(args.project_name, args.distro, args.force_config, with_konnect, args.layers, args.electrical_backend, args.preserve_existing)

    check_exit = None
    if args.check:
        check_exit = subprocess.run([
            sys.executable, str(ROOT / "scripts/check_pcb_environment.py"),
            "--project-root", str(ROOT), "--distro", args.distro,
        ], check=False).returncode
    print(json.dumps({
        "success": True,
        "project": args.project_name,
        "configured_konnect": with_konnect,
        "electrical_backend": args.electrical_backend,
            "layers": args.layers,
        "message": message,
        "check_exit": check_exit,
        "next": [
            "Install the tools manually following check_pcb_environment.py suggestions.",
            "Run python scripts/record_toolchain_receipt.py after manual installation.",
            "Run python scripts/check_pcb_environment.py --deep, then restart Codex.",
        ],
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
