#!/usr/bin/env python3
"""Run PCB checks into one stage-scoped artifact run and print compact JSON."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_store import config as artifact_config, create_run, finish_run, publish_stage_artifact, relative
from electrical_backend import read_config as read_electrical_config, selected_backend as selected_electrical_backend
from pcb_policy import load_acceptance, require_no_prohibited_evidence
from release_inputs import adopted_routing_board as resolve_adopted_routing_board, fresh_release_readiness, resolve_release_input
from stage_policy import ROOT, current_stage
from engine_runtime import activate

activate()
from pcb_template_engine import checkpoint  # noqa: E402
from pcb_template_engine.checkpoint import (  # noqa: E402
    canonical_step_path, last_json, sha256, step_files, step_record,
)


def toolchain_module_roots() -> dict[str, list[Path]]:
    policy = ROOT / 'config/toolchain-policy.json'
    if not policy.is_file():
        return {}
    kicad = json.loads(policy.read_text(encoding='utf-8')).get('kicad') or {}
    cli = kicad.get('cli')
    modules = kicad.get('python_modules', [])
    if not isinstance(cli, str) or not isinstance(modules, list):
        return {}
    packages = Path(cli).parent / 'Lib/site-packages'
    result = {}
    for module in modules:
        if not isinstance(module, str) or not module.isidentifier():
            continue
        package, single = packages / module, packages / (module + '.py')
        if package.is_dir() or single.is_file():
            result[module] = [package if package.is_dir() else single, *sorted(packages.glob('*.libs'))]
    return result


def checkpoint_dependencies(board: Path, baseline: Path | None, config_path: Path, freeze: Path | None = None) -> dict[str, str]:
    """Conservatively bind reusable checkpoint evidence to all local producers and policies."""
    paths = [board.resolve(), config_path.resolve()]
    if baseline is not None:
        paths.append(baseline.resolve())
    for pcb in (board, baseline):
        if pcb is not None:
            paths.extend(pcb.with_suffix(suffix) for suffix in (".kicad_pro", ".kicad_dru"))
            paths.append(pcb.parent / "fp-lib-table")
    paths.extend(sorted((ROOT / "scripts").glob("*.py")))
    paths.extend(sorted((ROOT / "config").glob("*.json")))
    paths.extend(sorted((ROOT / "template-engine/src").rglob("*.py")))
    for roots in toolchain_module_roots().values():
        for path in roots:
            paths.extend(p for p in (path.rglob('*') if path.is_dir() else [path])
                         if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'})
    for optional in (ROOT / "template-engine/pyproject.toml", ROOT / "build/00-shared/evidence/toolchain/toolchain-resolved.json"):
        if optional.is_file():
            paths.append(optional)
    # Native integrity uses live source resources; parity also consumes the
    # immutable bundle. Include file inventories so additions invalidate reuse.
    backend_path = ROOT / "config/electrical-backend.json"
    if backend_path.is_file():
        backend = json.loads(backend_path.read_text(encoding="utf-8"))
        source = backend.get("backends", {}).get(backend.get("active"), {}).get("source_root")
        if isinstance(source, str):
            paths.extend(path for path in (ROOT / source).rglob("*") if path.is_file())
    if freeze is not None:
        paths.extend(path for path in freeze.parent.rglob("*") if path.is_file())
    toolchain = ROOT / "config/toolchain-policy.json"
    if toolchain.is_file():
        cli = json.loads(toolchain.read_text(encoding="utf-8")).get("kicad", {}).get("cli")
        if isinstance(cli, str):
            binary = Path(cli)
            paths.extend([binary, binary.with_name("python.exe")])
            paths.extend((binary.parent / "Lib/site-packages").glob("*pcbnew*"))
    unique: dict[str, str] = {}
    for path in paths:
        resolved = path.resolve()
        if resolved.is_file():
            try:
                key = relative(resolved)
            except ValueError:
                key = str(resolved)
            unique[key] = sha256(resolved)
    return dict(sorted(unique.items()))


def dependencies_fresh(dependencies: object) -> bool:
    return checkpoint.dependencies_fresh(dependencies, ROOT)


def reusable_checkpoint(receipt_path: Path, board: Path, baseline: Path | None) -> dict[str, object] | None:
    try:
        receipt = json.loads(receipt_path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(receipt, dict) or receipt.get("kind") != "pcb_pipeline_summary" or receipt.get("success") is not True:
        return None
    requested = receipt.get("requested")
    if not isinstance(requested, dict) or requested.get("tier") != "checkpoint":
        return None
    if receipt.get("board_sha256") != sha256(board):
        return None
    expected_baseline = sha256(baseline) if baseline is not None else None
    if receipt.get("baseline_board_sha256") != expected_baseline:
        return None
    manifest_value = receipt.get("producer_dependency_manifest")
    manifest_hash = receipt.get("producer_dependency_manifest_sha256")
    if not isinstance(manifest_value, str) or not isinstance(manifest_hash, str):
        return None
    manifest_path = (ROOT / manifest_value).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if sha256(manifest_path) != manifest_hash.upper() or not isinstance(manifest, dict):
        return None
    if manifest.get("kind") != "pcb_pipeline_dependencies" or not dependencies_fresh(manifest.get("dependencies")):
        return None
    return receipt


def run_step(name: str, command: list[str], log_dir: Path) -> dict[str, object]:
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{name}.log"
    log.write_text(result.stdout, encoding="utf-8")
    lines = result.stdout.splitlines()
    return {
        "name": name,
        "success": result.returncode == 0,
        "exit_code": result.returncode,
        "log": str(log.resolve()),
        "log_bytes": log.stat().st_size,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "data": last_json(result.stdout),
        "tail": lines[-1][:500] if lines else "",
    }


def step_inputs(command: list[str], snapshot: dict[str, str], evidence: Path, work: Path,
                available: dict | None = None) -> dict:
    """Bind an audited native step to its producers and conservative data inputs.

    Local imports and literal child-script names form the producer closure.
    Dynamic imports or unreadable producers retain the complete snapshot.
    Configuration/resource inventories remain conservative; assembly sourcing
    is excluded only when no producer names that input.
    """
    pending = [Path(command[1]).resolve()]
    producers, texts = set(), []
    runtime_modules, used_modules = toolchain_module_roots(), set()
    complete = Path(command[1]).name in {
        'check_kicad_physical_integrity.py', 'check_kicad_footprint_equivalence.py',
        'check_kicad_placement_semantics.py', 'check_kicad_placement_routability.py',
        'check_kicad_placement_immutability.py', 'extract_kicad_parity_manifest.py',
        'check_final_electrical_parity.py', 'krt_candidate_audit.py',
        'audit_kicad_release.py', 'render_kicad_visual_bundle.py',
    }
    while pending:
        path = pending.pop()
        if path in producers:
            continue
        producers.add(path)
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeError):
            complete = False
            break
        texts.append(source)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
                if node.level:
                    complete = False
            if any(name.split('.')[0] == 'importlib' for name in modules):
                complete = False
            for module in modules:
                local = ROOT / "scripts" / (module.replace('.', '/') + ".py")
                if local.is_file():
                    pending.append(local.resolve())
                elif module.split('.')[0] in runtime_modules:
                    used_modules.add(module.split('.')[0])
                elif module.split('.')[0] not in sys.stdlib_module_names | {'pcbnew', '_pcbnew', 'pcb_template_engine'}:
                    complete = False
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"__import__", "exec", "eval"}:
                complete = False
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith('.py'):
                local = ROOT / "scripts" / Path(node.value).name
                if local.is_file():
                    pending.append(local.resolve())
    dependencies = dict(snapshot)
    if complete:
        dependencies = {key: value for key, value in snapshot.items()
                        if not key.replace('\\', '/').startswith('scripts/') or (ROOT / key).resolve() in producers}
        unused_roots = [path.resolve() for name, roots in runtime_modules.items() if name not in used_modules for path in roots]
        used_roots = [path.resolve() for name, roots in runtime_modules.items() if name in used_modules for path in roots]
        dependencies = {key: value for key, value in dependencies.items()
                        if not any((ROOT / key).resolve().is_relative_to(path) for path in unused_roots)
                        or any((ROOT / key).resolve().is_relative_to(path) for path in used_roots)}
        # These native checks consume the physical board and reviewed electrical
        # identity, never a supplier's catalog mapping. Unknown consumers keep it.
        flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
        assembly = (flow.get("manufacturing") or {}).get("assembly_parts", "config/jlcpcb-parts.json")
        if isinstance(assembly, str) and not any("assembly_parts" in text or Path(assembly).name in text for text in texts):
            dependencies = {key: value for key, value in dependencies.items()
                            if (ROOT / key).resolve() != (ROOT / assembly).resolve()}
    # The executor itself is part of the cache contract, without walking its
    # unrelated command definitions into each check's producer closure.
    for path in (Path(__file__).resolve(), Path(sys.executable).resolve(), *sorted(producers)):
        if path.is_file():
            dependencies[str(path)] = sha256(path)
    normalized = [canonical_step_path(arg, evidence, work) for arg in command]
    output_flags = {'--output', '--report', '--receipt', '--audit-json', '--drc-report',
                    '--output-dir', '--work-dir', '--cache-dir', '--log-dir'}
    upstream = {}
    for index, arg in enumerate(normalized[1:], 1):
        if arg.startswith(('{evidence}/', '{work}/')) and command[index - 1] not in output_flags:
            path = Path(command[index])
            upstream[arg] = sha256(path) if path.is_file() else (available or {}).get(arg, '<missing>')
    return {'schema': 1, 'command': normalized, 'dependencies': dict(sorted(dependencies.items())),
            'upstream_inputs': upstream, 'cacheable': complete}


def reusable_step(record: object, inputs: dict) -> bool:
    return checkpoint.reusable_step(record, inputs, ROOT)


def restore_step(record: dict, evidence: Path, work: Path) -> tuple[dict, dict]:
    return checkpoint.restore_step(record, evidence, work, ROOT / 'build')


def stdout_index(summary: dict[str, object]) -> dict[str, object]:
    failed = summary.get("failed_step")
    run = summary.get("run") if isinstance(summary.get("run"), dict) else {}
    metrics = summary.get("context_metrics") if isinstance(summary.get("context_metrics"), dict) else {}
    requested = summary.get("requested") if isinstance(summary.get("requested"), dict) else {}
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    failed_data = failed.get("data") if isinstance(failed, dict) and isinstance(failed.get("data"), dict) else {}
    detail = {
        key: failed_data[key]
        for key in ("error", "reason", "baseline_pass", "release_pass", "next_action", "next_command")
        if key in failed_data
    }
    if isinstance(failed_data.get("failures"), list):
        detail["failures"] = failed_data["failures"][:8]
    if not detail and isinstance(failed, dict) and failed.get("tail"):
        detail["tail"] = str(failed["tail"])[:240]
    return {
        "success": summary.get("success"),
        "release_pass": summary.get("release_pass"),
        "tier": requested.get("tier"),
        "board_sha256": summary.get("board_sha256"),
        "passed_step_count": len(summary.get("passed_steps") or []),
        "failed_step": failed.get("name") if isinstance(failed, dict) else None,
        "failed_step_detail": detail or None,
        "duration_ms": metrics.get("duration_ms"),
        "pipeline_summary": artifacts.get("pipeline_summary"),
        "run_id": run.get("run_id"),
        "reused": summary.get("reused", False),
        **({"pending_reviews": summary["pending_reviews"], "placement_adopted": False,
             "executed_step_count": summary.get("executed_step_count", len(summary.get("passed_steps") or [])),
             "reused_step_count": summary.get("reused_step_count", 0)}
           if "pending_reviews" in summary else {}),
    }


def copy_release_evidence(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return relative(destination)


def adopted_routing_board() -> Path:
    return resolve_adopted_routing_board(ROOT)


def placement_checkpoint_commands(board: Path, baseline: Path, freeze: Path, state: str,
                                  evidence: Path, work: Path) -> list[tuple[str, list[str]]]:
    """One existing verifier, bounded producers, no adoption or human verdict."""
    def command(script: str, *args: object) -> list[str]:
        return [sys.executable, str(ROOT / "scripts" / script), *map(str, args)]
    commands = [("physical-integrity", command("check_kicad_physical_integrity.py",
        "--board", board, "--phase", "placement", "--output", evidence / "physical-integrity.json"))]
    if state == "moved":
        commands.extend([
            ("placement-semantics", command("check_kicad_placement_semantics.py", "--input", baseline,
                "--candidate", board, "--output", evidence / "placement-semantics.json")),
            ("routability", command("check_kicad_placement_routability.py", "--board", board,
                "--baseline", baseline, "--output", evidence / "routability.json")),
        ])
    else:
        commands.append(("placement-immutability", command("check_kicad_placement_immutability.py",
            "--input", baseline, "--candidate", board, "--output", evidence / "placement-immutability.json")))
    commands.extend([
        ("electrical-manifest", command("extract_kicad_parity_manifest.py", "--board", board,
            "--output", work / "electrical-manifest.json")),
        ("electrical-parity", command("check_final_electrical_parity.py", "--board", board,
            "--electrical-freeze", freeze, "--final-manifest", work / "electrical-manifest.json",
            "--output", evidence / "final-electrical-parity.json")),
        ("kicad-audit", command("krt_candidate_audit.py", "--input", baseline, "--candidate", board,
            "--work-dir", work / "drc", "--cache-dir", work / "drc-cache",
            "--report", evidence / "final-routing-audit.json")),
        ("visual-bundle", command("render_kicad_visual_bundle.py", "--board", board, "--tier", "checkpoint",
            "--trigger", "footprint_moved" if state == "moved" else "functional_block_completed",
            "--output-dir", work / "visuals", "--receipt", evidence / "visual-bundle.json")),
    ])
    return commands


def cached_placement_checkpoint(path: Path, dependencies: dict[str, str], state: str) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("kind") != "pcb_pipeline_summary" or value.get("success") is not True
                or value.get("requested", {}).get("placement_state") != state
                or value.get("checkpoint_inputs") != dependencies
                or value.get('uncacheable_steps')
                or not dependencies_fresh(value.get("evidence_hashes"))
                or not all(reusable_step(record, record.get('inputs'))
                           and dependencies_fresh(record['inputs'].get('dependencies'))
                           for record in (value.get('step_records') or {}).values())):
            return None
        return value
    except (OSError, ValueError, AttributeError):
        return None


def run_placement_checkpoint(args: argparse.Namespace, config: dict, board: Path, baseline: Path) -> int:
    started = time.perf_counter()
    stage = str(current_stage().get("stage"))
    if stage not in {"physical", "routing"}:
        raise SystemExit("Placement checkpoints require the physical or routing stage")
    configured = config.get("manufacturing", {}).get("gate_receipts", {}).get("electrical_freeze")
    if not isinstance(configured, str) or not (ROOT / configured).is_file():
        raise SystemExit("A current immutable electrical freeze is required")
    freeze = (ROOT / configured).resolve()
    # An empty new-project draft must never silently pass an empty review.
    for name in ("placement-semantics.json", "placement-routability.json"):
        policy = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
        if policy.get("status") in {"draft", "pending"}:
            raise SystemExit(f"Review project-specific {name} before a placement checkpoint")
    dependencies = checkpoint_dependencies(board, baseline, args.config, freeze)
    cache = ROOT / f"build/{'05-placement' if stage == 'physical' else '06-routing'}/evidence/checkpoint-cache.json"
    previous = None
    records = {}
    index = {}
    if args.reuse_checkpoint:
        previous = cached_placement_checkpoint(args.reuse_checkpoint.resolve(), dependencies, args.placement_state)
        if previous is None:
            raise SystemExit("Placement checkpoint receipt is stale or mismatched")
    elif cache.is_file():
        try:
            index = json.loads(cache.read_text(encoding="utf-8"))
            receipt = (ROOT / index["receipt"]).resolve()
            if sha256(receipt) == index["sha256"]:
                previous = cached_placement_checkpoint(receipt, dependencies, args.placement_state)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Invalid cache evidence is rerun, never accepted.
    if not isinstance(index, dict):
        index = {}
    if not args.reuse_checkpoint:
        try:
            source = (ROOT / index.get('resume_receipt', index.get('receipt', ''))).resolve()
            digest = index.get('resume_sha256', index.get('sha256'))
            if source.is_file() and sha256(source) == digest:
                value = json.loads(source.read_text(encoding='utf-8'))
                if value.get('kind') == 'pcb_pipeline_summary' and value.get('inputs_stable') is True:
                    candidate_records = value.get('step_records')
                    records = candidate_records if isinstance(candidate_records, dict) else {}
        except (OSError, ValueError, TypeError):
            pass
    plan_commands = placement_checkpoint_commands(board, baseline, freeze,
        args.placement_state, Path('evidence'), Path('tmp'))
    if args.plan:
        checks, available = [], {}
        for name, command in plan_commands:
            record = records.get(name)
            reuse = previous or reusable_step(record, step_inputs(command, dependencies, Path('evidence'), Path('tmp'), available))
            checks.append({'name': name, 'action': 'reuse' if reuse else 'run'})
            if reuse and record:
                available.update({canonical_step_path(path, Path(record['lanes']['evidence']), Path(record['lanes']['work'])): digest
                                  for path, digest in record['files'].items()})
        print(json.dumps({"success": True, "kind": "placement_checkpoint_plan", "board_sha256": sha256(board),
            "checks": checks,
            "pending_reviews": ["visual_review", "critical_route_probes"], "adopts_board": False}, separators=(",", ":")))
        return 0
    if previous:
        summary = {**previous, "reused": True, "executed_step_count": 0,
                   "reused_step_count": len(previous.get('passed_steps') or []),
                   "context_metrics": {**previous["context_metrics"], "duration_ms": round((time.perf_counter() - started) * 1000)}}
        print(json.dumps(stdout_index(summary), separators=(",", ":")))
        return 0
    run = create_run(stage, "pcb-audit", args.run_id)
    steps = []
    updated_records = {}
    raised = None
    for name, command in placement_checkpoint_commands(board, baseline, freeze, args.placement_state, run.evidence, run.tmp):
        inputs = step_inputs(command, dependencies, run.evidence, run.tmp)
        record = records.get(name)
        try:
            if reusable_step(record, inputs):
                step, record = restore_step(record, run.evidence, run.tmp)
                updated_records[name] = record
            else:
                before = step_files(run.evidence, run.tmp)
                step = run_step(name, command, run.tmp / 'logs')
                if step['success']:
                    record = step_record(inputs, step, before, run.evidence, run.tmp)
                    if record is not None:
                        updated_records[name] = record
        except BaseException as exc:
            raised = exc
            step = {'name': name, 'success': False, 'log_bytes': 0, 'tail': str(exc)[:500],
                    'data': {'error': 'checkpoint_step_exception'}}
        steps.append(step)
        if not step["success"]:
            break
    failed = next((step for step in steps if not step["success"]), None)
    stable = dependencies == checkpoint_dependencies(board, baseline, args.config, freeze)
    stable = stable and all(reusable_step(record, record['inputs']) for record in updated_records.values())
    if not stable and failed is None:
        failed = {"name": "input-freshness", "tail": "Checkpoint dependencies changed during verification",
                  "data": {"error": "checkpoint_inputs_changed_during_run"}}
    success = failed is None and stable
    summary_path = run.evidence / "pipeline-summary.json"
    evidence_files = [p for p in run.evidence.rglob("*") if p.is_file() and p != run.manifest]
    evidence_files.extend(p for p in (run.tmp / "visuals").rglob("*") if p.is_file())
    summary = {"schema": 2, "kind": "pcb_pipeline_summary", "success": success,
        "run": {"run_id": run.run_id}, "requested": {"tier": "checkpoint", "build": False, "export": False,
            "placement_state": args.placement_state, "board": relative(board), "baseline_board": relative(baseline)},
        "board_sha256": sha256(board), "baseline_board_sha256": sha256(baseline),
        "checkpoint_inputs": dependencies, "inputs_stable": stable,
        "step_records": updated_records if stable else {},
        'uncacheable_steps': [step['name'] for step in steps if step['success'] and step['name'] not in updated_records],
        "evidence_hashes": {relative(p): sha256(p) for p in evidence_files},
        "passed_steps": [step["name"] for step in steps if step["success"]], "failed_step": failed,
        "executed_step_count": sum(not step.get('reused') for step in steps),
        "reused_step_count": sum(bool(step.get('reused')) for step in steps),
        "reused": bool(steps) and all(step.get('reused') for step in steps),
        "release_pass": False, "stage_complete": False, "placement_adopted": False,
        "pending_reviews": ["visual_review", "critical_route_probes"],
        "artifacts": {"pipeline_summary": relative(summary_path),
            **{p.stem: relative(p) for p in run.evidence.glob("*.json")}},
        "context_metrics": {"duration_ms": round((time.perf_counter() - started) * 1000),
            "executed_steps": sum(not step.get('reused') for step in steps),
            "reused_steps": sum(bool(step.get('reused')) for step in steps),
            "pipeline_log_bytes": sum(step["log_bytes"] for step in steps if not step.get('reused'))},
        "limits": "Deterministic checkpoint checks only; human visual review, critical probes, promotion and release remain separate gates."}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    finish_run(run, success, artifacts={"pipeline_summary": relative(summary_path)}, metrics=summary["context_metrics"])
    if stable and (success or updated_records):
        cache.parent.mkdir(parents=True, exist_ok=True)
        index.update(schema=2, resume_receipt=relative(summary_path), resume_sha256=sha256(summary_path))
        if success:
            index.update(receipt=relative(summary_path), sha256=sha256(summary_path))
        cache.write_text(json.dumps(index)+"\n", encoding="utf-8")
    print(json.dumps(stdout_index(summary), ensure_ascii=False, separators=(",", ":")))
    if raised is not None:
        raise raised
    return 0 if success else 1


def main() -> int:
    pipeline_started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/pcb-flow.json")
    parser.add_argument("--build", action="store_true", help="Run the selected isolated electrical backend first.")
    parser.add_argument("--export", action="store_true", help="Regenerate JLCPCB core outputs after checks pass.")
    parser.add_argument("--tier", choices=("checkpoint", "release"), default="checkpoint", help="Checkpoint runs board DRC/connectivity only; release also refreshes policy and project gates.")
    parser.add_argument(
        "--board",
        type=Path,
        help="Exact KiCad board to verify; defaults to project.board. Use this for isolated routing checkpoints.",
    )
    parser.add_argument(
        "--baseline-board",
        type=Path,
        help="Passing predecessor for checkpoint DRC/connectivity delta; not valid for release/export.",
    )
    parser.add_argument("--run-id", help="Optional stable artifact run id; defaults to a UTC timestamp and operation.")
    parser.add_argument("--log-dir", type=Path, help="Compatibility override; defaults inside the run tmp lane.")
    parser.add_argument("--reuse-checkpoint", type=Path, help="Reuse an exact passing checkpoint receipt only when every bound dependency is unchanged.")
    parser.add_argument("--placement-state", choices=("moved", "unchanged"), help="Aggregate placement integrity, parity, DRC and visual generation; unchanged proves immutability and skips placement semantics/scoring.")
    parser.add_argument("--plan", action="store_true", help="Plan an aggregate placement checkpoint without creating artifacts.")
    args = parser.parse_args()
    if args.build and args.export:
        raise SystemExit("A source build creates an unreviewed candidate; review it before a separate export attempt")
    if args.export:
        args.tier = "release"
    if args.baseline_board and (args.build or args.export or args.tier != "checkpoint"):
        raise SystemExit("--baseline-board is only valid for checkpoint verification")
    if args.reuse_checkpoint and (args.build or args.export or args.tier != "checkpoint"):
        raise SystemExit("--reuse-checkpoint is valid only for checkpoint verification")
    if args.plan and not args.placement_state:
        raise SystemExit("--plan requires --placement-state")
    if args.placement_state and (args.build or args.export or args.tier != "checkpoint" or not args.baseline_board):
        raise SystemExit("--placement-state requires checkpoint --baseline-board and excludes build/export")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    project = config["project"]
    selected_board = (
        args.board.resolve() if args.board
        else adopted_routing_board() if args.export
        else (ROOT / project["board"]).resolve()
    )
    preflight_board = selected_board
    if args.export:
        readiness = fresh_release_readiness(preflight_board)
        if readiness is None:
            print(json.dumps({
                "success": False, "error": "release_readiness_missing_or_stale",
                "next": f'py -3.14 scripts/pcbctl.py verify readiness --board "{preflight_board}"',
            }, separators=(",", ":")))
            return 2
        if readiness.get("release_ready") is not True:
            print(json.dumps({
                "success": False, "error": "release_readiness_blocked",
                "blockers": [item.get("code") for item in readiness.get("blockers", []) if isinstance(item, dict)],
                "next_action": readiness.get("next_action"), "next_command": readiness.get("next_command"),
            }, separators=(",", ":")))
            return 2
    audit_config = config["audit"]
    context_config = config["context"]
    manufacturing_config = config["manufacturing"]
    stage = str(current_stage().get("stage"))
    operation = "electrical-build" if args.build else "release-export" if args.export else "pcb-audit"
    acceptance_path = ROOT / config.get("acceptance", "config/pcb-acceptance.json")
    release_rules = load_acceptance(acceptance_path, "release", {
        "release_pass", "board_sha256_match", "drc_zero_unconnected", "manufacturing_profile_selected",
        "electrical_freeze_pass", "jlc_dfm_pass", "dfm_order_profile_match",
        "final_electrical_parity_pass", "physical_integrity_pass", "critical_footprint_rule_areas_pass", "manufacturing_registry_pass", "gerber_rules_pass",
        "manufacturing_cost_policy_pass", "special_manufacturing_processes_confirmed",
    })
    require_no_prohibited_evidence(release_rules, {"matching_release_audit"})
    board_path = selected_board
    if not args.build and not board_path.is_file():
        raise SystemExit(f"Board not found: {board_path}")
    board = str(board_path)
    baseline_board_path = args.baseline_board.resolve() if args.baseline_board else None
    if baseline_board_path and not baseline_board_path.is_file():
        raise SystemExit(f"Baseline board not found: {baseline_board_path}")
    if args.placement_state:
        return run_placement_checkpoint(args, config, board_path, baseline_board_path)
    if args.reuse_checkpoint:
        reused = reusable_checkpoint(args.reuse_checkpoint, board_path, baseline_board_path)
        if reused is None:
            print(json.dumps({
                "success": False, "error": "checkpoint_receipt_missing_stale_or_mismatched",
                "receipt": str(args.reuse_checkpoint.resolve()),
            }, separators=(",", ":")))
            return 2
        reused_index = dict(reused)
        reused_index["reused"] = True
        reused_index["context_metrics"] = {**(reused.get("context_metrics") or {}), "duration_ms": 0}
        print(json.dumps(stdout_index(reused_index), ensure_ascii=False, separators=(",", ":")))
        return 0

    # Create a run only after all read-only preflight and reuse decisions. This
    # prevents status checks and exact checkpoint reuse from leaving abandoned
    # in-progress artifacts behind.
    run = create_run(stage, operation, args.run_id)
    log_dir = args.log_dir.resolve() if args.log_dir else run.tmp / "logs"
    evidence_drc = run.evidence / "drc" / "kicad-cli-drc.rpt"
    evidence_audit = run.evidence / "final-routing-audit.json"
    context_output = run.tmp / "context" / "pcb-context.json"
    pipeline_summary = run.evidence / "pipeline-summary.json"
    package_audit = run.evidence / "jlcpcb-package-audit.json"
    manufacturing_gate = run.evidence / "manufacturing-gate.json"
    manufacturing_registry_validation = run.evidence / "manufacturing-registry-validation.json"
    manufacturing_cost_policy = run.evidence / "manufacturing-cost-policy.json"
    gerber_rules_gate = run.evidence / "manufacturing-registry-gerber.json"
    footprint_equivalence = run.evidence / "footprint-equivalence.json"
    physical_integrity = run.evidence / "physical-integrity.json"
    special_process_gate = run.evidence / "manufacturing-registry-pre-export.json"
    checkpoint_dependency_manifest = run.evidence / "checkpoint-dependencies.json"

    commands: list[tuple[str, list[str]]] = []
    resolved_release_inputs: dict[str, dict[str, str | None]] = {}
    if args.build or args.tier == "release":
        commands.append(("compile-design-policy", [sys.executable, "scripts/compile_design_policy.py"]))
        commands.append(("manufacturing-registry", [
            sys.executable, "scripts/check_manufacturing_registry.py",
            "--phase", "validate",
            "--output", str(manufacturing_registry_validation),
        ]))
        for script in config.get("checks", []):
            commands.append((Path(script).stem, [sys.executable, script]))
    if args.build:
        electrical_name, _ = selected_electrical_backend(read_electrical_config())
        electrical_output = run.tmp / "generated" / electrical_name
        electrical_receipt = run.evidence / "electrical-build.json"
        commands.append((
            "electrical-build",
            [
                sys.executable, "scripts/build_electrical.py",
                "--build", project["build_target"],
                "--output", str(electrical_output),
                "--receipt", str(electrical_receipt),
            ],
        ))
    else:
        if args.tier == "checkpoint" and baseline_board_path:
            audit_command = [
                sys.executable, "scripts/krt_candidate_audit.py",
                "--input", str(baseline_board_path),
                "--candidate", board,
                "--work-dir", str(run.tmp / "checkpoint-drc"),
                "--cache-dir", str(run.tmp / "drc-cache"),
                "--report", str(evidence_audit),
            ]
        else:
            audit_command = [
                sys.executable, "scripts/audit_kicad_release.py",
                "--board", board,
                "--baseline", str(ROOT / audit_config["baseline"]),
                "--routing-paths", str(ROOT / audit_config["routing_paths"]),
                "--audit-json", str(evidence_audit),
                "--drc-report", str(evidence_drc),
            ]
            if audit_config.get("require_in1_ground", False):
                audit_command.append("--require-in1-ground")
        commands.append(("kicad-audit", audit_command))
        if args.tier == "release":
            commands.append(("physical-integrity", [
                sys.executable, "scripts/check_kicad_physical_integrity.py",
                "--board", board, "--phase", "release",
                "--output", str(physical_integrity),
                "--footprint-output", str(footprint_equivalence),
            ]))
            commands.append((
                "pcb-context",
                [
                    sys.executable, "scripts/extract_pcb_context.py",
                    "--board", board,
                    "--policy", str(ROOT / context_config["policy"]),
                    "--output", str(context_output),
                ],
            ))
        if args.export:
            physical_review, _, physical_review_source = resolve_release_input(config, "physical_review")
            export_log = run.tmp / "logs" / "jlcpcb-export.log"
            gate_receipts = manufacturing_config["gate_receipts"]
            final_parity, _, final_parity_source = resolve_release_input(config, "final_electrical_parity")
            jlc_dfm, _, jlc_dfm_source = resolve_release_input(config, "jlc_dfm")
            selected = {
                "physical_review": (physical_review, physical_review_source),
                "final_electrical_parity": (final_parity, final_parity_source),
                "jlc_dfm": (jlc_dfm, jlc_dfm_source),
            }
            missing_inputs = [name for name, (path, _) in selected.items() if path is None]
            if missing_inputs:
                raise SystemExit("Release inputs are not configured: " + ", ".join(missing_inputs))
            resolved_release_inputs = {
                name: {"path": relative(path), "source": source}
                for name, (path, source) in selected.items() if path is not None
            }
            manufacturing_gate_command = [
                sys.executable, "scripts/check_manufacturing_gates.py",
                "--board", board,
                "--electrical-freeze", str(ROOT / gate_receipts["electrical_freeze"]),
                "--final-electrical-parity", str(final_parity),
                "--jlc-dfm", str(jlc_dfm),
                "--output", str(manufacturing_gate),
            ]
            if gate_receipts.get("easyeda_import_parity"):
                manufacturing_gate_command.extend(["--import-parity", str(ROOT / gate_receipts["easyeda_import_parity"])])
            if gate_receipts.get("easyeda_drc"):
                manufacturing_gate_command.extend(["--easyeda-drc", str(ROOT / gate_receipts["easyeda_drc"])])
            commands.extend([
                (
                    "manufacturing-cost-policy",
                    [
                        sys.executable, "scripts/check_manufacturing_registry.py",
                        "--phase", "cost",
                        "--output", str(manufacturing_cost_policy),
                    ],
                ),
                (
                    "manufacturing-registry-pre-export",
                    [
                        sys.executable, "scripts/check_manufacturing_registry.py",
                        "--phase", "pre_export",
                        "--output", str(special_process_gate),
                    ],
                ),
                (
                    "manufacturing-gates",
                    manufacturing_gate_command,
                ),
                (
                    "jlcpcb-export",
                    [
                        sys.executable, "scripts/export_jlcpcb_release.py",
                        "--board", board,
                        "--audit-json", str(evidence_audit),
                        "--physical-review", str(physical_review),
                        "--manufacturing-gates", str(manufacturing_gate),
                        "--output", str(ROOT / manufacturing_config["output"]),
                        "--log", str(export_log),
                        "--assembly-parts", str(ROOT / manufacturing_config["assembly_parts"]),
                        "--acceptance", str(acceptance_path),
                        "--registry-pre-export", str(special_process_gate),
                        "--cost-policy", str(manufacturing_cost_policy),
                        "--gerber-rules-output", str(gerber_rules_gate),
                        "--footprint-equivalence", str(footprint_equivalence),
                        "--physical-integrity", str(physical_integrity),
                    ],
                ),
                (
                    "jlcpcb-package-check",
                    [
                        sys.executable, "scripts/check_jlcpcb_release.py",
                        "--package", str(ROOT / manufacturing_config["output"]),
                        "--board-stack", str(ROOT / config["board_stack"]),
                        "--gerber-rules", str(gerber_rules_gate),
                        "--output", str(package_audit),
                    ],
                ),
            ])

    steps: list[dict[str, object]] = []
    for name, command in commands:
        step = run_step(name, command, log_dir)
        steps.append(step)
        if not step["success"]:
            break

    audit = next((item["data"] for item in steps if item["name"] == "kicad-audit"), None)
    context = next((item["data"] for item in steps if item["name"] == "pcb-context"), None)
    electrical = next((item["data"] for item in steps if item["name"] == "electrical-build"), None)
    gate_result = next((item["data"] for item in steps if item["name"] == "manufacturing-gates"), None)
    package_result = next((item["data"] for item in steps if item["name"] == "jlcpcb-package-check"), None)
    failed = next((item for item in steps if not item["success"]), None)
    success = len(steps) == len(commands) and all(bool(item["success"]) for item in steps)
    checkpoint_delta = args.tier == "checkpoint" and baseline_board_path is not None
    kicad_audit_pass = (
        audit.get("success") is True and audit.get("baseline_pass") is True
        if checkpoint_delta and isinstance(audit, dict)
        else audit.get("release_pass") is True if isinstance(audit, dict)
        else None
    )
    release_pass = bool(
        args.export and success and kicad_audit_pass
        and isinstance(gate_result, dict) and gate_result.get("manufacturing_gate_pass") is True
        and isinstance(package_result, dict) and package_result.get("success") is True
    )
    checkpoint_dependency_manifest_value = None
    checkpoint_dependency_manifest_hash = None
    if args.tier == "checkpoint" and not args.build:
        checkpoint_dependency_manifest.write_text(json.dumps({
            "schema": 1,
            "kind": "pcb_pipeline_dependencies",
            "dependencies": checkpoint_dependencies(board_path, baseline_board_path, args.config),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        checkpoint_dependency_manifest_value = relative(checkpoint_dependency_manifest)
        checkpoint_dependency_manifest_hash = sha256(checkpoint_dependency_manifest)
    summary: dict[str, object] = {
        "schema": 2,
        "kind": "pcb_pipeline_summary",
        "success": success,
        "run": {key: relative(Path(value)) if key in {"tmp", "evidence", "release", "manifest"} else value for key, value in run.as_dict().items()},
        "requested": {
            "build": args.build,
            "export": args.export,
            "tier": args.tier,
            "board": relative(board_path) if not args.build else None,
            "baseline_board": relative(baseline_board_path) if baseline_board_path else None,
        },
        "electrical_candidate": electrical,
        "candidate_pending_review": args.build and not failed,
        "acceptance": {"path": relative(acceptance_path), "release_requires": release_rules.get("requires", [])},
        "resolved_release_inputs": resolved_release_inputs,
        "board_sha256": audit.get("board_sha256") if isinstance(audit, dict) else None,
        "baseline_board_sha256": sha256(baseline_board_path) if baseline_board_path else None,
        "producer_dependency_manifest": checkpoint_dependency_manifest_value,
        "producer_dependency_manifest_sha256": checkpoint_dependency_manifest_hash,
        "in1_track_segments": audit.get("in1_non_ground_segments") if isinstance(audit, dict) else None,
        "drc_categories": (
            audit.get("new_violation_types", {}) if checkpoint_delta
            else audit.get("drc_categories")
        ) if isinstance(audit, dict) else None,
        "kicad_audit_pass": kicad_audit_pass,
        "release_pass": release_pass,
        "passed_steps": [str(item["name"]) for item in steps if item["success"]],
        "failed_step": ({
            "name": failed["name"], "exit_code": failed["exit_code"],
            "log": relative(Path(str(failed["log"]))), "tail": failed["tail"],
        } if failed else None),
        "artifacts": {
            "electrical_receipt": relative(electrical_receipt) if args.build and electrical_receipt.is_file() else None,
            "audit_json": relative(evidence_audit) if evidence_audit.is_file() else None,
            "drc_report": relative(evidence_drc) if evidence_drc.is_file() else None,
            "pcb_context_index": relative(context_output) if context_output.is_file() else None,
            "package_audit": relative(package_audit) if package_audit.is_file() else None,
            "manufacturing_gate": relative(manufacturing_gate) if manufacturing_gate.is_file() else None,
            "manufacturing_registry_validation": relative(manufacturing_registry_validation) if manufacturing_registry_validation.is_file() else None,
            "gerber_rules_gate": relative(gerber_rules_gate) if gerber_rules_gate.is_file() else None,
            "footprint_equivalence": relative(footprint_equivalence) if footprint_equivalence.is_file() else None,
            "physical_integrity": relative(physical_integrity) if physical_integrity.is_file() else None,
            "manufacturing_registry_pre_export": relative(special_process_gate) if special_process_gate.is_file() else None,
            "checkpoint_dependencies": checkpoint_dependency_manifest_value,
            "logs": relative(log_dir),
        },
        "context_metrics": {
            "pcb_context_bytes": context_output.stat().st_size if context_output.is_file() else 0,
            "pipeline_log_bytes": sum(int(item.get("log_bytes", 0)) for item in steps),
            "duration_ms": round((time.perf_counter() - pipeline_started) * 1000),
            "step_durations_ms": {str(item["name"]): int(item.get("duration_ms", 0)) for item in steps},
            "stdout_contract": "compact_json_only",
            "full_pcb_may_enter_llm_context": False,
        },
    }
    pipeline_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    release_artifacts: dict[str, str] = {}
    if release_pass and isinstance(audit, dict):
        release_artifacts["routing_audit"] = copy_release_evidence(evidence_audit, run.release / evidence_audit.name)
        release_artifacts["drc_report"] = copy_release_evidence(evidence_drc, run.release / evidence_drc.name)
        if package_audit.is_file():
            release_artifacts["package_audit"] = copy_release_evidence(package_audit, run.release / package_audit.name)
        if manufacturing_gate.is_file():
            release_artifacts["manufacturing_gate"] = copy_release_evidence(manufacturing_gate, run.release / manufacturing_gate.name)
        if manufacturing_registry_validation.is_file():
            release_artifacts["manufacturing_registry_validation"] = copy_release_evidence(manufacturing_registry_validation, run.release / manufacturing_registry_validation.name)
        if gerber_rules_gate.is_file():
            release_artifacts["gerber_rules_gate"] = copy_release_evidence(gerber_rules_gate, run.release / gerber_rules_gate.name)
        if footprint_equivalence.is_file():
            release_artifacts["footprint_equivalence"] = copy_release_evidence(footprint_equivalence, run.release / footprint_equivalence.name)
        if physical_integrity.is_file():
            release_artifacts["physical_integrity"] = copy_release_evidence(physical_integrity, run.release / physical_integrity.name)
        if special_process_gate.is_file():
            release_artifacts["manufacturing_registry_pre_export"] = copy_release_evidence(special_process_gate, run.release / special_process_gate.name)
        if physical_review is None:
            raise SystemExit("Passing physical review input disappeared during release publication")
        release_artifacts["physical_review"] = copy_release_evidence(physical_review, run.release / physical_review.name)
        package_reference = run.release / "package-reference.json"
        package_reference.write_text(json.dumps({
            "schema": 1,
            "board_sha256": audit.get("board_sha256"),
            "release_pass": release_pass,
            "package": str((ROOT / manufacturing_config["output"]).resolve()),
            "note": "The immutable manufacturing snapshot remains under manufacturing/releases after create_release_snapshot.py.",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        release_artifacts["package_reference"] = relative(package_reference)
        published = publish_stage_artifact(
            run, board_path, "released.kicad_pcb",
            evidence={
                "routing_audit": release_artifacts["routing_audit"],
                "physical_review": release_artifacts["physical_review"],
                **({"manufacturing_gate": release_artifacts["manufacturing_gate"]} if "manufacturing_gate" in release_artifacts else {}),
                **({"manufacturing_registry_validation": release_artifacts["manufacturing_registry_validation"]} if "manufacturing_registry_validation" in release_artifacts else {}),
                **({"gerber_rules_gate": release_artifacts["gerber_rules_gate"]} if "gerber_rules_gate" in release_artifacts else {}),
                **({"footprint_equivalence": release_artifacts["footprint_equivalence"]} if "footprint_equivalence" in release_artifacts else {}),
                **({"physical_integrity": release_artifacts["physical_integrity"]} if "physical_integrity" in release_artifacts else {}),
                **({"manufacturing_registry_pre_export": release_artifacts["manufacturing_registry_pre_export"]} if "manufacturing_registry_pre_export" in release_artifacts else {}),
            },
        )
        release_artifacts["board"] = str(published["artifact"])
        release_artifacts["stage_result"] = relative(run.release / "stage-result.json")

    summary["release_artifacts"] = release_artifacts
    summary["artifacts"]["pipeline_summary"] = relative(pipeline_summary)
    pipeline_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(
        run,
        success,
        artifacts={**summary["artifacts"], "pipeline_summary": relative(pipeline_summary), **release_artifacts},
        metrics=summary["context_metrics"],
    )
    print(json.dumps(stdout_index(summary), ensure_ascii=False, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
