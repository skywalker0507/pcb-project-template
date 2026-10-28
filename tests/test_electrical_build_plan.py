from __future__ import annotations

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


electrical_build = load("electrical_build_plan", "scripts/build_electrical.py")


class ElectricalBuildPlanTests(unittest.TestCase):
    def test_plan_rejects_build_directory_option_before_running_preflight(self):
        with mock.patch.object(sys, 'argv', ['build_electrical.py', '--plan', '--output', 'wrong.json']), \
             mock.patch.object(electrical_build.source_preflight, 'run') as preflight, \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            electrical_build.main()
        self.assertEqual(result.exception.code, 2)
        preflight.assert_not_called()

    def test_plan_reports_live_review_state_without_claiming_whole_board_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            for status in ["draft", "reviewed"]:
                with self.subTest(status=status):
                    (root / "config/requirements.json").write_text(json.dumps({
                        "status": status, "unknowns": [{"id": "unmeasured-power-off"}],
                    }), encoding="utf-8")
                    (root / "config/selected-parts.json").write_text(json.dumps({
                        "status": status, "parts": [{"refs": ["U1", "R1"]}, {"refs": ["R1"]}],
                    }), encoding="utf-8")
                    stdout = io.StringIO()
                    with mock.patch.object(electrical_build, "ROOT", root), \
                         mock.patch.object(sys, "argv", ["build_electrical.py", "--plan"]), \
                         mock.patch.object(electrical_build, "read_config", return_value={}), \
                         mock.patch.object(electrical_build, "selected_backend", return_value=("test", {})), \
                         mock.patch.object(electrical_build, "validate_backend", return_value={}), \
                         mock.patch.object(electrical_build, "production_toolchain", return_value=None), \
                         mock.patch.object(electrical_build, "build_input_manifest", return_value={"fingerprint": "A"*64}), \
                         mock.patch.object(electrical_build, "find_matching_build", return_value={"receipt": "surviving.json"}), \
                         contextlib.redirect_stdout(stdout):
                        self.assertEqual(electrical_build.main(), 0)
                    plan = json.loads(stdout.getvalue())
                    self.assertEqual(json.loads((root / 'build/03-electrical/build-plan.json').read_text()), plan)
                    self.assertFalse(plan["build_required"])
                    self.assertEqual(plan["validation_scope"], "modeled_components_only")
                    self.assertEqual(plan["next_action"], "review_matching_candidate")
                    self.assertEqual(plan["review_context"]["requirements"]["status"], status)
                    self.assertEqual(plan["review_context"]["requirements"]["unknown_ids"], ["unmeasured-power-off"])
                    self.assertEqual(plan["review_context"]["selected_parts"]["declared_unique_ref_count"], 2)
                    self.assertTrue(plan["review_context"]["informational_only"])

    def test_review_context_bounds_output_without_hiding_declared_unknown_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config/requirements.json").write_text(json.dumps({
                "status": "x"*1000, "unknowns": [{"id": str(i)*1000} for i in range(20)],
            }), encoding="utf-8")
            with mock.patch.object(electrical_build, "ROOT", root):
                entry = electrical_build.review_context()["requirements"]
            self.assertEqual(entry["declared_unknown_count"], 20)
            self.assertEqual(len(entry["unknown_ids"]), 12)
            self.assertTrue(entry["unknown_ids_truncated"])
            self.assertLessEqual(len(json.dumps(entry)), 1300)

    def test_missing_or_malformed_review_metadata_stays_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config/requirements.json").write_text("[]", encoding="utf-8")
            (root / "config/selected-parts.json").write_text('{"status":"draft","parts":[{"refs":"U1"}]}', encoding="utf-8")
            with mock.patch.object(electrical_build, "ROOT", root):
                context = electrical_build.review_context()
            self.assertEqual(context["requirements"], {"availability": "unavailable", "status": None})
            self.assertIsNone(context["selected_parts"]["declared_unique_ref_count"])
            self.assertEqual(context["assembly_registry"]["availability"], "unavailable")

    def test_physical_adoption_does_not_trigger_a_new_compile_but_project_identity_does(self):
        config = electrical_build.read_config(electrical_build.DEFAULT_CONFIG)
        name, backend = electrical_build.selected_backend(config)
        status = electrical_build.validate_backend(config, name, backend)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "config").mkdir()
            flow_path = root / "config/pcb-flow.json"
            flow = {"project": {"name": "first-project", "board": "board-a"}}
            flow_path.write_text(json.dumps(flow))
            with mock.patch.object(electrical_build, "ROOT", root), mock.patch.object(electrical_build, "sha256", return_value="H"*64), mock.patch.object(electrical_build, "relative", side_effect=lambda path: path.relative_to(root if path.is_relative_to(root) else ROOT).as_posix()):
                first = electrical_build.build_input_manifest(name, backend, status, "default", None)
                flow["project"]["board"] = "adopted-physical-board"
                flow_path.write_text(json.dumps(flow))
                adopted = electrical_build.build_input_manifest(name, backend, status, "default", None)
                self.assertEqual(first["fingerprint"], adopted["fingerprint"])
                review_path = root / "config/requirements.json"
                review_path.write_text('{"status":"draft","unknowns":[{"id":"open"}]}', encoding="utf-8")
                electrical_build.review_context()
                review_path.write_text('{"status":"reviewed","unknowns":[]}', encoding="utf-8")
                self.assertEqual(adopted["fingerprint"], electrical_build.build_input_manifest(name, backend, status, "default", None)["fingerprint"])
                flow["project"]["name"] = "different-project"
                flow_path.write_text(json.dumps(flow))
                changed = electrical_build.build_input_manifest(name, backend, status, "default", None)
                self.assertNotEqual(first["fingerprint"], changed["fingerprint"])

    def test_input_fingerprint_is_stable_and_build_specific(self) -> None:
        config = electrical_build.read_config(electrical_build.DEFAULT_CONFIG)
        name, backend = electrical_build.selected_backend(config)
        status = electrical_build.validate_backend(config, name, backend)
        # CI has no installed EDA compiler, so provide a resolved receipt
        # fixture instead of depending on machine-local build evidence.
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            receipt = Path(directory) / "toolchain-resolved.json"
            receipt.write_text(json.dumps({
                "electrical_backend": {"active": name},
                name: {"version": "test-compiler-1.0"},
            }), encoding="utf-8")
            with mock.patch.object(electrical_build, "TOOLCHAIN_RECEIPT", receipt):
                toolchain = electrical_build.production_toolchain(name, status)
                first = electrical_build.build_input_manifest(name, backend, status, "default", toolchain)
                second = electrical_build.build_input_manifest(name, backend, status, "default", toolchain)
                alternate = electrical_build.build_input_manifest(name, backend, status, "alternate", toolchain)

        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(first["fingerprint"], alternate["fingerprint"])
        self.assertTrue(first["producer_dependencies"])

    def test_matching_build_requires_an_untampered_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "build/03-electrical/evidence/20260101T000000Z-electrical-build"
            candidate = root / "build/03-electrical/tmp/run/candidate.kicad_pcb"
            evidence.mkdir(parents=True)
            candidate.parent.mkdir(parents=True)
            candidate.write_text("candidate", encoding="utf-8")
            candidate_hash = electrical_build.sha256(candidate)
            receipt = evidence / "electrical-build.json"
            integrity = evidence / "integrity.json"
            integrity.write_text(json.dumps({
                "kind": "electrical_candidate_integrity", "success": True,
                "read_only_inputs_unchanged": True, "candidate_board_sha256": candidate_hash,
                "source_sha256": "S" * 64,
            }), encoding="utf-8")
            payload = {
                "success": True,
                "build_input": {"fingerprint": "A" * 64},
                "candidate_board": str(candidate),
                "candidate_board_sha256": candidate_hash,
                "contract": {"source_sha256": "S" * 64},
                "candidate_integrity": {"receipt": str(integrity), "sha256": electrical_build.sha256(integrity)},
            }
            receipt.write_text(json.dumps(payload), encoding="utf-8")

            relative = lambda path: path.resolve().relative_to(root.resolve()).as_posix()
            with mock.patch.object(electrical_build, "ROOT", root), mock.patch.object(
                electrical_build, "relative", side_effect=relative
            ):
                match = electrical_build.find_matching_build({"fingerprint": "A" * 64})
                self.assertEqual(match["candidate_board_sha256"], candidate_hash)
                normalization = evidence / "normalization.json"
                normalization.write_text(json.dumps({
                    "kind": "atopile_candidate_normalization", "success": True,
                    "exact_electrical_geometry_parity": True, "source_sha256": "S"*64,
                    "candidate_board_sha256": candidate_hash, "compiler_candidate_sha256": "R"*64,
                }), encoding="utf-8")
                payload["compiler_candidate_board_sha256"] = "R"*64
                payload["normalization"] = {"receipt": str(normalization), "sha256": electrical_build.sha256(normalization)}
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNotNone(electrical_build.find_matching_build({"fingerprint": "A"*64}))
                normalization.unlink()
                self.assertIsNone(electrical_build.find_matching_build({"fingerprint": "A"*64}))
                payload.pop("normalization")
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                integrity.write_text("{}", encoding="utf-8")
                self.assertIsNone(electrical_build.find_matching_build({"fingerprint": "A" * 64}))
                payload.pop("candidate_integrity")
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(electrical_build.find_matching_build({"fingerprint": "A" * 64}))
                candidate.write_text("tampered", encoding="utf-8")
                self.assertIsNone(electrical_build.find_matching_build({"fingerprint": "A" * 64}))


if __name__ == "__main__":
    unittest.main()
