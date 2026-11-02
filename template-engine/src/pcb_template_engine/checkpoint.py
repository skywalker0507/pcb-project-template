"""Project-independent checkpoint evidence validation and materialization.

Callers supply project/artifact roots. This module never reads project policy,
selects a board, executes a checker, or grants stage/release approval.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

CHECKPOINT_API_VERSION = 1


def last_json(output: str) -> dict[str, object] | None:
    for line in reversed(output.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def dependencies_fresh(dependencies: object, project_root: Path) -> bool:
    if not isinstance(dependencies, dict) or not dependencies:
        return False
    for value, expected in dependencies.items():
        path = (project_root / str(value)).resolve()
        if not path.is_file() or sha256(path) != str(expected).upper():
            return False
    return True


def canonical_step_path(arg: str, evidence: Path, work: Path) -> str:
    for label, base in (('{evidence}', evidence), ('{work}', work)):
        prefix = str(base)
        if arg == prefix or arg.startswith(prefix + '/') or arg.startswith(prefix + '\\'):
            return label + arg[len(prefix):].replace('\\', '/')
    return arg


def step_files(evidence: Path, work: Path) -> dict[str, str]:
    return {str(path.resolve()): sha256(path) for lane in (evidence, work)
            for path in lane.rglob('*') if path.is_file() and path != evidence / 'run.json'}


def reusable_step(record: object, inputs: dict, project_root: Path) -> bool:
    return bool(isinstance(record, dict) and record.get('inputs') == inputs
                and isinstance(inputs, dict) and inputs.get('cacheable') is True
                and isinstance(record.get('step'), dict) and record['step'].get('success') is True
                and isinstance(record.get('lanes'), dict) and {'evidence', 'work'}.issubset(record['lanes'])
                and isinstance(record.get('files'), dict) and record['files']
                and isinstance(record.get('required_outputs'), list) and record['required_outputs']
                and all(isinstance(path, str) and path in record['files'] for path in record['required_outputs'])
                and dependencies_fresh(record['files'], project_root)
                and (not record.get('origin_files') or dependencies_fresh(record['origin_files'], project_root)))


def step_record(inputs: dict, step: dict, before: dict, evidence: Path, work: Path) -> dict | None:
    if not inputs.get('cacheable'):
        return None
    after = step_files(evidence, work)
    files = {key: value for key, value in after.items() if before.get(key) != value}
    flags = {'--output', '--report', '--receipt', '--audit-json', '--drc-report'}
    command = inputs['command']
    outputs = []
    for index, arg in enumerate(command[:-1]):
        if arg in flags:
            value = command[index + 1].replace('{evidence}', str(evidence)).replace('{work}', str(work))
            outputs.append(str(Path(value).resolve()))
    if not outputs or not all(path in files for path in outputs):
        return None  # No complete output contract: run next time.
    return {'inputs': inputs, 'step': step, 'files': files, 'required_outputs': outputs,
            'lanes': {'evidence': str(evidence.resolve()), 'work': str(work.resolve())}}


def restore_step(record: dict, evidence: Path, work: Path, artifact_root: Path) -> tuple[dict, dict]:
    """Materialize byte-identical outputs; preserve original proof references."""
    restored, mapping = {}, {}
    for name, digest in record['files'].items():
        source = Path(name).resolve()
        for role, destination in (('evidence', evidence), ('work', work)):
            base = Path(record['lanes'][role]).resolve()
            if not base.is_relative_to(artifact_root.resolve()) or not destination.resolve().is_relative_to(artifact_root.resolve()):
                raise SystemExit('Cached step lanes must stay in the project artifact store')
            if source.is_relative_to(base):
                target = (destination / source.relative_to(base)).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise SystemExit('Cached step output escapes its destination')
                if target.exists() and sha256(target) != digest:
                    raise SystemExit('Cached step output conflicts with current evidence')
                target.parent.mkdir(parents=True, exist_ok=True)
                if source != target:
                    shutil.copy2(source, target)
                if sha256(target) != digest:
                    raise SystemExit('Cached step materialization changed output bytes')
                restored[str(target)] = digest
                mapping[name] = str(target)
                break
        else:
            raise SystemExit('Cached step output is outside its recorded lanes')
    step = {**record['step'], 'reused': True, 'duration_ms': 0}
    return step, {**record, 'step': step, 'files': restored,
                  'required_outputs': [mapping[path] for path in record['required_outputs']],
                  'origin_files': {**record.get('origin_files', {}), **record['files']},
                  'lanes': {'evidence': str(evidence.resolve()), 'work': str(work.resolve())}}
