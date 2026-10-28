"""Run declared project checks once per exact input set; preserve proof scopes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import string
import subprocess
import sys

from artifact_store import create_run, finish_run

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'success', 'calculation_checks_pass', 'arithmetic_checks_pass'}
TOKENS = {'output', 'work', 'evidence', 'build_receipt'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected JSON object: {path}')
    return value


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def inside(root: Path, value: str | Path) -> Path:
    path = (root / value).resolve()
    path.relative_to(root.resolve())
    return path


def source_path(root: Path, value: str) -> Path:
    path = inside(root, value)
    if path.relative_to(root).parts[0] in {'tmp', 'build', '.git', '.ato'} or not path.is_file():
        raise ValueError(f'Check producer/input must be a durable project file: {value}')
    return path


def definitions(path: Path, root: Path) -> list[dict]:
    value = load(path)
    if set(value) != {'schema', 'checks'} or value['schema'] != 1 or not isinstance(value['checks'], list):
        raise ValueError('Expected schema1 project-checks with a checks array')
    seen = set()
    for item in value['checks']:
        if not isinstance(item, dict) or set(item) - {'id', 'script', 'runtime', 'args', 'inputs', 'pass_field', 'result', 'timeout_seconds'}:
            raise ValueError('Invalid project check declaration')
        name = item.get('id')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', name) or name in seen:
            raise ValueError('Check ids must be unique stable lower-case names')
        seen.add(name)
        if not isinstance(item.get('script'), str) or source_path(root, item['script']).suffix != '.py':
            raise ValueError(f'Expected a durable Python script: {name}')
        if not isinstance(item.get('runtime', 'python'), str) or not isinstance(item.get('pass_field', 'success'), str) or item.get('runtime', 'python') not in {'python', 'kicad'} or item.get('pass_field', 'success') not in FIELDS:
            raise ValueError(f'Unsupported runtime/pass_field: {name}')
        for key in ('args', 'inputs'):
            if not isinstance(item.get(key), list) or not all(isinstance(x, str) and x for x in item[key]):
                raise ValueError(f'{name} requires explicit {key}')
        for argument in item['args']:
            for _, field, spec, conversion in string.Formatter().parse(argument):
                if field is not None and (field not in TOKENS or spec or conversion):
                    raise ValueError(f'Unknown argument token: {name}')
        if not isinstance(item.get('result', 'result.json'), str):
            raise ValueError(f'Invalid JSON result location: {name}')
        result = Path(item.get('result', 'result.json'))
        if result.is_absolute() or '..' in result.parts or result.suffix != '.json':
            raise ValueError(f'Invalid JSON result location: {name}')
        timeout = item.get('timeout_seconds', 120)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError(f'Invalid timeout: {name}')
    return value['checks']


def runtime(root: Path, kind: str) -> Path:
    if kind == 'python':
        return Path(sys.executable).resolve()
    policy = load(root / 'config/toolchain-policy.json')
    cli = Path(policy['kicad']['cli']).resolve()
    executable = cli.with_name('python.exe') if cli.suffix.lower() == '.exe' else Path(sys.executable)
    if not executable.is_file():
        raise ValueError('Configured KiCad Python is unavailable')
    return executable.resolve()


def build_binding(root: Path, receipt: Path | None) -> dict:
    from electrical_backend import source_sha256, read_config, selected_backend
    from electrical_candidate_contract import require_candidate_integrity
    if receipt is None:
        pointer = load(root / 'build/03-electrical/BUILD.json')
        receipt = inside(root, pointer['receipt'])
        if digest(receipt) != pointer['sha256']:
            raise ValueError('Electrical build pointer is stale')
    receipt = inside(root, receipt)
    value = load(receipt)
    if value.get('success') is not True:
        raise ValueError('A passing electrical build receipt is required')
    board = inside(root, value['candidate_board'])
    _, backend = selected_backend(read_config(root / 'config/electrical-backend.json'))
    if digest(board) != value['candidate_board_sha256'] or source_sha256(root / backend['source_root']) != value['contract']['source_sha256']:
        raise ValueError('Electrical candidate/source changed;run build --plan first')
    integrity = Path(require_candidate_integrity(value, root))
    return {'receipt': str(receipt), 'sha256': digest(receipt), 'board': str(board),
            'board_sha256': digest(board), 'integrity': str(integrity), 'integrity_sha256': digest(integrity)}


def packet(root: Path, item: dict, binding: dict | None) -> dict:
    files = {source_path(root, item['script'])}
    for pattern in item['inputs']:
        if Path(pattern).is_absolute() or '..' in Path(pattern).parts:
            raise ValueError(f'Input pattern must stay in the project: {pattern}')
        matches = [source_path(root, str(path.relative_to(root))) for path in root.glob(pattern) if path.is_file()]
        if not matches:
            raise ValueError(f'Input pattern matched no files: {pattern}')
        files.update(matches)
    executable = runtime(root, item.get('runtime', 'python'))
    value = {'definition': item, 'runner_sha256': digest(Path(__file__)),
             'runtime': {'path': str(executable), 'sha256': digest(executable)},
             'files': [{'path': path.relative_to(root).as_posix(), 'sha256': digest(path)} for path in sorted(files)]}
    if '{build_receipt}' in '\n'.join(item['args']):
        if binding is None:
            raise ValueError('Native checks require a verified electrical build')
        value['build'] = binding
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return {**value, 'fingerprint': hashlib.sha256(encoded).hexdigest().upper()}


def cached(root: Path, entry: dict | None, key: str) -> dict | None:
    if not isinstance(entry, dict) or entry.get('fingerprint') != key:
        return None
    try:
        receipt = inside(root, entry['receipt'])
        receipt.relative_to(root / 'build')
        if digest(receipt) != entry['sha256']:
            return None
        value = load(receipt)
        if value.get('fingerprint') != key or type(value.get('success')) is not bool:
            return None
        for output in value.get('outputs', []):
            path = inside(root, output['path'])
            path.relative_to(root / 'build')
            if digest(path) != output['sha256']:
                return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def execute(config: Path, *, only: list[str] | None = None, plan: bool = False,
            refresh: bool = False, build_receipt: Path | None = None) -> dict:
    root = ROOT.resolve()
    items = definitions(config, root)
    config_hash = digest(config)
    names = {item['id'] for item in items}
    if only and set(only) - names:
        raise ValueError('Unknown check ids: ' + ','.join(sorted(set(only) - names)))
    chosen = [item for item in items if not only or item['id'] in only]
    binding = build_binding(root, build_receipt) if any('{build_receipt}' in '\n'.join(item['args']) for item in chosen) else None
    base = root / 'build/03-electrical/project-checks'
    index_path = base / 'cache.json'
    index = load(index_path).get('checks', {}) if index_path.is_file() else {}
    if not isinstance(index, dict):
        raise ValueError('Project check cache index is invalid')
    packets = {item['id']: packet(root, item, binding) for item in chosen}
    reuse = {name: None if refresh else cached(root, index.get(name), value['fingerprint']) for name, value in packets.items()}
    if plan:
        return {'schema': 1, 'success': True, 'kind': 'project_check_plan', 'check_count': len(chosen),
                'checks': [{'id': item['id'], 'action': 'reuse_pass' if reuse[item['id']] and reuse[item['id']]['success'] else
                           'reuse_failure' if reuse[item['id']] else 'run'} for item in chosen],
                'scope': 'Declared project checks only;no approval or physical adoption'}
    run = create_run('electrical', 'project-checks', settings=load(root / 'config/build-artifacts.json')) if any(value is None for value in reuse.values()) else None
    rows = []
    for item in chosen:
        name = item['id']
        if reuse[name] is not None:
            rows.append({'id': name, 'success': reuse[name]['success'], 'reused': True,
                         'receipt': index[name]['receipt'], 'sha256': index[name]['sha256']})
            continue
        evidence, work = run.evidence / name, run.tmp / name
        evidence.mkdir(); work.mkdir()
        result = evidence / item.get('result', 'result.json')
        result.parent.mkdir(parents=True, exist_ok=True)
        values = {'output': str(result), 'evidence': str(evidence), 'work': str(work),
                  'build_receipt': binding['receipt'] if binding else ''}
        command = [packets[name]['runtime']['path'], str(root / item['script']), *[arg.format(**values) for arg in item['args']]]
        log = work / 'check.log'
        error = None
        exit_code = None
        try:
            with log.open('w', encoding='utf-8') as output:
                completed = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT,
                                           timeout=item.get('timeout_seconds', 120), check=False)
            exit_code = completed.returncode
            payload = load(result)
            if exit_code != 0 or payload.get(item.get('pass_field', 'success')) is not True:
                error = 'checker_failed'
            if packet(root, item, binding) != packets[name]:
                error = 'inputs_changed_during_check'
            if digest(config) != config_hash:
                error = 'check_configuration_changed_during_run'
            if binding and (digest(Path(binding['board'])) != binding['board_sha256'] or digest(Path(binding['receipt'])) != binding['sha256'] or digest(Path(binding['integrity'])) != binding['integrity_sha256']):
                error = 'electrical_artifacts_changed_during_check'
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__ + ': ' + str(exc)[-400:]
        outputs = [{'path': path.relative_to(root).as_posix(), 'sha256': digest(path)} for path in sorted(evidence.rglob('*')) if path.is_file()]
        receipt = run.evidence / (name + '.json')
        value = {'schema': 1, 'kind': 'project_check_receipt', 'id': name, 'success': error is None,
                 'fingerprint': packets[name]['fingerprint'], 'input_packet': packets[name],
                 'outputs': outputs, 'exit_code': exit_code, 'error': error, 'log': log.relative_to(root).as_posix(),
                 'scope': 'Declared check only;does not approve requirements,freeze,placement,hardware or manufacturing'}
        write(receipt, value)
        index[name] = {'receipt': receipt.relative_to(root).as_posix(), 'sha256': digest(receipt), 'fingerprint': packets[name]['fingerprint']}
        rows.append({'id': name, 'success': value['success'], 'reused': False, **index[name]})
        write(index_path, {'schema': 1, 'checks': index})
        if error in {'inputs_changed_during_check', 'electrical_artifacts_changed_during_check', 'check_configuration_changed_during_run'}:
            break
    result = {'schema': 1, 'kind': 'project_check_summary', 'success': all(row['success'] for row in rows),
              'check_count': len(rows), 'executed': sum(not row['reused'] for row in rows),
              'reused': sum(row['reused'] for row in rows), 'failed': [row['id'] for row in rows if not row['success']],
              'checks': rows, 'scope': 'Declared project checks only;not electrical/hardware/release approval',
              'config': str(config), 'config_sha256': config_hash,
              'configured_check_count': len(items), 'selected_ids': [item['id'] for item in chosen],
              'not_run': [item['id'] for item in chosen if item['id'] not in {row['id'] for row in rows}]}
    write(base / 'current.json', result)
    if run:
        write(run.evidence / 'summary.json', result)
        finish_run(run, result['success'], artifacts={'summary': (run.evidence / 'summary.json').relative_to(root).as_posix()},
                   metrics={'executed': result['executed'], 'failed': len(result['failed'])})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/project-checks.json')
    parser.add_argument('--only', action='append', help='Stable check id;repeat to select a bounded batch')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='Explicitly retry selected checks even when inputs match')
    parser.add_argument('--build-receipt', type=Path)
    parser.add_argument('--report', type=Path, help='Save aggregate JSON under build/')
    args = parser.parse_args()
    try:
        config = inside(ROOT, args.config)
        if args.report:
            report = inside(ROOT, args.report)
            report.relative_to(ROOT / 'build')
            if report.suffix != '.json':
                raise ValueError('--report requires a JSON path under build/')
        result = execute(config, only=args.only, plan=args.plan, refresh=args.refresh, build_receipt=args.build_receipt)
        if args.report:
            write(report, result)
        compact = {key: value for key, value in result.items() if key in {'success', 'kind', 'check_count', 'executed', 'reused', 'failed', 'scope'}}
        compact['details'] = str(args.report) if args.report else ('build/03-electrical/project-checks/current.json' if not args.plan else None)
        if args.plan:
            compact['checks'] = result['checks'][:12]
            compact['checks_truncated'] = len(result['checks']) > 12
        print(json.dumps(compact, separators=(',', ':')))
        return 0 if result['success'] else 1
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'success': False, 'error': 'project_check_inputs_invalid', 'details': str(exc)[-600:]}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
