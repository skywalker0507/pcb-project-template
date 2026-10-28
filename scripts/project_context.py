"""Resolve an explicit project or a machine-local workspace selection."""
from __future__ import annotations

import json
from pathlib import Path

LOCAL_SELECTION = '.pcb-workspace.local.json'


def selection(root: Path, arguments: list[str]) -> tuple[Path, list[str]]:
    """Context options precede the command; forwarded tool arguments stay intact."""
    args = list(arguments)
    explicit = None
    local = False
    while args and args[0] in {'--project', '--local'}:
        option = args.pop(0)
        if option == '--local':
            if local or explicit is not None:
                raise ValueError('Use one of --project PATH or --local')
            local = True
        else:
            if local or explicit is not None or not args or args[0].startswith('--'):
                raise ValueError('Use one of --project PATH or --local')
            explicit = args.pop(0)
    root = root.resolve()
    if local:
        return root, args
    source = root / LOCAL_SELECTION
    if explicit is None and source.is_file():
        value = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or set(value) != {'schema', 'active_project'} or value['schema'] != 1:
            raise ValueError(f'Invalid local project selection: {source}')
        explicit = value['active_project']
        if not isinstance(explicit, str) or not explicit.strip():
            raise ValueError(f'Invalid active_project: {source}')
    target = (root / explicit).resolve() if explicit is not None else root
    if target != root:
        for name in ('scripts/pcbctl.py', 'scripts/session_boot.py', 'config/pcb-flow.json'):
            if not (target / name).is_file():
                raise ValueError(f'Selected PCB project lacks {name}: {target}')
    return target, args
