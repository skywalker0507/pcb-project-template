from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import create_electrical_freeze as freeze
import validate_electrical_contract as contract


def reviewed(unknowns=None):
    return {"schema": 2, "status": "reviewed", "interfaces": [{"name": "fixture"}],
            "rails": [], "connectors": [], "constraints": [], "critical_nets": [],
            "placement_regions": [], "mechanical": {}, "unknowns": unknowns or []}


class ReviewedRequirementsGateTests(unittest.TestCase):
    def run_contract(self, root, requirements):
        def save(name, data):
            path = root / name
            path.write_text(json.dumps(data), encoding="utf-8")
            return path
        board = root / "candidate.kicad_pcb"
        board.write_bytes(b"opaque board fixture; contract uses deterministic manifest")
        digest = hashlib.sha256(board.read_bytes()).hexdigest().upper()
        build = save("build.json", {"success": True, "backend": "atopile", "candidate_board": str(board)})
        manifest = save("manifest.json", {"schema": 2, "normalization": {"length_unit": "nm", "rotation_unit": "microdegree"},
                        "board_sha256": digest, "board_outline": [], "holes": [], "nets": [],
                        "components": [{"reference": "R1", "part_identity": "EXACT-ONE", "footprint": "Fixture:One",
                                        "side": "F.Cu", "rotation_udeg": 0, "pads": []}]})
        requirement_path = save("requirements.json", requirements)
        decisions = save("decisions.json", {"status": "confirmed"})
        parts = save("parts.json", {"status": "electrical_review", "parts": [{"mpn": "EXACT-ONE", "refs": ["R1"]}]})
        output = root / "result.json"
        argv = ["validate", "--receipt", str(build), "--manifest", str(manifest), "--requirements", str(requirement_path),
                "--product-decisions", str(decisions), "--selected-parts", str(parts), "--assembly-parts", str(parts), "--output", str(output)]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            code = contract.main()
        return code, json.loads(output.read_text(encoding="utf-8"))

    def test_matching_candidate_with_no_blocking_unknowns_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_contract(Path(directory), reviewed())
            self.assertEqual(code, 0, result)
            self.assertTrue(result["contract_pass"])

    def test_explicit_nonblocking_note_does_not_become_a_new_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_contract(Path(directory), reviewed([{"id": "optional-label", "blocks": []}]))
            self.assertEqual(code, 0, result)

    def test_reviewed_label_cannot_hide_blocking_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_contract(Path(directory), reviewed([{"id": "unproven-supply", "blocks": ["electrical_freeze"]}]))
            self.assertEqual(code, 1, result)
            self.assertFalse(result["contract_pass"])
            self.assertTrue(any("blocking unknowns" in issue for issue in result["record_issues"]), result)

    def test_missing_required_structure_cannot_be_approved_by_status(self):
        with tempfile.TemporaryDirectory() as directory:
            requirements = reviewed()
            requirements.pop("mechanical")
            code, result = self.run_contract(Path(directory), requirements)
            self.assertEqual(code, 1, result)
            self.assertIn("mechanical must be an object", " ".join(result["record_issues"]))

    def test_freeze_rechecks_current_requirements_before_reading_old_pass_receipts(self):
        for requirements in [reviewed([{"id": "unproven-supply", "blocks": ["electrical_freeze"]}]),
                             {**reviewed(), "status": "draft"}]:
            with self.subTest(requirements=requirements), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "requirements.json"
                path.write_text(json.dumps(requirements), encoding="utf-8")
                original = root / "preserved-physical-work"
                original.write_bytes(b"preserved")
                argv = ["freeze", "--build-receipt", str(root / "old-build.json"),
                        "--contract-receipt", str(root / "old-contract.json"), "--candidate-review", str(root / "old-review.json")]
                with mock.patch.object(freeze, "PROVENANCE_INPUTS", {"requirements": path}), \
                     mock.patch.object(freeze, "read_config", return_value={}), \
                     mock.patch.object(freeze, "selected_backend", return_value=("atopile", {"status": "primary"})), \
                     mock.patch.object(freeze, "create_run") as create, mock.patch.object(sys, "argv", argv):
                    with self.assertRaisesRegex(SystemExit, "Requirements are not ready"):
                        freeze.main()
                    create.assert_not_called()
                self.assertEqual(original.read_bytes(), b"preserved")
                self.assertFalse((root / "build").exists())

    def test_contract_binding_rechecks_current_unknowns_even_when_all_hashes_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {}
            for name, value in {"requirements": reviewed([{"id": "live-rail", "blocks": ["electrical_freeze"]}]),
                                "product_decisions": {"status": "confirmed"}, "selected_parts": {"status": "reviewed"}}.items():
                path = root / (name + ".json")
                path.write_text(json.dumps(value), encoding="utf-8")
                inputs[name] = path
            old_pass = {"success": True, "contract_pass": True,
                        "inputs": {name: {"sha256": freeze.sha256(path)} for name, path in inputs.items()}}
            with self.assertRaisesRegex(SystemExit, "Requirements are not ready"):
                freeze.verify_contract_inputs(old_pass, inputs)


if __name__ == "__main__":
    unittest.main()
