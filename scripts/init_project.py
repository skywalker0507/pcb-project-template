#!/usr/bin/env python3
"""Configure a project or preview/apply a guarded framework-script update."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/toolchain-policy.json"
RUNTIME_LOCK = "template-runtime.lock.json"


def content_sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def object_sha(value: object) -> str:
    return content_sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def framework_files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): content_sha(p.read_bytes())
            for p in sorted((root / "scripts").glob("*.py")) if p.is_file()}


def framework_validation_inputs(root: Path) -> dict[str, str]:
    """Bind template test evidence to code and checked configuration, not CAD."""
    paths = set((root / "scripts").glob("*.py"))
    for directory in ("tests", "config", "template-engine", ".codex/mcp", "tscircuit"):
        for parent, directories, names in (root / directory).walk():
            directories[:] = [name for name in directories
                              if name not in {"__pycache__", "node_modules", ".venv", "dist", "build"}
                              and not name.endswith(".egg-info")]
            for name in names:
                path = parent / name
                if (directory == "tests" and path.suffix not in {".pyc", ".pyo"}
                        or path.suffix in {".py", ".json", ".toml", ".txt", ".ts", ".tsx", ".js", ".mjs", ".lock"}):
                    paths.add(path)
    paths.update(root.glob("requirements*.txt"))
    if (root / RUNTIME_LOCK).is_file():
        paths.add(root / RUNTIME_LOCK)
    return {p.relative_to(root).as_posix(): content_sha(p.read_bytes())
            for p in sorted(paths) if p.is_file()}


def engine_binding(root: Path) -> dict[str, object]:
    contract = root / "config/template-engine.json"
    value = json.loads(contract.read_text(encoding="utf-8"))
    binding: dict[str, object] = {"contract_sha256": content_sha(contract.read_bytes())}
    if value["source"]["type"] == "path":
        package = (root / value["source"]["path"]).resolve()
        paths = [package / "pyproject.toml", *sorted((package / "src").rglob("*.py"))]
        binding["files"] = {p.relative_to(package).as_posix(): content_sha(p.read_bytes()) for p in paths}
    return binding


def runtime_inventory(root: Path) -> dict[str, object]:
    bundle = {"files": framework_files(root), "engine": engine_binding(root)}
    return {"schema": 1, "kind": "template_runtime_lock", **bundle, "bundle_sha256": object_sha(bundle)}


def safe_runtime_path(root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if relative != RUNTIME_LOCK and (len(parts) != 2 or parts[0] != "scripts"
                                      or not re.fullmatch(r"[\w.-]+\.py", parts[1])):
        raise ValueError(f"Invalid managed runtime path: {relative}")
    path = root / relative
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or parent.is_junction():
            raise ValueError(f"Runtime update refuses linked paths: {path}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Runtime update path escapes project: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Runtime update expected a file: {path}")
    return path


def framework_sync_plan(source: Path, target: Path, validation: Path) -> dict[str, object]:
    source, target = source.resolve(), target.resolve()
    if source == target:
        raise ValueError("Framework source and target must be different projects")
    for name in ("scripts/pcbctl.py", "scripts/session_boot.py", "config/pcb-flow.json"):
        path = safe_runtime_path(target, name) if name.startswith("scripts/") else target / name
        if not path.is_file():
            raise ValueError(f"Target is not an initialized PCB project: {target / name}")
    desired_lock = runtime_inventory(source)
    files = desired_lock["files"]
    if not files:
        raise ValueError("Framework source has no Python scripts")
    for name in files:
        safe_runtime_path(source, name)
    source_engine = desired_lock["engine"]
    compatible = source_engine == engine_binding(target)
    lock_path = safe_runtime_path(target, RUNTIME_LOCK)
    lock_bytes = lock_path.read_bytes() if lock_path.is_file() else None
    previous = json.loads(lock_bytes) if lock_bytes else None
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get("schema") != 1
                or previous.get("kind") != "template_runtime_lock"
                or not isinstance(previous.get("files"), dict)
                or previous.get("bundle_sha256") != object_sha({"files": previous["files"], "engine": previous.get("engine")})):
            raise ValueError("Invalid template runtime lock; do not replace it to hide local changes")
        for name, digest in previous["files"].items():
            if not name.startswith("scripts/"):
                raise ValueError(f"Invalid managed script in runtime lock: {name}")
            safe_runtime_path(target, name)
            if not isinstance(digest, str) or not re.fullmatch(r"[A-F0-9]{64}", digest):
                raise ValueError(f"Invalid runtime lock digest: {name}")
    baseline = previous["files"] if previous else {}
    entries = []
    for name in sorted(files.keys() | baseline.keys()):
        path = safe_runtime_path(target, name)
        current = content_sha(path.read_bytes()) if path.is_file() else None
        expected = files.get(name)
        if expected is None:
            action = "conflict_removed_upstream"
        elif current == expected:
            action = "unchanged"
        elif name in baseline and current == baseline[name]:
            action = "update"
        elif previous is not None and name not in baseline and current is None:
            action = "add"
        else:
            action = "conflict_local_change" if previous else "conflict_untracked_baseline"
        entries.append({"path": name, "action": action, "current_sha256": current, "source_sha256": expected})
    inventory = framework_validation_inputs(source)
    proof = None
    validation_sha = None
    if validation.is_file():
        raw = validation.read_bytes()
        validation_sha = content_sha(raw)
        try:
            proof = json.loads(raw)
        except json.JSONDecodeError:
            pass
    checks = proof.get("checks", []) if isinstance(proof, dict) else []
    required = {"unit-tests", "template-engine", "backend-architecture", "tool-entrypoints"}
    tested = (isinstance(proof, dict) and proof.get("kind") == "template_regression_checks"
              and proof.get("success") is True and proof.get("validation_inputs") == inventory
              and isinstance(checks, list)
              and required <= {item.get("name") for item in checks if isinstance(item, dict) and item.get("success") is True})
    plan = {"schema": 1, "kind": "template_runtime_update_plan", "source": str(source), "target": str(target),
            "engine_compatible": compatible, "validation_current": tested,
            "validation_receipt": str(validation.resolve()), "validation_sha256": validation_sha,
            "lock_sha256": content_sha(lock_bytes) if lock_bytes is not None else None,
            "lock_update": previous != desired_lock, "desired_lock": desired_lock, "files": entries}
    plan["plan_sha256"] = object_sha(plan)
    return plan


def apply_framework_sync(plan: dict[str, object], expected_sha: str) -> dict[str, object]:
    source, target = Path(plan["source"]), Path(plan["target"])
    validation = Path(plan["validation_receipt"])
    current = framework_sync_plan(source, target, validation)
    if expected_sha != plan["plan_sha256"] or current != plan:
        raise ValueError("Framework update plan is stale; preview again before applying")
    if (not plan["engine_compatible"] or not plan["validation_current"]
            or any(item["action"].startswith("conflict") for item in plan["files"])):
        raise ValueError("Framework update blocked by conflicts, engine mismatch or missing current full template tests")
    updates = [item for item in plan["files"] if item["action"] in {"update", "add"}]
    if not updates and not plan["lock_update"]:
        return {"success": True, "updated": 0, "lock_updated": False, "plan_sha256": expected_sha}
    payloads = {item["path"]: (source / item["path"]).read_bytes() for item in updates}
    if any(content_sha(payloads[item["path"]]) != item["source_sha256"] for item in updates):
        raise ValueError("Framework source changed during update preparation")
    payloads[RUNTIME_LOCK] = (json.dumps(plan["desired_lock"], indent=2) + "\n").encode()
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = target / "tmp/framework-update" / identity
    # Resolve and check the backup/evidence lanes before any managed file changes.
    evidence = target / "build/00-shared/evidence" / f"framework-update-{identity}" / "framework-update.json"
    for lane in (backup, evidence.parent):
        if not lane.resolve().is_relative_to(target):
            raise ValueError("Framework update artifact lane escapes project")
    originals = {}
    for name, payload in payloads.items():
        path = safe_runtime_path(target, name)
        originals[name] = path.read_bytes() if path.is_file() else None
        saved = backup / "original" / name
        staged = backup / "staged" / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(payload)
        if originals[name] is not None:
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(originals[name])
            if saved.read_bytes() != originals[name]:
                raise ValueError(f"Framework update backup verification failed: {name}")
    if framework_sync_plan(source, target, validation) != plan:
        raise ValueError("Framework update inputs changed before application; preview again")
    applied = []
    try:
        for name in payloads:
            path = safe_runtime_path(target, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup / "staged" / name, path)
            applied.append(name)
        for name, payload in payloads.items():
            if (target / name).read_bytes() != payload:
                raise ValueError(f"Framework update write verification failed: {name}")
    except BaseException:
        for name in reversed(applied):
            path = safe_runtime_path(target, name)
            if originals[name] is None:
                path.unlink()
            else:
                restored = backup / "staged" / name
                restored.write_bytes(originals[name])
                os.replace(restored, path)
        raise
    receipt = {"schema": 1, "kind": "template_runtime_update", "success": True,
               "plan_sha256": expected_sha, "bundle_sha256": plan["desired_lock"]["bundle_sha256"],
               "updated_files": [item["path"] for item in updates], "lock_updated": bool(plan["lock_update"]),
               "validation_receipt": str(validation), "validation_sha256": plan["validation_sha256"],
               "backup": backup.relative_to(target).as_posix(),
               "limits": "Framework scripts only; no PCB, project authority, freeze or release approval changed."}
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {"success": True, "updated": len(updates), "lock_updated": bool(plan["lock_update"]),
            "plan_sha256": expected_sha, "receipt": str(evidence)}


def sync_framework_cli(target: Path, validation: Path | None, apply: bool, expected_sha: str | None) -> int:
    try:
        plan = framework_sync_plan(ROOT, target, validation or ROOT / "build/00-shared/template-checks/current.json")
        if apply:
            result = apply_framework_sync(plan, expected_sha or "")
        else:
            output = ROOT / "tmp/framework-update" / plan["plan_sha256"] / "plan.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            conflicts = [item["path"] for item in plan["files"] if item["action"].startswith("conflict")]
            result = {"success": not conflicts and plan["engine_compatible"] and plan["validation_current"],
                      "mode": "framework_update_plan", "plan_sha256": plan["plan_sha256"],
                      "bundle_sha256": plan["desired_lock"]["bundle_sha256"], "file_count": len(plan["files"]),
                      "update_count": sum(item["action"] in {"update", "add"} for item in plan["files"]),
                      "lock_update": plan["lock_update"], "engine_compatible": plan["engine_compatible"],
                      "validation_current": plan["validation_current"], "conflict_count": len(conflicts),
                      "conflicts": conflicts[:8], "details": str(output)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"success": False, "error": str(exc)}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["success"] else 1


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
        "project-checks.json": {"schema": 1, "checks": []},
        "requirements.json": {"schema": 2, "revision": "Rev A", "status": "draft", "interfaces": [], "rails": [], "connectors": [], "mechanical": {}, "constraints": [], "critical_nets": [], "placement_regions": [], "controlled_impedance": {}, "unknowns": ["Record and review this project's requirements before electrical freeze."]},
        "product-decisions.json": {"schema": 1, "project": project_name, "revision": "Rev A", "status": "draft", "decisions": {}, "invariants": []},
        "selected-parts.json": {"schema": 1, "revision": "Rev A", "status": "draft", "footprint_and_polarity_review": {"status": "pending", "reviewed_refs": []}, "parts": []},
        "footprint-provenance.json": {"schema": 1, "status": "draft", "parts": {}},
        "design-data.json": {"schema": 1, "revision": "Rev A", "status": "draft", "project": project_name, "controller": {}, "connectors": []},
        "jlcpcb-parts.json": {"schema": 1, "revision": "Rev A", "status": "draft", "board_sha256": None, "dnp_refs": [], "parts": []},
        "placement-semantics.json": {"schema": 1, "status": "draft", "roles": [], "relations": []},
        "placement-routability.json": {"schema": 1, "status": "draft", "weights": {"default": 1, "nets": {}}, "critical_groups": {}, "hard_no_regression_groups": [], "checkpoint": {"require_baseline_comparison": True, "require_critical_probe_receipts": True, "require_unchanged_board_outline": True, "critical_probe_groups": [], "critical_probe_components": {}}},
        "manufacturing-profile.json": {"schema": 1, "status": "draft", "selected": False, "manufacturer": None, "order_profile": {"status": "draft"}},
        "manufacturing-rules.json": {"schema": 2, "status": "draft", "gerber_file_patterns": {}, "gerber_rules": []},
        "net-review-policy.json": {"status": "draft", "rules": []},
        "routing-paths.json": {"status": "draft", "paths": [], "pairs": []},
        "routing-planner.json": {"schema": 1, "status": "draft", "local_direct": {}, "reviewed_multipoint_profiles": {}, "krt_prerequisite_gate": {}, "ordered_scopes": []},
        "power-component-review.json": {"schema": 1, "status": "pending"},
        "pcb-release-baseline.json": {"status": "draft", "reviewed_board_sha256": None, "allowed_violation_items": {}},
        "release-debt-policy.json": {"schema": 1, "status": "draft", "categories": {}},
    }
    pointers = [ROOT / "build/CURRENT.json", *ROOT.glob("build/*/CURRENT.json"), ROOT / "build/03-electrical/BUILD.json",
                ROOT / "build/07-release/INPUTS.json", ROOT / "build/WORKSET.json", ROOT / "build/WORKSET.md"]
    historical_only = ["source-references.json", "usb-esd-placement-evidence.json",
                       "buck-bootstrap-placement-evidence.json", "jlcpcb-impedance-evidence.json",
                       "krt-fab-overrides.txt", "krt-usb-geometry-floor.txt"]
    paths = [*sorted((ROOT / "config").glob("*.json")), *sorted((ROOT / "config").glob("*.txt")), ROOT / "HARDWARE_SPEC.md",
             ROOT / '.pcb-workspace.local.json', ROOT / RUNTIME_LOCK,
             ROOT / "config/project-initialization.json", ROOT / "handoff/stages/CURRENT_STAGE.json",
             ROOT / "handoff/stages/CURRENT_STAGE.md", *pointers]
    paths = list(dict.fromkeys(paths))
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
    for name in historical_only:
        path = ROOT / "config" / name
        if path.is_file():
            path.unlink()
    local_selection = ROOT / '.pcb-workspace.local.json'
    if local_selection.is_file():
        local_selection.unlink()
    for name, payload in drafts.items():
        payload["project"] = project_name
        (ROOT / "config" / name).write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
    krt_path = ROOT / "config/kicad-routing-tools.json"
    krt = json.loads(krt_path.read_text(encoding="utf-8"))
    # Retain only the generic explicit-net router. Reviewed project-specific
    # strategies and fabrication overrides belong to the archived project.
    generic = krt["profiles"]["outer_low_risk"]
    generic["include_nets"] = []
    for key in ("track_width", "clearance", "via_size", "via_drill", "hole_to_hole_clearance", "board_edge_clearance", "fab_overrides"):
        generic["routing"][key] = None
    generic["routing"]["extra_args"] = []
    generic.pop("strategy_evidence", None)
    krt["profiles"] = {"outer_low_risk": generic}
    krt["batch_policy"] = {"mode": "explicit_reviewed_nets", "profiles": ["outer_low_risk"]}
    krt["exploration"] = {"routing_overrides": [], "environment": {}}
    krt_path.write_text(json.dumps(krt, indent=2)+"\n", encoding="utf-8")
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
    if framework_files(ROOT):
        # A new project starts a new software inventory, never a PCB approval.
        safe_runtime_path(ROOT, RUNTIME_LOCK).write_text(json.dumps(runtime_inventory(ROOT), indent=2)+"\n", encoding="utf-8")
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
    preflight_cleared = False
    if not preserve_existing:
        for candidate in backend_config["backends"].values():
            if "source_preflight" in candidate:
                del candidate["source_preflight"]
                preflight_cleared = True
    flow["project"]["name"] = project_name
    if not preserve_existing:
        flow["project"]["board"] = f"elec/layout/{project_name}/{project_name}.kicad_pcb"
        flow["manufacturing"]["output"] = f"manufacturing/jlcpcb/{project_name}-rev-a"
        flow["checks"] = ["scripts/check_template_engine.py", "scripts/check_backend_architecture.py", "scripts/check_tool_entrypoints.py"]
        flow["audit"]["require_in1_ground"] = layers == 4
        initialization = json.loads((ROOT / "config/project-initialization.json").read_text(encoding="utf-8"))
        future_evidence = f"build/07-release/evidence/{initialization['initialization_id']}"
        flow["audit"]["physical_review"] = f"{future_evidence}/physical-review.json"
        flow["audit"]["audit_json"] = f"{future_evidence}/final-routing-audit.json"
        flow["audit"]["drc_report"] = f"{future_evidence}/drc/kicad-cli-drc.rpt"
        flow["manufacturing"]["gate_receipts"] = {
            "electrical_freeze": "build/04-electrical-freeze/release/current/electrical-freeze.json",
            "final_electrical_parity": f"{future_evidence}/final-electrical-parity.json",
            "jlc_dfm": f"{future_evidence}/jlc-dfm.json",
        }
        flow["manufacturing"]["optional_gate_receipts"] = {}
        flow_path.write_text(json.dumps(flow, indent=2) + "\n", encoding="utf-8")
        configure_board_stack(layers)
    backend_changed = backend_config["active"] != electrical_backend or preflight_cleared
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
    mode.add_argument("--sync-framework", type=Path, metavar="PROJECT", help="Preview or apply tested framework scripts to an existing project; preserve project inputs and CAD.")
    update = parser.add_mutually_exclusive_group()
    update.add_argument("--plan", action="store_true", help="Preview --sync-framework without changing the target project (default).")
    update.add_argument("--apply", action="store_true", help="Apply --sync-framework using --expect-plan-sha256.")
    parser.add_argument("--expect-plan-sha256", help="Exact framework update plan digest from the preview.")
    parser.add_argument("--validation-receipt", type=Path, help="Full passing template-check receipt for the current source; defaults to build/00-shared/template-checks/current.json.")
    parser.add_argument("--check", action="store_true", help="Run the local readiness check after configuration.")
    parser.add_argument("--layers", type=int, choices=(2, 4), help="New-project copper layer preset (default 2); requires --new-project.")
    konnect_group = parser.add_mutually_exclusive_group()
    konnect_group.add_argument("--with-konnect", dest="konnect", action="store_true")
    konnect_group.add_argument("--no-konnect", dest="konnect", action="store_false")
    parser.set_defaults(konnect=None)
    args = parser.parse_args()
    if args.sync_framework is not None:
        if any((args.project_name, args.distro, args.electrical_backend, args.force_config,
                args.check, args.layers, args.konnect is not None)):
            parser.error("--sync-framework cannot be combined with project reconfiguration options")
        if args.apply != bool(args.expect_plan_sha256):
            parser.error("--apply and --expect-plan-sha256 must be used together")
        return sync_framework_cli(args.sync_framework, args.validation_receipt, args.apply, args.expect_plan_sha256)
    if any((args.plan, args.apply, args.expect_plan_sha256, args.validation_receipt)):
        parser.error("Framework update options require --sync-framework PROJECT")
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
