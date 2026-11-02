"""The reusable checkpoint contract must work outside a PCB project checkout."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EngineCheckpointTests(unittest.TestCase):
    def test_isolated_engine_restores_original_receipt_format_and_binds_origin_bytes(self):
        # Isolated Python has neither the project adapters nor their globals.
        code = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from pcb_template_engine import checkpoint as cache
root = Path(sys.argv[2])
store = root / 'artifacts'
evidence, work = store / 'evidence/one', store / 'work/one'
evidence.mkdir(parents=True); work.mkdir(parents=True)
before = cache.step_files(evidence, work)
output = evidence / 'report.json'; output.write_text('{"success":true}')
inputs = {'cacheable': True, 'command': ['checker', '--output', '{evidence}/report.json']}
record = cache.step_record(inputs, {'success': True}, before, evidence, work)
assert set(record) == {'inputs', 'step', 'files', 'required_outputs', 'lanes'}
assert cache.reusable_step(record, inputs, root)
step, restored = cache.restore_step(record, store / 'evidence/two', store / 'work/two', store)
assert step['reused'] and step['duration_ms'] == 0
assert cache.reusable_step(restored, inputs, root)
assert (store / 'evidence/two/report.json').read_bytes() == output.read_bytes()
output.write_text('changed origin')
assert not cache.reusable_step(restored, inputs, root)
print(json.dumps({'api': cache.CHECKPOINT_API_VERSION, 'restored': True, 'origin_tamper_rejected': True}))
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-I", "-c", code,
                                     str(ROOT / "template-engine/src"), directory],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"api": 1, "restored": True, "origin_tamper_rejected": True})

    def test_other_project_artifacts_cannot_be_materialized_into_current_project(self):
        sys.path.insert(0, str(ROOT / "template-engine/src"))
        from pcb_template_engine import checkpoint as cache
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original/evidence"
            original.mkdir(parents=True)
            report = original / "report.json"
            report.write_text("original proof")
            record = {"files": {str(report): cache.sha256(report)},
                      "lanes": {"evidence": str(original), "work": str(root / "original/work")}}
            destination = root / "other/artifacts"
            with self.assertRaisesRegex(SystemExit, "artifact store"):
                cache.restore_step(record, destination / "evidence", destination / "work", destination)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
