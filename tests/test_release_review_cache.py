"""Exercise resume through the real review entrypoint, without native CAD mocks approving a release."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import artifact_store as store
import finalize_release_review as finalize
import prepare_release_review as review
import release_inputs
import run_pcb_pipeline as pipeline


class ReleaseReviewCacheTests(unittest.TestCase):
    def test_resume_plan_refresh_and_proof_tampering_preserve_review_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'config').mkdir(); (root / 'scripts').mkdir()
            board = root / 'board.kicad_pcb'; board.write_bytes(b'fixture board')
            flow = {'audit': {'baseline': 'config/baseline.json', 'routing_paths': 'config/paths.json'},
                    'manufacturing': {'assembly_parts': 'config/jlcpcb-parts.json'}}
            for name, value in [('pcb-flow', flow), ('baseline', {}), ('paths', {}), ('jlcpcb-parts', {})]:
                (root / f'config/{name}.json').write_text(json.dumps(value))
            for name in ('audit_kicad_release', 'check_kicad_footprint_equivalence',
                         'check_kicad_placement_semantics', 'render_kicad_visual_bundle', 'run_pcb_pipeline'):
                (root / f'scripts/{name}.py').write_text('VALUE=1\n')
            index, calls = {}, []
            fail_visual = True
            def publish(name, path, _):
                index[name] = (path, pipeline.sha256(path))
            def resolve(_, name):
                if name not in index: return None, None, 'missing'
                path, digest = index[name]
                value = json.loads(path.read_text()) if pipeline.sha256(path) == digest else None
                return path, value, 'test_index'
            def execute(name, command, logs, accepted):
                calls.append(name)
                logs.mkdir(parents=True, exist_ok=True)
                log = logs / f'{name}.log'; log.write_text('native fixture completion')
                if name == 'release-visual-bundle' and fail_visual:
                    raise SystemExit('render interrupted')
                for flag in ('--audit-json', '--drc-report', '--output', '--receipt'):
                    if flag not in command: continue
                    path = Path(command[command.index(flag) + 1]); path.parent.mkdir(parents=True, exist_ok=True)
                    if flag == '--drc-report':
                        path.write_text('[lib_footprint_mismatch]:\n    Footprint U1 differs\n')
                    elif flag == '--audit-json':
                        path.write_text(json.dumps({'drc_categories': {'lib_footprint_mismatch': 1}, 'baseline': {},
                                                    'unconnected_signatures': [], 'baseline_pass': False}))
                    else:
                        path.write_text(json.dumps({'success': True, 'review_approved': False}))
                        if name == 'release-visual-bundle':
                            (path.parent / 'view.png').write_bytes(b'fixture image; not a review')
                return 4 if name == 'release-audit' else 0, log
            argv = ['review', '--board', str(board), '--placement-baseline', str(board)]
            with mock.patch.object(store, 'ROOT', root), mock.patch.object(store, 'CURRENT_INDEX', root / 'build/CURRENT.json'), \
                 mock.patch.object(pipeline, 'ROOT', root), mock.patch.object(review, 'ROOT', root), \
                 mock.patch.object(finalize, 'ROOT', root), \
                 mock.patch.object(release_inputs, 'ROOT', root), \
                 mock.patch.object(review, 'adopted_routing_board', return_value=board), \
                 mock.patch.object(review, 'register_release_input', side_effect=publish), \
                 mock.patch.object(review, 'resolve_release_input', side_effect=resolve), \
                 mock.patch.object(review, 'run_step', side_effect=execute), \
                 mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, 'render interrupted'):
                    review.main()
                self.assertNotIn('release_review_packet', index)
                failed = json.loads(index['release_review_progress'][0].read_text())
                self.assertEqual(failed['status'], 'failed')
                self.assertEqual(len(failed['metrics']['step_cache']), 3)
                self.assertEqual(failed['metrics']['executed_steps'], 4)
                calls.clear(); argv.append('--plan')
                previous_index = dict(index)
                self.assertEqual(review.main(), 0)
                self.assertEqual(index, previous_index)
                self.assertEqual(calls, [])
                argv.pop(); fail_visual = False
                self.assertEqual(review.main(), 0)
                self.assertEqual(calls, ['release-visual-bundle'])
                packet = json.loads(index['release_review_packet'][0].read_text())
                self.assertEqual(packet['executed_step_count'], 1)
                self.assertEqual(packet['reused_step_count'], 3)
                finalize.require_current_review_inputs(packet, board)
                added = root / 'config/new-input.json'; added.write_text('{}')
                with self.assertRaisesRegex(SystemExit, 'inputs changed'):
                    finalize.require_current_review_inputs(packet, board)
                added.unlink()
                self.assertTrue(packet['ready_for_assessment'])
                self.assertNotIn('release_approved', packet)
                assessment = root / packet['artifacts']['assessment_template']
                self.assertEqual(json.loads(assessment.read_text())['verdict'], 'REPLACE_WITH_pass_revise_or_reject')
                calls.clear(); before = dict(index)
                self.assertEqual(review.main(), 0)
                self.assertEqual(index, before)
                self.assertEqual(calls, [])
                argv.extend(['--refresh-step', 'footprint-equivalence'])
                self.assertEqual(review.main(), 0)
                self.assertEqual(calls, ['footprint-equivalence'])
                argv[-2:] = []; calls.clear()
                # An original image remains part of the proof after copying.
                packet = json.loads(index['release_review_packet'][0].read_text())
                record = packet['step_records']['release-visual-bundle']
                image = next(Path(path) for path in record.get('origin_files', record['files']) if path.endswith('.png'))
                image.write_bytes(b'tampered fixture')
                with self.assertRaisesRegex(SystemExit, 'step evidence'):
                    finalize.require_current_review_inputs(packet, board)
                self.assertEqual(review.main(), 0)
                self.assertEqual(calls, ['release-visual-bundle'])

    def test_no_record_or_incomplete_outputs_never_reuse(self):
        for record in (None, [], {}, {'step': {'success': True}, 'inputs': {}},
                       {'step': {'success': True}, 'inputs': {}, 'files': {}, 'required_outputs': [],
                        'lanes': {'work': 'tmp', 'evidence': 'evidence'}}):
            with self.subTest(record=record):
                self.assertFalse(pipeline.reusable_step(record, {}))

    def test_restore_rejects_lanes_outside_project_artifact_store(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            source = root / 'private.txt'; source.write_text('not an artifact')
            record = {'files': {str(source): pipeline.sha256(source)},
                      'lanes': {'evidence': str(root), 'work': str(root)}}
            with mock.patch.object(pipeline, 'ROOT', root):
                with self.assertRaisesRegex(SystemExit, 'artifact store'):
                    pipeline.restore_step(record, root / 'build/evidence/run', root / 'build/tmp/run')
            self.assertFalse((root / 'build').exists())


if __name__ == '__main__':
    unittest.main()
