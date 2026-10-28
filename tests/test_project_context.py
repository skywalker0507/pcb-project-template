from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from project_context import LOCAL_SELECTION, selection
import session_boot
import pcbctl


class ProjectContextTests(unittest.TestCase):
    def target(self, path):
        (path / 'scripts').mkdir(parents=True)
        (path / 'config').mkdir()
        (path / 'config/pcb-flow.json').write_text('{}')
        (path / 'scripts/pcbctl.py').write_text('')
        (path / 'scripts/session_boot.py').write_text('')

    def test_explicit_selection_overrides_local_config_and_preserves_tool_args(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / 'SIM board 项目'
            self.target(target)
            (root / LOCAL_SELECTION).write_text(json.dumps({'schema': 1, 'active_project': 'missing'}))
            selected, args = selection(root, ['--project', str(target), 'verify', 'project', '--only', 'usb'])
            self.assertEqual(selected, target.resolve())
            self.assertEqual(args, ['verify', 'project', '--only', 'usb'])

    def test_invalid_local_target_does_not_fall_back_to_old_project(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / LOCAL_SELECTION).write_text(json.dumps({'schema': 1, 'active_project': 'missing'}))
            with self.assertRaises(ValueError):
                selection(root, ['status'])
            self.assertEqual(selection(root, ['--local', 'status']), (root.resolve(), ['status']))

    def test_conflicting_or_missing_selector_arguments_are_rejected(self):
        for args in (['--project'], ['--project', '--local'], ['--local', '--project', 'x']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                selection(ROOT, args)

    def test_session_mirrors_target_workset_without_mutating_old_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / 'SIM board'
            self.target(target)
            (root / 'handoff').mkdir(); old = root / 'handoff/state.json'; old.write_text('old history')
            (root / LOCAL_SELECTION).write_text(json.dumps({'schema': 1, 'active_project': 'SIM board'}))
            (target / 'scripts/session_boot.py').write_text(
                'import pathlib,json,sys\n'
                'assert sys.argv[1:]==["--local"]\n'
                'pathlib.Path("build").mkdir(exist_ok=True)\n'
                'pathlib.Path("build/WORKSET.json").write_text(json.dumps({"stage":"requirements"}))\n'
                'print(json.dumps({"stage":"requirements","read_now":["build/WORKSET.json"]}))\n')
            out = io.StringIO()
            with mock.patch.object(session_boot, 'ROOT', root), contextlib.redirect_stdout(out):
                self.assertEqual(session_boot.main([]), 0)
            self.assertEqual(json.loads(out.getvalue())['project_root'], str(target.resolve()))
            self.assertEqual(json.loads((root / 'build/WORKSET.json').read_text())['stage'], 'requirements')
            self.assertEqual(old.read_text(), 'old history')

    def test_dispatch_delegates_once_with_explicit_local_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / 'SIM board'
            self.target(target)
            with mock.patch.object(pcbctl, 'ROOT', root), mock.patch.object(pcbctl.subprocess, 'run') as execute:
                execute.return_value.returncode = 0
                self.assertEqual(pcbctl.main(['--project', str(target), 'verify', 'project', '--plan']), 0)
                command = execute.call_args.args[0]
                self.assertEqual(command[2:], ['--local', 'verify', 'project', '--plan'])


if __name__ == '__main__':
    unittest.main()
