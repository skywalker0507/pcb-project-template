from __future__ import annotations

import importlib.util
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
            with mock.patch.object(electrical_build, "ROOT", root), mock.patch.object(electrical_build, "sha256", return_value="H"*64), mock.patch.object(electrical_build, "relative", side_effect=lambda path: path.name):
                first = electrical_build.build_input_manifest(name, backend, status, "default", None)
                flow["project"]["board"] = "adopted-physical-board"
                flow_path.write_text(json.dumps(flow))
                adopted = electrical_build.build_input_manifest(name, backend, status, "default", None)
                self.assertEqual(first["fingerprint"], adopted["fingerprint"])
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
                integrity.write_text("{}", encoding="utf-8")
                self.assertIsNone(electrical_build.find_matching_build({"fingerprint": "A" * 64}))
                payload.pop("candidate_integrity")
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(electrical_build.find_matching_build({"fingerprint": "A" * 64}))
                candidate.write_text("tampered", encoding="utf-8")
                self.assertIsNone(electrical_build.find_matching_build({"fingerprint": "A" * 64}))


if __name__ == "__main__":
    unittest.main()
