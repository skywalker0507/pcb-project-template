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


physical = load("physical_integrity_quality", "scripts/check_kicad_physical_integrity.py")
manufacturing = load("special_process_quality", "scripts/check_special_manufacturing_processes.py")
prepare = load("prepare_physical_quality", "scripts/prepare_kicad_physical_candidate.py")
physical_review = load("record_physical_review_quality", "scripts/record_physical_review.py")


class ProcessQualityOptimizationTests(unittest.TestCase):
    def test_active_stack_expectation_is_config_driven(self) -> None:
        copper, forbidden = physical.active_stack_expectation({
            "active": "four",
            "profiles": {"four": {
                "copper_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                "forbidden_signal_layers": ["In1.Cu", "In2.Cu"],
            }},
        })
        self.assertEqual(copper, ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
        self.assertEqual(forbidden, ["In1.Cu", "In2.Cu"])

    def test_special_process_gate_has_no_component_specific_logic(self) -> None:
        pending = manufacturing.validate_special_processes({
            "special_processes": {"custom_finish": {
                "status": "confirmation_required",
                "allowed_confirmed_outcomes": ["approved"],
                "selected_outcome": None,
                "evidence": None,
            }},
        })
        self.assertFalse(pending["success"])
        self.assertEqual(pending["pending"], ["custom_finish"])
        passed = manufacturing.validate_special_processes({
            "special_processes": {"custom_finish": {
                "status": "confirmed",
                "allowed_confirmed_outcomes": ["approved"],
                "selected_outcome": "approved",
                "evidence": "manufacturer ticket 123",
            }},
        })
        self.assertTrue(passed["success"])

    def test_physical_start_generates_reviewed_library_table(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            part = root / "src/parts/Demo"
            part.mkdir(parents=True)
            (part / "Demo.kicad_mod").write_text("(footprint Demo)", encoding="utf-8")
            (root / "config").mkdir()
            (root / "config/electrical-backend.json").write_text(json.dumps({
                "schema": 1, "active": "atopile", "backends": {"atopile": {
                    "driver": "scripts/build_atopile_isolated.py", "source_root": "src", "entry": "main.ato:App",
                    "runtime": "wsl-ato", "platform": "wsl-native", "status": "primary",
                }},
            }), encoding="utf-8")
            project = root / "elec/layout/board"
            project.mkdir(parents=True)
            with mock.patch.object(prepare, "ROOT", root):
                table, created = prepare.ensure_reviewed_fp_table(project)
            self.assertTrue(created)
            self.assertIn('(name "Demo")', table.read_text(encoding="utf-8"))

    def test_physical_review_evidence_is_exact_board_bound(self) -> None:
        board_hash = "A" * 64
        self.assertTrue(physical_review.placement_evidence_matches({
            "kind": "kicad_placement_semantics", "success": True,
            "candidate_sha256": board_hash,
        }, board_hash))
        self.assertFalse(physical_review.placement_evidence_matches({
            "kind": "kicad_placement_semantics", "success": True,
            "candidate_sha256": "B" * 64,
        }, board_hash))
        self.assertTrue(physical_review.routing_evidence_matches({
            "board_sha256": board_hash, "routed_net_metrics": [],
        }, board_hash))


if __name__ == "__main__":
    unittest.main()
