"""Checkpoint orchestration must save calls without accepting stale evidence."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_pcb_pipeline as pipeline
from artifact_store import ArtifactRun


class PlacementCheckpointTests(unittest.TestCase):
    def test_declared_native_python_modules_are_bound_only_to_their_consumers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            args, _, board, baseline, freeze = self.fixture(root)
            scripts = root / 'scripts'; scripts.mkdir()
            producer = scripts / 'render_kicad_visual_bundle.py'; producer.write_text('from PIL import Image\n')
            audit = scripts / 'krt_candidate_audit.py'; audit.write_text('VALUE=1\n')
            module = root / 'native/Lib/site-packages/PIL/Image.py'; module.parent.mkdir(parents=True)
            module.write_text('VALUE=1\n')
            policy = root / 'config/toolchain-policy.json'
            policy.write_text(json.dumps({'kicad': {'cli': str(root / 'native/kicad-cli.exe'), 'python_modules': ['PIL']}}))
            with mock.patch.object(pipeline, 'ROOT', root), mock.patch.object(pipeline, 'relative', lambda p: str(p.relative_to(root))):
                def signature(script):
                    return pipeline.step_inputs([sys.executable, str(script)],
                        pipeline.checkpoint_dependencies(board, baseline, args.config, freeze), Path('evidence'), Path('tmp'))
                render_before, audit_before = signature(producer), signature(audit)
                self.assertTrue(render_before['cacheable'])
                module.write_text('VALUE=2\n')
                self.assertNotEqual(signature(producer), render_before)
                self.assertEqual(signature(audit), audit_before)
                policy.write_text(json.dumps({'kicad': {'cli': str(root / 'native/kicad-cli.exe')}}))
                self.assertFalse(signature(producer)['cacheable'])

    def test_step_dependencies_separate_rendering_and_sourcing_and_bind_shared_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args, _, board, baseline, freeze = self.fixture(root)
            scripts = root / 'scripts'; scripts.mkdir()
            producer = scripts / 'render_kicad_visual_bundle.py'; producer.write_text('import shared_render\n')
            shared = scripts / 'shared_render.py'; shared.write_text('VALUE=1\n')
            unrelated = scripts / 'prepare_jlcpcb_dfm_candidate.py'; unrelated.write_text('VALUE=1\n')
            parts = root / 'config/jlcpcb-parts.json'; parts.write_text('{}')
            command = [sys.executable, str(producer), '--board', str(board), '--receipt', 'evidence/visual.json']
            with mock.patch.object(pipeline, 'ROOT', root), mock.patch.object(pipeline, 'relative', lambda p: str(p.relative_to(root))):
                def inputs():
                    snapshot = pipeline.checkpoint_dependencies(board, baseline, args.config, freeze)
                    return pipeline.step_inputs(command, snapshot, Path('evidence'), Path('tmp'))
                original = inputs()
                unrelated.write_text('VALUE=2\n'); parts.write_text('{"sourcing":"changed"}')
                self.assertEqual(inputs(), original)
                shared.write_text('VALUE=2\n')
                self.assertNotEqual(inputs(), original)
                shared.write_text('VALUE=1\n')
                board.with_suffix('.kicad_dru').write_text('(rules changed)')
                self.assertNotEqual(inputs(), original)
                board.with_suffix('.kicad_dru').unlink()
                producer.write_text('import importlib\n')
                dynamic = inputs()
                unrelated.write_text('VALUE=3\n')
                self.assertNotEqual(inputs(), dynamic)

    def test_failed_checkpoint_resumes_then_only_reruns_changed_or_tampered_steps(self):
        for failure in ('returncode', 'exception'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                args, config, board, baseline, freeze = self.fixture(root)
                scripts = root / 'scripts'; scripts.mkdir()
                commands = pipeline.placement_checkpoint_commands(board, baseline, freeze, 'moved', Path('evidence'), Path('tmp'))
                for _, command in commands:
                    (scripts / Path(command[1]).name).write_text('VALUE=1\n')
                parts = root / 'config/jlcpcb-parts.json'; parts.write_text('{}')
                runs, calls = [], []
                fail = True
                def create(*_):
                    identity = str(len(runs) + 1)
                    evidence = root / 'build/05-placement/evidence' / identity
                    work = root / 'build/05-placement/tmp' / identity
                    evidence.mkdir(parents=True); work.mkdir(parents=True)
                    run = ArtifactRun('physical', '05-placement', identity, 'pcb-audit', work, evidence, root / 'release', evidence / 'run.json')
                    runs.append(run)
                    return run
                def execute(name, command, _):
                    calls.append(name)
                    if fail and name == 'routability':
                        if failure == 'exception': raise SystemExit('interrupted native checker')
                        return {'name': name, 'success': False, 'log_bytes': 0}
                    flag = next(flag for flag in ('--receipt', '--report', '--output') if flag in command)
                    output = Path(command[command.index(flag) + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps({'success': True, 'name': name,
                                                  'producer': Path(command[1]).read_text()}))
                    return {'name': name, 'success': True, 'log_bytes': 0}
                def summary():
                    return json.loads((runs[-1].evidence / 'pipeline-summary.json').read_text())
                with mock.patch.object(pipeline, 'ROOT', root), mock.patch.object(pipeline, 'relative', lambda p: str(p.relative_to(root))), \
                     mock.patch.object(pipeline, 'current_stage', return_value={'stage': 'physical'}), \
                     mock.patch.object(pipeline, 'create_run', side_effect=create), \
                     mock.patch.object(pipeline, 'run_step', side_effect=execute), mock.patch.object(pipeline, 'finish_run'), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if failure == 'exception':
                        with self.assertRaisesRegex(SystemExit, 'interrupted'):
                            pipeline.run_placement_checkpoint(args, config, board, baseline)
                    else:
                        self.assertEqual(pipeline.run_placement_checkpoint(args, config, board, baseline), 1)
                    self.assertFalse(summary()['success'])
                    self.assertEqual(len(summary()['step_records']), 2)
                    index = json.loads((runs[-1].evidence.parent / 'checkpoint-cache.json').read_text())
                    self.assertNotIn('receipt', index)
                    self.assertIn('resume_receipt', index)
                    fail = False; calls.clear()
                    self.assertEqual(pipeline.run_placement_checkpoint(args, config, board, baseline), 0)
                    self.assertEqual(calls, [name for name, _ in commands][2:])
                    self.assertEqual(summary()['reused_step_count'], 2)
                    self.assertFalse(summary()['release_pass'])
                    calls.clear()
                    pipeline.run_placement_checkpoint(args, config, board, baseline)
                    self.assertEqual(calls, [])
                    self.assertEqual(len(runs), 2)
                    (scripts / 'render_kicad_visual_bundle.py').write_text('VALUE=2\n')
                    pipeline.run_placement_checkpoint(args, config, board, baseline)
                    self.assertEqual(calls, ['visual-bundle'])
                    self.assertEqual(summary()['reused_step_count'], 6)
                    calls.clear(); parts.write_text('{"sourcing":"changed"}')
                    pipeline.run_placement_checkpoint(args, config, board, baseline)
                    self.assertEqual(calls, [])
                    self.assertEqual(summary()['reused_step_count'], 7)
                    (scripts / 'extract_kicad_parity_manifest.py').write_text('VALUE=2\n')
                    pipeline.run_placement_checkpoint(args, config, board, baseline)
                    self.assertEqual(calls, ['electrical-manifest', 'electrical-parity'])
                    self.assertEqual(summary()['reused_step_count'], 5)
                    calls.clear()
                    # Corrupt an original proof behind a materialized copy.
                    record = summary()['step_records']['physical-integrity']
                    Path(record['required_outputs'][0]).write_text('tampered')
                    pipeline.run_placement_checkpoint(args, config, board, baseline)
                    self.assertEqual(calls, ['physical-integrity'])
                    self.assertEqual(summary()['reused_step_count'], 6)

    def fixture(self, root):
        def write(name, value):
            p = root / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(value), encoding="utf-8"); return p
        board = write("layout/board.kicad_pcb", "board")
        baseline = write("layout/before.kicad_pcb", "baseline")
        freeze = write("freeze/electrical-freeze.json", {"freeze_pass": True})
        write("freeze/provenance/source/part.kicad_mod", "source")
        write("config/placement-semantics.json", {"status": "reviewed"})
        write("config/placement-routability.json", {"status": "reviewed"})
        config = {"manufacturing": {"gate_receipts": {"electrical_freeze": str(freeze)}}}
        config_path = write("config/pcb-flow.json", config)
        args = argparse.Namespace(config=config_path, placement_state="moved", plan=False, reuse_checkpoint=None, run_id="test")
        return args, config, board, baseline, freeze

    def test_checkpoint_runs_once_and_reuses_without_creating_another_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config, board, baseline, _ = self.fixture(root)
            scripts = root / 'scripts'; scripts.mkdir()
            for _, command in pipeline.placement_checkpoint_commands(board, baseline, Path('freeze'), 'moved', Path('evidence'), Path('tmp')):
                (scripts / Path(command[1]).name).write_text('VALUE=1\n')
            evidence, work = root / "build/05-placement/evidence/test", root / "build/05-placement/tmp/test"
            evidence.mkdir(parents=True); work.mkdir(parents=True)
            run = ArtifactRun("physical", "05-placement", "test", "pcb-audit", work, evidence, root / "release", evidence / "run.json")
            run.manifest.write_text("running")
            def step(name, command, logs):
                flag = next(flag for flag in ('--receipt', '--report', '--output') if flag in command)
                output = Path(command[command.index(flag) + 1]); output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({'success': True}))
                return {"name": name, "success": True, "log_bytes": 0}
            def finish(*args, **kwargs):
                run.manifest.write_text("finished")
            with mock.patch.object(pipeline, "ROOT", root), mock.patch.object(pipeline, "relative", lambda p: str(p.relative_to(root))), mock.patch.object(pipeline, "current_stage", return_value={"stage": "physical"}), mock.patch.object(pipeline, "create_run", return_value=run) as create, mock.patch.object(pipeline, "run_step", side_effect=step) as execute, mock.patch.object(pipeline, "finish_run", side_effect=finish), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pipeline.run_placement_checkpoint(args, config, board, baseline), 0)
                count = execute.call_count
                self.assertEqual(pipeline.run_placement_checkpoint(args, config, board, baseline), 0)
                self.assertEqual(create.call_count, 1)
                self.assertEqual(execute.call_count, count)
                summary = json.loads((evidence / "pipeline-summary.json").read_text())
                self.assertFalse(summary["placement_adopted"])
                self.assertFalse(summary["stage_complete"])
                self.assertFalse(summary["release_pass"])
                self.assertIn("critical_route_probes", summary["pending_reviews"])
                self.assertNotIn(str(run.manifest.relative_to(root)), summary["evidence_hashes"])

    def test_dependency_and_output_changes_invalidate_cached_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, _, board, baseline, freeze = self.fixture(root)
            evidence = root / "proof.json"; evidence.write_text('{"success":true}')
            receipt = root / "summary.json"
            with mock.patch.object(pipeline, "ROOT", root), mock.patch.object(pipeline, "relative", lambda p: str(p.relative_to(root))):
                deps = lambda: pipeline.checkpoint_dependencies(board, baseline, args.config, freeze)
                original = deps()
                receipt.write_text(json.dumps({"kind": "pcb_pipeline_summary", "success": True,
                    "requested": {"placement_state": "moved"}, "checkpoint_inputs": original,
                    "evidence_hashes": {str(evidence): pipeline.sha256(evidence)}}))
                self.assertIsNotNone(pipeline.cached_placement_checkpoint(receipt, deps(), "moved"))
                for target in (board, board.with_suffix(".kicad_pro"), root / "scripts/new_checker.py", freeze.parent / "provenance/source/part.kicad_mod", root / "config/new-policy.json"):
                    with self.subTest(target=target.name):
                        before = target.read_bytes() if target.exists() else None
                        target.parent.mkdir(parents=True, exist_ok=True); target.write_text("changed")
                        self.assertIsNone(pipeline.cached_placement_checkpoint(receipt, deps(), "moved"))
                        target.unlink() if before is None else target.write_bytes(before)
                self.assertIsNone(pipeline.cached_placement_checkpoint(receipt, deps(), "unchanged"))
                evidence.write_text("tampered")
                self.assertIsNone(pipeline.cached_placement_checkpoint(receipt, deps(), "moved"))

    def test_unchanged_checks_native_immutability_and_skips_semantics(self):
        names = [name for name, _ in pipeline.placement_checkpoint_commands(Path("b"), Path("a"), Path("freeze"), "unchanged", Path("evidence"), Path("tmp"))]
        self.assertIn("placement-immutability", names)
        self.assertNotIn("placement-semantics", names)
        self.assertNotIn("routability", names)

    def test_plan_and_draft_rejection_create_no_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, config, board, baseline, _ = self.fixture(root)
            args.plan = True
            with mock.patch.object(pipeline, "ROOT", root), mock.patch.object(pipeline, "current_stage", return_value={"stage": "physical"}), mock.patch.object(pipeline, "create_run") as create, mock.patch.object(pipeline, "run_step") as execute, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pipeline.run_placement_checkpoint(args, config, board, baseline), 0)
                (root / "config/placement-semantics.json").write_text('{"status":"draft"}')
                with self.assertRaisesRegex(SystemExit, "Review project-specific"):
                    pipeline.run_placement_checkpoint(args, config, board, baseline)
                create.assert_not_called(); execute.assert_not_called()

    def test_failure_or_input_change_never_populates_a_passing_cache(self):
        for mode in ("checker_failure", "input_change"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                args, config, board, baseline, _ = self.fixture(root)
                evidence, work = root / "build/05-placement/evidence/test", root / "build/05-placement/tmp/test"
                evidence.mkdir(parents=True); work.mkdir(parents=True)
                run = ArtifactRun("physical", "05-placement", "test", "pcb-audit", work, evidence, root / "release", evidence / "run.json")
                def step(name, command, logs):
                    if mode == "input_change":
                        board.write_text("changed during check")
                    return {"name": name, "success": mode != "checker_failure", "log_bytes": 0,
                            "tail": "failure", "data": {"error": "native_checker_failed"}}
                with mock.patch.object(pipeline, "ROOT", root), mock.patch.object(pipeline, "relative", lambda p: str(p.relative_to(root))), mock.patch.object(pipeline, "current_stage", return_value={"stage": "physical"}), mock.patch.object(pipeline, "create_run", return_value=run), mock.patch.object(pipeline, "run_step", side_effect=step) as execute, mock.patch.object(pipeline, "finish_run"), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(pipeline.run_placement_checkpoint(args, config, board, baseline), 1)
                    self.assertFalse((evidence.parent / "checkpoint-cache.json").exists())
                    summary = json.loads((evidence / "pipeline-summary.json").read_text())
                    self.assertFalse(summary["success"])
                    if mode == "checker_failure":
                        self.assertEqual(execute.call_count, 1)
                        self.assertEqual(summary["executed_step_count"], 1)
                    else:
                        self.assertEqual(summary["failed_step"]["name"], "input-freshness")


if __name__ == "__main__":
    unittest.main()
