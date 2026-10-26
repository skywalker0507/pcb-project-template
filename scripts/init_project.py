#!/usr/bin/env python3
"""Configure project MCP paths without downloading, cloning or compiling tools."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import shlex
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
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    board = ROOT / flow["project"]["board"]
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


def initialize_project_records(project_name: str) -> Path:
    """Archive inherited authority before creating explicitly unreviewed inputs."""
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = ROOT / "tmp/project-initialization" / identity
    drafts = {
        "requirements.json": {"schema": 2, "revision": "Rev A", "status": "draft", "interfaces": [], "rails": [], "connectors": [], "mechanical": {}, "constraints": [], "critical_nets": [], "unknowns": ["Record and review this project's requirements before electrical freeze."]},
        "product-decisions.json": {"schema": 1, "project": project_name, "revision": "Rev A", "status": "draft", "decisions": {}, "invariants": []},
        "selected-parts.json": {"schema": 1, "revision": "Rev A", "status": "draft", "footprint_and_polarity_review": {"status": "pending", "reviewed_refs": []}, "parts": []},
        "footprint-provenance.json": {"schema": 1, "status": "draft", "parts": {}},
        "design-data.json": {"schema": 1, "revision": "Rev A", "status": "draft", "project": project_name, "controller": {}, "connectors": []},
        "jlcpcb-parts.json": {"schema": 1, "revision": "Rev A", "status": "draft", "board_sha256": None, "dnp_refs": [], "parts": []},
    }
    pointers = [*ROOT.glob("build/*/CURRENT.json"), ROOT / "build/WORKSET.json", ROOT / "build/WORKSET.md"]
    paths = [*(ROOT / "config" / name for name in drafts), ROOT / "HARDWARE_SPEC.md",
             ROOT / "config/project-initialization.json", ROOT / "handoff/stages/CURRENT_STAGE.json",
             ROOT / "handoff/stages/CURRENT_STAGE.md", *pointers]
    records = []
    # Complete the archive before changing any authority or adoption pointer.
    for path in paths:
        if not path.is_file():
            continue
        destination = archive / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if hashlib.sha256(destination.read_bytes()).hexdigest().upper() != digest:
            raise RuntimeError(f"Project initialization archive failed verification: {path}")
        records.append({"path": path.relative_to(ROOT).as_posix(), "sha256": digest})
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "archive.json").write_text(json.dumps({"schema": 1, "project": project_name, "files": records}, indent=2)+"\n", encoding="utf-8")
    for name, payload in drafts.items():
        payload["project"] = project_name
        (ROOT / "config" / name).write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
    (ROOT / "HARDWARE_SPEC.md").write_text(f"# {project_name}\n\nStatus: draft. Requirements and unknowns must be recorded and reviewed.\n", encoding="utf-8")
    for path in pointers:
        if path.is_file():
            path.unlink()
    (ROOT / "config/project-initialization.json").write_text(json.dumps({
        "schema": 1, "project": project_name, "initialization_id": identity,
        "archive": archive.relative_to(ROOT).as_posix(),
        "status": "draft", "inherited_artifacts_are_authority": False,
        "remaining_project_inputs": "Review project-specific placement, routing and manufacturing inputs before advancing their stages. Existing CAD and release files are preserved as historical artifacts.",
    }, indent=2)+"\n", encoding="utf-8")
    return archive


def configure_project(project_name: str | None, distro: str | None, force: bool, with_konnect: bool, layers: int, electrical_backend: str | None, preserve_existing: bool = True) -> str:
    flow_path = ROOT / "config/pcb-flow.json"
    flow = json.loads(flow_path.read_text(encoding="utf-8"))
    current_name = flow["project"]["name"]
    project_name = project_name or current_name
    if not preserve_existing and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", project_name):
        raise SystemExit("New project name must use letters, numbers, dots, dashes or underscores")
    if preserve_existing and project_name != current_name:
        raise SystemExit("Changing project identity requires --new-project; default reconfiguration preserves reviewed project inputs.")
    backend_path = ROOT / "config/electrical-backend.json"
    backend_config = json.loads(backend_path.read_text(encoding="utf-8"))
    electrical_backend = electrical_backend or backend_config["active"]
    if electrical_backend not in backend_config.get("backends", {}):
        raise SystemExit(f"Unknown electrical backend: {electrical_backend}")
    selected = backend_config["backends"][electrical_backend]
    distro = distro or selected.get("distro", "Ubuntu-24.04")
    if not preserve_existing:
        initialize_project_records(project_name)
    flow["project"]["name"] = project_name
    if not preserve_existing:
        flow["project"]["board"] = "elec/layout/default/default.kicad_pcb"
        flow["manufacturing"]["output"] = f"manufacturing/jlcpcb/{project_name}-rev-a"
        flow_path.write_text(json.dumps(flow, indent=2) + "\n", encoding="utf-8")
        configure_board_stack(layers)
    backend_changed = backend_config["active"] != electrical_backend
    if selected.get("platform") == "wsl-native" and selected.get("distro") != distro:
        selected["distro"] = distro
        backend_changed = True
    if backend_changed:
        backend_config["active"] = electrical_backend
        backend_path.write_text(json.dumps(backend_config, indent=2) + "\n", encoding="utf-8")
    project_file = ensure_kicad_project()
    stage_file = ensure_stage_bootstrap(project_name, preserve_existing)

    root_windows = str(ROOT)
    root_wsl = windows_to_wsl(ROOT)
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    tscircuit_enabled = "true" if electrical_backend == "tscircuit" else "false"
    shell_command = json.dumps(f"cd {shlex.quote(root_wsl)} && exec ./scripts/start-atopile-mcp-wsl.sh")
    config = f'''[mcp_servers.atopile]
type = "stdio"
command = 'C:\\Windows\\System32\\wsl.exe'
args = ['-d', {json.dumps(distro)}, '--', 'bash', '-lc', {shell_command}]
cwd = {json.dumps(root_windows)}
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.tscircuit]
type = "stdio"
command = {json.dumps(node)}
args = [{json.dumps(str(ROOT / ".codex/mcp/tscircuit-server.mjs"))}]
cwd = {json.dumps(root_windows)}
enabled = {tscircuit_enabled}
env_vars = ["PCB_PYTHON", "BUN_BINARY"]
startup_timeout_sec = 30
tool_timeout_sec = 300

'''
    if with_konnect:
        konnect = ROOT / "tools/konnect/konnect.exe"
        konnect_config = ROOT / ".codex/konnect/config.toml"
        konnect_config.parent.mkdir(parents=True, exist_ok=True)
        konnect_config.write_text(f'''kicad_cli = 'C:\\Program Files\\KiCad\\10.0\\bin\\kicad-cli.exe'
kicad_binary = 'C:\\Program Files\\KiCad\\10.0\\bin\\kicad.exe'
project_dir = {json.dumps(root_windows)}
ipc_address = ''
transport = 'stdio'
jlcpcb_db_path = {json.dumps(str(ROOT / "build/00-shared/tmp/konnect/jlcpcb.sqlite"))}
log_level = 'warn'
''', encoding="utf-8")
        config += f'''
[mcp_servers.konnect]
type = "stdio"
command = {json.dumps(sys.executable)}
args = [{json.dumps(str(ROOT / "scripts/start_konnect_mcp.py"))}, {json.dumps(str(konnect))}, '--config', {json.dumps(str(konnect_config))}]
cwd = {json.dumps(root_windows)}
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
    parser.add_argument("--project-name", help="Defaults to the configured project identity")
    parser.add_argument("--distro", help="Defaults to the configured backend distro, then Ubuntu-24.04")
    parser.add_argument("--electrical-backend", help="Defaults to the configured backend; atopile is the production backend.")
    parser.add_argument("--force-config", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--new-project", action="store_true", help="Archive inherited authority, create draft project inputs, and reset adoption pointers. CAD/release files are preserved.")
    mode.add_argument("--preserve-existing", action="store_true", help="Explicit alias for the default safe reconfiguration mode.")
    parser.add_argument("--check", action="store_true", help="Run the local readiness check after configuration.")
    parser.add_argument("--layers", type=int, choices=(2, 4), help="New-project copper layer preset (default 2); requires --new-project.")
    konnect_group = parser.add_mutually_exclusive_group()
    konnect_group.add_argument("--with-konnect", dest="konnect", action="store_true")
    konnect_group.add_argument("--no-konnect", dest="konnect", action="store_false")
    parser.set_defaults(konnect=None)
    args = parser.parse_args()
    if args.layers and not args.new_project:
        parser.error("--layers requires --new-project; reconfiguration preserves the reviewed board stack")
    if args.new_project and not args.project_name:
        parser.error("--new-project requires --project-name")
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    with_konnect = bool(policy["konnect"].get("enabled_by_default", True)) if args.konnect is None else args.konnect
    message = configure_project(args.project_name, args.distro, args.force_config, with_konnect, args.layers or 2, args.electrical_backend, not args.new_project)
    configured_flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    configured_backend = json.loads((ROOT / "config/electrical-backend.json").read_text(encoding="utf-8"))
    configured_stack = json.loads((ROOT / "config/board-stack.json").read_text(encoding="utf-8"))
    distro = configured_backend["backends"][configured_backend["active"]].get("distro", args.distro or "Ubuntu-24.04")

    check_exit = None
    if args.check:
        check_exit = subprocess.run([
            sys.executable, str(ROOT / "scripts/check_pcb_environment.py"),
            "--project-root", str(ROOT), "--distro", distro,
        ], check=False).returncode
    print(json.dumps({
        "success": check_exit in (None, 0),
        "project": configured_flow["project"]["name"],
        "configured_konnect": with_konnect,
        "electrical_backend": configured_backend["active"],
        "board_stack": configured_stack["active"],
        "mode": "new_project" if args.new_project else "reconfigure",
        "message": message,
        "check_exit": check_exit,
        "next": [
            "Install the tools manually following check_pcb_environment.py suggestions.",
            "Run python scripts/record_toolchain_receipt.py after manual installation.",
            "Run python scripts/check_pcb_environment.py --deep, then restart Codex.",
        ],
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if check_exit in (None, 0) else check_exit


if __name__ == "__main__":
    raise SystemExit(main())
