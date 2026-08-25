import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "check_kicad_placement_semantics",
    ROOT / "scripts/check_kicad_placement_semantics.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class KicadPlacementSemanticsTests(unittest.TestCase):
    def test_minimum_pad_distance_uses_related_pads(self):
        pads = {"C1": {"1": [(1.0, 1.0)]}, "U1": {"2": [(4.0, 5.0)]}}
        relation = {"id": "decoupling", "from": {"ref": "C1", "pads": ["1"]}, "to": {"ref": "U1", "pads": ["2"]}, "aggregate": "minimum"}
        self.assertEqual(MODULE.measure_relation(pads, relation), 5.0)

    def test_maximum_nearest_covers_every_connector_contact(self):
        pads = {"J1": {"A6": [(0.0, 0.0)], "B6": [(10.0, 0.0)]}, "U2": {"3": [(1.0, 0.0)], "4": [(8.0, 0.0)]}}
        relation = {"id": "esd", "from": {"ref": "J1", "pads": ["A6", "B6"]}, "to": {"ref": "U2", "pads": ["3", "4"]}, "aggregate": "maximum_nearest"}
        self.assertEqual(MODULE.measure_relation(pads, relation), 2.0)

    def test_absolute_limit_requires_reviewed_source(self):
        contract = {
            "schema": 1,
            "status": "reviewed",
            "measurement_policy": {"regression_tolerance_mm": 0.001},
            "roles": [{"ref": "C1", "role": "decoupling"}],
            "relations": [{"id": "x", "direction": "non_increasing", "source": "spec", "max_distance_mm": 1.0}],
        }
        with self.assertRaises(SystemExit):
            MODULE.validate_contract(contract)

    def test_bounded_regression_requires_limit_and_source(self):
        contract = {
            "schema": 1, "status": "reviewed",
            "measurement_policy": {"regression_tolerance_mm": 0.001},
            "roles": [{"ref": "U2", "role": "esd"}],
            "relations": [{"id": "x", "direction": "bounded_regression", "source": "spec"}],
        }
        with self.assertRaises(SystemExit):
            MODULE.validate_contract(contract)
        contract["relations"][0].update({"max_regression_mm": 0.85, "limit_source": "evidence.json"})
        _, relations, _ = MODULE.validate_contract(contract)
        self.assertEqual(relations[0]["direction"], "bounded_regression")

    def test_mechanical_anchor_change_uses_exact_placement(self):
        self.assertTrue(MODULE.placement_changed((1.0, 2.0, 0.0), (1.0, 2.1, 0.0)))
        self.assertFalse(MODULE.placement_changed((1.0, 2.0, 0.0), (1.0, 2.0, 0.0)))


if __name__ == "__main__":
    unittest.main()
