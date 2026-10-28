from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import artifact_store as store
import project_checks as runner


class ProjectCheckRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'config').mkdir()
        (self.root / 'checks').mkdir()
        (self.root / 'inputs').mkdir()
        self.config = self.root / 'config/project-checks.json'
        (self.root / 'config/build-artifacts.json').write_text(json.dumps({
            'schema': 1, 'root': 'build', 'stages': {'electrical': '03-electrical'}}))
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(runner, 'ROOT', self.root))
        self.patches.enter_context(mock.patch.object(store, 'ROOT', self.root))
        self.patches.enter_context(mock.patch.object(store, 'CURRENT_INDEX', self.root / 'build/CURRENT.json'))

    def checker(self, name, *, success=True, extra='', pass_field='success'):
        (self.root / 'checks' / (name + '.py')).write_text(
            'import json,pathlib,sys\n' + extra + '\n' +
            f"pathlib.Path(sys.argv[1]).write_text(json.dumps({{{pass_field!r}:{success!r}}}))\n",
            encoding='utf-8')
        (self.root / 'inputs' / name).write_text('input-1')
        return {'id': name, 'script': f'checks/{name}.py', 'args': ['{output}'],
                'inputs': [f'inputs/{name}'], 'pass_field': pass_field}

    def save(self, items):
        self.config.write_text(json.dumps({'schema': 1, 'checks': items}))

    def test_second_identical_batch_creates_no_new_artifact_run(self):
        self.save([self.checker('power'), self.checker('usb')])
        first = runner.execute(self.config)
        runs = list((self.root / 'build/03-electrical/evidence').iterdir())
        second = runner.execute(self.config)
        self.assertEqual((first['executed'], second['executed'], second['reused']), (2, 0, 2))
        self.assertEqual(list((self.root / 'build/03-electrical/evidence').iterdir()), runs)
        self.assertNotIn('freeze_ready', second)

    def test_only_changed_dependency_is_rechecked(self):
        self.save([self.checker('power'), self.checker('usb')])
        runner.execute(self.config)
        (self.root / 'inputs/power').write_text('input-2')
        result = runner.execute(self.config)
        self.assertEqual([(r['id'], r['reused']) for r in result['checks']], [('power', False), ('usb', True)])

    def test_changing_producer_invalidates_its_receipt(self):
        self.save([self.checker('power')])
        runner.execute(self.config)
        with (self.root / 'checks/power.py').open('a') as output:
            output.write('\n# producer revision\n')
        self.assertEqual(runner.execute(self.config)['executed'], 1)

    def test_pass_to_failure_never_falls_back_to_older_pass(self):
        item = self.checker('power')
        self.save([item]); runner.execute(self.config)
        self.checker('power', success=False)
        failure = runner.execute(self.config)
        again = runner.execute(self.config)
        self.assertFalse(failure['success'])
        self.assertFalse(again['success'])
        self.assertEqual((again['executed'], again['reused']), (0, 1))
        self.assertEqual(again['failed'], ['power'])

    def test_tampered_output_is_not_reused(self):
        self.save([self.checker('power')])
        result = runner.execute(self.config)
        proof = runner.load(self.root / result['checks'][0]['receipt'])
        (self.root / proof['outputs'][0]['path']).write_text('{}')
        self.assertEqual(runner.execute(self.config)['executed'], 1)

    def test_tampered_check_receipt_is_not_reused(self):
        self.save([self.checker('power')])
        result = runner.execute(self.config)
        (self.root / result['checks'][0]['receipt']).write_text('{}')
        self.assertEqual(runner.execute(self.config)['executed'], 1)

    def test_zero_exit_without_passing_json_is_a_failure(self):
        self.save([self.checker('power')])
        (self.root / 'checks/power.py').write_text('print("pretend success")\n')
        self.assertFalse(runner.execute(self.config)['success'])

    def test_screening_field_remains_screening_only(self):
        self.save([self.checker('power', pass_field='calculation_checks_pass')])
        result = runner.execute(self.config)
        self.assertTrue(result['success'])
        self.assertIn('not electrical/hardware/release approval', result['scope'])

    def test_missing_and_escaping_dependencies_are_rejected(self):
        item = self.checker('power')
        for pattern in ('inputs/missing*', '../outside*', 'build/*'):
            with self.subTest(pattern=pattern):
                self.save([{**item, 'inputs': [pattern]}])
                with self.assertRaises(ValueError):
                    runner.execute(self.config)

    def test_plan_and_unknown_id_do_not_execute_or_write_results(self):
        self.save([self.checker('power')])
        self.assertEqual(runner.execute(self.config, plan=True)['checks'][0]['action'], 'run')
        self.assertFalse((self.root / 'build').exists())
        with self.assertRaises(ValueError):
            runner.execute(self.config, only=['typo'])

    def test_input_mutation_aborts_batch_and_is_not_passing_evidence(self):
        self.save([self.checker('power', extra="pathlib.Path('inputs/power').write_text('mutated')"), self.checker('usb')])
        result = runner.execute(self.config)
        self.assertFalse(result['success'])
        self.assertEqual(result['not_run'], ['usb'])

    def test_explicit_refresh_retries_cached_failure(self):
        self.save([self.checker('power', success=False)])
        runner.execute(self.config)
        self.assertEqual(runner.execute(self.config, refresh=True)['executed'], 1)

    def test_candidate_mutation_aborts_remaining_checks(self):
        board = self.root / 'build/candidate.test'; board.parent.mkdir()
        board.write_text('original candidate')
        build = self.root / 'build/build.json'; build.write_text('{}')
        integrity = self.root / 'build/integrity.json'; integrity.write_text('{}')
        binding = {'receipt': str(build), 'sha256': runner.digest(build), 'board': str(board),
                   'board_sha256': runner.digest(board), 'integrity': str(integrity), 'integrity_sha256': runner.digest(integrity)}
        bad = self.checker('power', extra="pathlib.Path('build/candidate.test').write_text('changed')")
        bad['args'].append('{build_receipt}')
        self.save([bad, self.checker('usb')])
        with mock.patch.object(runner, 'build_binding', return_value=binding):
            result = runner.execute(self.config)
        self.assertFalse(result['success'])
        self.assertEqual(result['not_run'], ['usb'])

    def test_duplicate_ids_and_unrecognized_contract_fields_are_rejected(self):
        item = self.checker('power')
        for items in ([item, item], [{**item, 'expected_exit_code': 1}]):
            self.save(items)
            with self.assertRaises(ValueError):
                runner.execute(self.config)

    def test_malformed_typed_fields_report_contract_errors_before_execution(self):
        item = self.checker('power')
        for overrides in ({'runtime': []}, {'pass_field': {}}, {'result': None}, {'timeout_seconds': True}, {'args': ['{unknown}']}):
            with self.subTest(overrides=overrides):
                self.save([{**item, **overrides}])
                with self.assertRaises(ValueError):
                    runner.execute(self.config)
        self.assertFalse((self.root / 'build').exists())

    def test_replacing_config_with_extra_check_preserves_existing_check_cache(self):
        item = self.checker('power')
        self.save([item]); runner.execute(self.config)
        self.save([item, self.checker('usb')])
        result = runner.execute(self.config)
        self.assertEqual((result['executed'], result['reused']), (1, 1))

    def test_subset_result_cannot_claim_the_entire_config_ran(self):
        self.save([self.checker('power'), self.checker('usb')])
        result = runner.execute(self.config, only=['usb'])
        self.assertEqual((result['configured_check_count'], result['check_count']), (2, 1))
        self.assertEqual(result['selected_ids'], ['usb'])


if __name__ == '__main__':
    unittest.main()
