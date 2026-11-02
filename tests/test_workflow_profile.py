from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


profile = load("workflow_profile_tests", "scripts/profile_workflow.py")
artifact_store = load("artifact_store_reference_tests", "scripts/artifact_store.py")


class WorkflowProfileTests(unittest.TestCase):
    def test_pruning_follows_current_receipts_to_original_proof_hash_map_keys(self):
        for pointer in ('release', 'checkpoint'):
            with self.subTest(pointer=pointer), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                origin = root / 'build/07-release/tmp/old-origin'; origin.mkdir(parents=True)
                image = origin / 'proof.png'; image.write_bytes(b'original proof')
                proof = root / 'build/07-release/evidence/new/proof.json'; proof.parent.mkdir(parents=True)
                proof.write_text(json.dumps({'origin_files': {str(image): 'HASH'}}))
                index = root / ('build/07-release/INPUTS.json' if pointer == 'release'
                                else 'build/07-release/evidence/checkpoint-cache.json')
                index.write_text(json.dumps({'receipt': str(proof)}))
                os.utime(origin, (1, 1))
                settings = {'root': 'build', 'stages': {'release': '07-release'}, 'lanes': {'tmp': 'tmp'},
                            'retention': {'tmp_max_age_hours': 0}, 'legacy': {'archive_root': 'archive'}}
                with mock.patch.object(artifact_store, 'ROOT', root), \
                     mock.patch.object(artifact_store, 'CURRENT_INDEX', root / 'build/CURRENT.json'), \
                     mock.patch.object(artifact_store, 'STAGE_RESULTS_INDEX', root / 'build/STAGE_RESULTS.json'):
                    report = artifact_store.prune_tmp(settings, apply=False)
                self.assertEqual(report['protected_run_count'], 1)
                self.assertEqual(report['run_count'], 0)

    def test_filesystem_profile_uses_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            build = Path(folder) / "build"
            run = build / "06-routing/tmp/run-a"
            run.mkdir(parents=True)
            (run / "candidate.kicad_pcb").write_bytes(b"pcb")
            value = profile.filesystem_profile(build)
        self.assertEqual(value["files"], 1)
        self.assertEqual(value["bytes"], 3)
        self.assertEqual(value["tmp_run_dirs"], 1)

    def test_recommendations_prioritize_route_churn_before_storage(self) -> None:
        value = {
            "storage": {"by_lane": [{"name": "tmp", "bytes": 200}], "tmp_run_dirs": 20},
            "routing": {"recent_rejection_rate": 0.6},
            "pipeline": {"same_board_tier_max_repeats": 1},
            "artifact_runs": {"operations": []},
        }
        result, mitigated = profile.findings(value, {"thresholds": {
            "tmp_bytes_warning": 100,
            "recent_route_rejection_rate_warning": 0.35,
            "same_board_tier_repeat_warning": 3,
        }})
        self.assertEqual([item["code"] for item in result], ["routing_candidate_churn", "expired_tmp_pressure"])
        self.assertEqual(mitigated, [])

    def test_declaring_mitigation_does_not_hide_observed_repetition(self) -> None:
        value = {
            "storage": {"by_lane": [{"name": "tmp", "bytes": 0}], "tmp_run_dirs": 0},
            "routing": {"recent_rejection_rate": 0.0},
            "pipeline": {"same_board_tier_max_repeats": 5},
            "artifact_runs": {"operations": [], "stale_in_progress_by_operation": {}},
        }
        recommendations, mitigated = profile.findings(value, {
            "thresholds": {"same_board_tier_repeat_warning": 3},
            "implemented_mitigations": {
                "repeated_full_board_verification": {"status": "implemented"},
            },
        })
        self.assertEqual(mitigated, [])
        self.assertEqual([item["code"] for item in recommendations], ["repeated_full_board_verification"])
        self.assertEqual(recommendations[0]["status"], "implementation_declared_effect_unverified")

    def test_execution_time_does_not_include_delayed_closure_or_invent_legacy_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            build = Path(folder)
            for name, measured in (("measured", 250), ("legacy", None)):
                run = build / "07-release/evidence" / name / "run.json"
                run.parent.mkdir(parents=True)
                run.write_text(json.dumps({"operation": "review", "status": "failed", "success": False,
                                           "created_at": "2026-01-01T00:00:00+00:00",
                                           "updated_at": "2026-01-01T01:00:00+00:00",
                                           'metrics': {'executed_steps': 1, 'reused_steps': 3} if measured else {},
                                           "execution_duration_ms": measured}), encoding="utf-8")
            result = profile.artifact_run_profile(build)["operations"][0]
        self.assertEqual(result["total_seconds"], 7200)
        self.assertEqual(result["recorded_execution_seconds"], .25)
        self.assertEqual(result["measured_execution_runs"], 1)
        self.assertEqual(result["legacy_timing_runs"], 1)
        self.assertEqual(result['recorded_executed_steps'], 1)
        self.assertEqual(result['recorded_reused_steps'], 3)
        self.assertEqual(result['cache_observed_runs'], 1)

    def test_guarded_run_closes_system_exit_and_preserves_original_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            settings = {"root": "build", "stages": {"release": "07-release"}}
            with mock.patch.object(artifact_store, "ROOT", root), \
                 mock.patch.object(artifact_store, "CURRENT_INDEX", root / "build/CURRENT.json"):
                run = artifact_store.create_run("release", "review", settings=settings)
                with self.assertRaisesRegex(SystemExit, "bad baseline"):
                    with artifact_store.guard_run(run):
                        raise SystemExit("bad baseline")
                value = json.loads(run.manifest.read_text(encoding="utf-8"))
                self.assertEqual(value["status"], "failed")
                self.assertFalse(value["success"])
                self.assertEqual(value["metrics"]["reason"], "bad baseline")
                self.assertIsInstance(value["execution_duration_ms"], int)

                with self.assertRaisesRegex(RuntimeError, "later failure"):
                    with artifact_store.guard_run(run):
                        raise RuntimeError("later failure")
                self.assertEqual(json.loads(run.manifest.read_text())["metrics"], value["metrics"])

    def test_stale_in_progress_run_is_not_counted_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            build = Path(folder) / "build"
            evidence = build / "06-routing/evidence/stale"
            evidence.mkdir(parents=True)
            (evidence / "run.json").write_text(json.dumps({
                "operation": "krt", "status": "in_progress", "success": None,
                "created_at": "2020-01-01T00:00:00+00:00",
                "updated_at": "2020-01-01T00:00:01+00:00",
            }), encoding="utf-8")
            value = profile.artifact_run_profile(build)
        self.assertEqual(value["runs"], 0)
        self.assertEqual(value["stale_in_progress_runs"], 1)
        self.assertEqual(value["operations"], [])

    def test_tmp_prune_preserves_stable_pointer_target(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tmp = root / "build/06-routing/tmp"
            keep = tmp / "keep"
            drop = tmp / "drop"
            keep.mkdir(parents=True)
            drop.mkdir()
            (keep / "manifest.json").write_text("{}", encoding="utf-8")
            (drop / "candidate.txt").write_text("drop", encoding="utf-8")
            pointer = tmp / "packet-CURRENT.json"
            pointer.write_text(json.dumps({"manifest": "build/06-routing/tmp/keep/manifest.json"}), encoding="utf-8")
            old = 1
            os.utime(keep, (old, old))
            os.utime(drop, (old, old))
            settings = {
                "root": "build", "stages": {"routing": "06-routing"},
                "lanes": {"tmp": "tmp"}, "retention": {"tmp_max_age_hours": 0},
                "legacy": {"archive_root": "build/00-legacy"},
            }
            with mock.patch.object(artifact_store, "ROOT", root), \
                 mock.patch.object(artifact_store, "CURRENT_INDEX", root / "build/CURRENT.json"), \
                 mock.patch.object(artifact_store, "STAGE_RESULTS_INDEX", root / "build/STAGE_RESULTS.json"):
                result = artifact_store.prune_tmp(settings, apply=False)
        self.assertEqual(result["protected_run_count"], 1)
        self.assertEqual(result["run_count"], 1)
        self.assertEqual(result["preview"][0]["path"], "build/06-routing/tmp/drop")

    def test_interrupted_retention_cleanup_resumes_from_verified_archive(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "build/06-routing/tmp/drop"
            run.mkdir(parents=True)
            (run / "readonly.txt").write_text("archived", encoding="utf-8")
            archive = root / "build/00-legacy/retention-test.zip"
            archive.parent.mkdir(parents=True)
            manifest = {
                "schema": 1, "kind": "expired_tmp_archive", "max_age_hours": 24,
                "runs": [{"path": "build/06-routing/tmp/drop", "bytes": 8}],
            }
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("retention-manifest.json", json.dumps(manifest))
                bundle.write(run / "readonly.txt", "build/06-routing/tmp/drop/readonly.txt")
            settings = {
                "root": "build", "stages": {"routing": "06-routing"},
                "lanes": {"tmp": "tmp"}, "retention": {"tmp_max_age_hours": 24},
                "legacy": {"archive_root": "build/00-legacy"},
            }
            with mock.patch.object(artifact_store, "ROOT", root), \
                 mock.patch.object(artifact_store, "CURRENT_INDEX", root / "build/CURRENT.json"), \
                 mock.patch.object(artifact_store, "STAGE_RESULTS_INDEX", root / "build/STAGE_RESULTS.json"):
                result = artifact_store.prune_tmp(settings, apply=True)
            run_exists = run.exists()
            cleanup_complete = json.loads(archive.with_suffix(".manifest.json").read_text(encoding="utf-8"))["cleanup_complete"]
        self.assertTrue(result["resumed"])
        self.assertEqual(result["removed_run_count"], 1)
        self.assertFalse(run_exists)
        self.assertTrue(cleanup_complete)


if __name__ == "__main__":
    unittest.main()
