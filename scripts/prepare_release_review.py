#!/usr/bin/env python3
"""Prepare exact-board deterministic evidence for one release review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from artifact_store import create_run, finish_run, guard_run, relative, write_manifest
from run_pcb_pipeline import checkpoint_dependencies, step_inputs, step_files, step_record, reusable_step, restore_step
from audit_kicad_release import FATAL_CATEGORIES
from record_kicad_drc_review import REVIEWABLE
from release_inputs import (
    adopted_routing_board,
    dependencies_fresh,
    dependency,
    load_json,
    register_release_input,
    resolve_path,
    resolve_release_input,
    sha256,
)


ROOT = Path(__file__).resolve().parents[1]


def reviewed_placement_baseline(flow: dict[str, Any], explicit: Path | None) -> Path:
    if explicit is not None:
        baseline = explicit.resolve()
        if not baseline.is_file():
            raise SystemExit(f"Placement baseline not found: {baseline}")
        return baseline
    _, review, _ = resolve_release_input(flow, "physical_review")
    if review is None:
        raise SystemExit("No reviewed physical baseline is available; pass --placement-baseline")
    direct = resolve_path(review.get("board"))
    if direct is not None and direct.is_file() and sha256(direct) == review.get("board_sha256"):
        return direct
    evidence = review.get("evidence") or {}
    for value in evidence.get("placement", []) if isinstance(evidence, dict) else []:
        receipt_path = resolve_path(value)
        receipt = load_json(receipt_path) if receipt_path is not None else None
        candidate = resolve_path((receipt or {}).get("candidate"))
        if (
            receipt is not None and receipt.get("kind") == "kicad_placement_semantics"
            and receipt.get("success") is True and candidate is not None and candidate.is_file()
            and sha256(candidate) == receipt.get("candidate_sha256")
        ):
            return candidate
    raise SystemExit("Reviewed physical evidence no longer contains its placement baseline; pass --placement-baseline")


def run_step(
    name: str, command: list[str], log_dir: Path, accepted: set[int] = {0},
) -> tuple[int, Path]:
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{name}.log"
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode not in accepted:
        raise SystemExit(f"Release review preparation failed at {name}; see {relative(log)}")
    return completed.returncode, log


def mismatch_refs(report: str) -> list[str]:
    sections = re.findall(
        r"(?ms)^\[lib_footprint_mismatch\]:.*?(?=^\[[^\]]+\]:|\Z)", report,
    )
    return sorted(set(re.findall(r"Footprint\s+([A-Za-z]+\d+)\b", "\n".join(sections))))


def reusable_packet(flow: dict[str, Any], board: Path, snapshot: dict | None = None) -> tuple[Path, dict[str, Any]] | None:
    path, packet, _ = resolve_release_input(flow, "release_review_packet")
    if (
        path is None or packet is None or packet.get("kind") != "release_review_packet"
        or packet.get("success") is not True or packet.get("board_sha256") != sha256(board)
        or not dependencies_fresh(packet.get("producer_dependencies"))
        or (snapshot is not None and packet.get('checkpoint_inputs') != snapshot)
        or packet.get('uncacheable_steps')
    ):
        return None
    for name, digest in (packet.get("artifact_sha256") or {}).items():
        artifact = resolve_path((packet.get("artifacts") or {}).get(name))
        if artifact is None or not artifact.is_file() or sha256(artifact) != digest:
            return None
    if not all(isinstance(record, dict) and isinstance(record.get('inputs'), dict)
               and reusable_step(record, record['inputs']) and dependencies_fresh(record['inputs'].get('dependencies'))
               for record in (packet.get('step_records') or {}).values()):
        return None
    return path, packet


def review_commands(board: Path, baseline: Path, flow: dict, evidence: Path, work: Path, refs: list[str]) -> dict:
    def command(script, *args):
        return [sys.executable, str(ROOT / 'scripts' / script), *map(str, args)]
    audit = flow['audit']
    audit_command = command('audit_kicad_release.py', '--board', board,
        '--drc-report', evidence / 'drc/kicad-cli-drc.rpt', '--audit-json', evidence / 'pre-review-routing-audit.json',
        '--baseline', ROOT / audit['baseline'], '--routing-paths', ROOT / audit['routing_paths'])
    if audit.get('require_in1_ground', False):
        audit_command.append('--require-in1-ground')
    footprint_command = command('check_kicad_footprint_equivalence.py', '--board', board,
                                '--output', evidence / 'footprint-equivalence.json')
    for ref in refs:
        footprint_command.extend(['--ref', ref])
    return {'release-audit': audit_command, 'footprint-equivalence': footprint_command,
        'placement-semantics': command('check_kicad_placement_semantics.py', '--input', baseline,
            '--candidate', board, '--output', evidence / 'placement-semantics.json'),
        'release-visual-bundle': command('render_kicad_visual_bundle.py', '--board', board, '--tier', 'release',
            '--output-dir', work / 'visual', '--receipt', work / 'visual/visual-bundle.json')}


def require_reviewable_audit(path: Path) -> dict:
    audit = load_json(path)
    if audit is None:
        raise SystemExit('Release audit did not produce a receipt')
    categories = {str(key): int(value) for key, value in (audit.get('drc_categories') or {}).items()}
    missing = list(((audit.get('baseline') or {}).get('missing_required_zones') or []))
    if set(categories) - REVIEWABLE or FATAL_CATEGORIES.intersection(categories) or audit.get('unconnected_signatures') or missing:
        raise SystemExit('Release review packet cannot package fatal/non-reviewable DRC or missing required zones')
    return audit


def review_step_inputs(command, snapshot, evidence, work, name):
    inputs = step_inputs(command, snapshot, evidence, work)
    inputs['accepted_exit_codes'] = [0, 4] if name == 'release-audit' else [0]
    return inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path)
    parser.add_argument("--placement-baseline", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument('--plan', action='store_true', help='Show per-check run/reuse decisions without creating artifacts.')
    parser.add_argument('--refresh-step', action='append', default=[],
                        choices=['release-audit', 'footprint-equivalence', 'placement-semantics', 'release-visual-bundle'],
                        help='Recompute one check while retaining other valid evidence.')
    args = parser.parse_args()
    board = args.board.resolve() if args.board else adopted_routing_board()
    adopted = adopted_routing_board()
    if board != adopted or sha256(board) != sha256(adopted):
        raise SystemExit("Release review preparation accepts only the adopted routing artifact")
    flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
    baseline = reviewed_placement_baseline(flow, args.placement_baseline)
    freeze = resolve_path((flow.get('manufacturing') or {}).get('gate_receipts', {}).get('electrical_freeze'))
    snapshot = checkpoint_dependencies(board, baseline, ROOT / 'config/pcb-flow.json', freeze)
    existing = reusable_packet(flow, board, snapshot)
    if existing is not None and not args.refresh and not args.refresh_step and not args.plan:
        path, packet = existing
        print(json.dumps({
            "success": True, "reused": True, "board_sha256": packet["board_sha256"],
            "packet": relative(path), "visual_bundle": (packet.get("artifacts") or {}).get("visual_bundle"),
            "assessment_template": (packet.get("artifacts") or {}).get("assessment_template"),
            "next_command": packet.get("next_command"),
            "executed_step_count": 0, "reused_step_count": len(packet.get('step_records') or {}),
        }, separators=(",", ":")))
        return 0
    _, progress, _ = resolve_release_input(flow, 'release_review_progress')
    records = ((progress or {}).get('metrics') or {}).get('step_cache') or {}
    if args.refresh or not isinstance(records, dict) or (progress or {}).get('operation') != 'release-review-packet':
        records = {}
    records = {name: record for name, record in records.items() if name not in args.refresh_step}
    if args.plan:
        evidence, work = Path('evidence'), Path('tmp')
        commands = review_commands(board, baseline, flow, evidence, work, [])
        audit_inputs = review_step_inputs(commands['release-audit'], snapshot, evidence, work, 'release-audit')
        audit_record = records.get('release-audit')
        refs = None
        if reusable_step(audit_record, audit_inputs):
            report = next((Path(path) for path in audit_record['required_outputs'] if path.endswith('.rpt')), None)
            if report is not None:
                refs = mismatch_refs(report.read_text(encoding='utf-8'))
                commands = review_commands(board, baseline, flow, evidence, work, refs)
        checks = []
        for name, command in commands.items():
            if name == 'footprint-equivalence' and refs == []:
                continue
            action = 'reuse' if reusable_step(records.get(name), review_step_inputs(command, snapshot, evidence, work, name)) else 'run'
            if name == 'footprint-equivalence' and refs is None:
                action = 'run_if_required'
            checks.append({'name': name, 'action': action})
        print(json.dumps({'success': True, 'kind': 'release_review_plan', 'checks': checks,
                          'release_approved': False, 'creates_artifacts': False}, separators=(',', ':')))
        return 0
    run = create_run('release', 'release-review-packet', args.run_id)
    try:
        with guard_run(run):
            return execute_review(run, board, baseline, flow, records, snapshot, freeze)
    finally:
        # Progress points to an existing run manifest, never to a passing packet.
        value = load_json(run.manifest)
        if value is not None:
            register_release_input('release_review_progress', run.manifest, value)


def execute_review(run, board: Path, baseline: Path, flow: dict[str, Any], records: dict, snapshot: dict, freeze: Path | None) -> int:
    logs = run.tmp / "logs"
    drc_report = run.evidence / "drc" / "kicad-cli-drc.rpt"
    audit_path = run.evidence / "pre-review-routing-audit.json"
    footprint_path = run.evidence / "footprint-equivalence.json"
    semantics_path = run.evidence / "placement-semantics.json"
    visual_dir = run.tmp / "visual"
    visual_bundle = visual_dir / "visual-bundle.json"
    updated_records, steps = {}, []
    executed, reused = 0, 0

    def save_progress():
        write_manifest(run, 'in_progress', success=None, metrics={'board_sha256': sha256(board),
            'step_cache': updated_records, 'executed_steps': executed, 'reused_steps': reused})
        register_release_input('release_review_progress', run.manifest, load_json(run.manifest))

    def cached_run(name, command):
        nonlocal executed, reused
        inputs = review_step_inputs(command, snapshot, run.evidence, run.tmp, name)
        record = records.get(name)
        if reusable_step(record, inputs):
            step, record = restore_step(record, run.evidence, run.tmp)
            updated_records[name] = record
            reused += 1
        else:
            before = step_files(run.evidence, run.tmp)
            executed += 1
            save_progress()
            started = time.perf_counter()
            code, log = run_step(name, command, logs, set(inputs['accepted_exit_codes']))
            step = {'name': name, 'success': True, 'exit_code': code, 'log': str(log),
                    'log_bytes': log.stat().st_size, 'duration_ms': round((time.perf_counter() - started) * 1000)}
            record = step_record(inputs, step, before, run.evidence, run.tmp)
            if record is not None:
                updated_records[name] = record
        if name == 'release-audit':
            require_reviewable_audit(audit_path)
        if snapshot != checkpoint_dependencies(board, baseline, ROOT / 'config/pcb-flow.json', freeze):
            raise SystemExit('Review inputs changed during verification')
        steps.append(step)
        save_progress()
        return step['exit_code']

    commands = review_commands(board, baseline, flow, run.evidence, run.tmp, [])
    audit_code = cached_run('release-audit', commands['release-audit'])
    audit = require_reviewable_audit(audit_path)
    categories = {str(key): int(value) for key, value in (audit.get("drc_categories") or {}).items()}

    report_text = drc_report.read_text(encoding="utf-8", errors="replace")
    refs = mismatch_refs(report_text)
    commands = review_commands(board, baseline, flow, run.evidence, run.tmp, refs)
    if refs:
        cached_run('footprint-equivalence', commands['footprint-equivalence'])
    cached_run('placement-semantics', commands['placement-semantics'])
    cached_run('release-visual-bundle', commands['release-visual-bundle'])

    assessment_template = run.evidence / "visual-assessment-template.json"
    assessment_template.write_text(json.dumps({
        "schema": 1,
        "kind": "pcb_visual_assessment",
        "verdict": "REPLACE_WITH_pass_revise_or_reject",
        "ratings": {
            "layout": None, "critical_routing": None, "return_paths": None,
            "silkscreen": None, "manufacturability": None,
        },
        "findings": [],
        "summary": "Replace placeholders only after inspecting every image in the bound release visual bundle.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts = {
        "drc_report": relative(drc_report),
        "audit": relative(audit_path),
        "footprint_equivalence": relative(footprint_path) if footprint_path.is_file() else None,
        "placement_semantics": relative(semantics_path),
        "visual_bundle": relative(visual_bundle),
        "assessment_template": relative(assessment_template),
    }
    packet = {
        "schema": 1, "kind": "release_review_packet", "success": True,
        "ready_for_assessment": True,
        "board": relative(board), "board_sha256": sha256(board),
        "placement_baseline": relative(baseline), "placement_baseline_sha256": sha256(baseline),
        "drc": {
            "categories": categories, "audit_exit_code": audit_code,
            "baseline_pass": audit.get("baseline_pass"),
            "review_required": audit.get("baseline_pass") is not True,
            "library_mismatch_refs": refs,
        },
        "artifacts": artifacts,
        "artifact_sha256": {
            name: sha256((ROOT / value).resolve())
            for name, value in artifacts.items() if isinstance(value, str)
        },
        "producer_dependencies": {
            item["path"]: item["sha256"] for item in [
                dependency(Path(__file__).resolve()),
                dependency(ROOT / "scripts/audit_kicad_release.py"),
                dependency(ROOT / "scripts/check_kicad_footprint_equivalence.py"),
                dependency(ROOT / "scripts/check_kicad_placement_semantics.py"),
                dependency(ROOT / "scripts/render_kicad_visual_bundle.py"),
                dependency(ROOT / 'scripts/run_pcb_pipeline.py'),
            ]
        },
        'step_records': updated_records,
        'uncacheable_steps': [step['name'] for step in steps if step['name'] not in updated_records],
        'checkpoint_inputs': snapshot,
        'executed_step_count': sum(not step.get('reused') for step in steps),
        'reused_step_count': sum(bool(step.get('reused')) for step in steps),
        "next_action": "inspect_visual_bundle_and_complete_assessment",
        "next_command": (
            f'py -3.14 scripts/pcbctl.py verify finalize-review --packet "{relative(run.evidence / "release-review-packet.json")}" '
            '--assessment "<completed-visual-assessment.json>"'
        ),
        "limits": "This packet prepares deterministic evidence only. It cannot create a visual verdict, DRC waiver, physical approval, or manufacturing release.",
    }
    packet_path = run.evidence / "release-review-packet.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_release_input("release_review_packet", packet_path, packet)
    finish_run(
        run, True,
        artifacts={"packet": relative(packet_path), **artifacts},
        metrics={"board_sha256": packet["board_sha256"], "drc_categories": categories, "image_count": 4,
                 'step_cache': updated_records, 'executed_steps': packet['executed_step_count'],
                 'reused_steps': packet['reused_step_count']},
    )
    print(json.dumps({
        "success": True, "board_sha256": packet["board_sha256"],
        "drc_categories": categories, "library_mismatch_refs": refs,
        "packet": relative(packet_path), "visual_bundle": relative(visual_bundle),
        "assessment_template": relative(assessment_template), "next_command": packet["next_command"],
        'executed_step_count': packet['executed_step_count'], 'reused_step_count': packet['reused_step_count'],
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
