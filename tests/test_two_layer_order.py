import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from apply_kicad_stack_plan import two_layer_order


class TwoLayerOrderTests(unittest.TestCase):
    def test_reviewed_nominal_order_does_not_require_a_four_layer_impedance_template(self):
        profile = {"selected": True, "order_profile": {"status": "reviewed", "fabrication": {"layers": 2, "board_thickness_mm": 1.6}}}
        self.assertEqual(two_layer_order(profile, {"controlled_impedance": {}})["board_thickness_mm"], 1.6)

    def test_missing_review_bad_thickness_and_impedance_requirements_are_rejected(self):
        profile = {"selected": True, "order_profile": {"status": "reviewed", "fabrication": {"layers": 2, "board_thickness_mm": 1.6}}}
        for modified in [
            {**profile, "selected": False},
            {**profile, "order_profile": {"status": "draft", "fabrication": profile["order_profile"]["fabrication"]}},
            {**profile, "order_profile": {"status": "reviewed", "fabrication": {"layers": 4, "board_thickness_mm": 1.6}}},
            {**profile, "order_profile": {"status": "reviewed", "fabrication": {"layers": 2, "board_thickness_mm": -1}}},
        ]:
            with self.subTest(profile=modified), self.assertRaises(SystemExit):
                two_layer_order(modified, {})
        with self.assertRaises(SystemExit):
            two_layer_order(copy.deepcopy(profile), {"controlled_impedance": {"USB": {"ohms": 90}}})
