from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("placement_routability", ROOT / "scripts/check_kicad_placement_routability.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PlacementRoutabilityTests(unittest.TestCase):
    def test_mst_is_shorter_than_daisy_chain_detour(self):
        length, edges = MODULE.mst([(0, 0), (1, 0), (0, 1)])
        self.assertAlmostEqual(length, 2.0)
        self.assertEqual(len(edges), 2)

    def test_crossing_excludes_shared_endpoint(self):
        self.assertTrue(MODULE.crosses(((0, 0), (2, 2)), ((0, 2), (2, 0))))
        self.assertFalse(MODULE.crosses(((0, 0), (2, 2)), ((2, 2), (3, 0))))

    def test_comparison_rejects_critical_regression(self):
        baseline = {
            "total_hpwl_mm": 10, "total_mst_mm": 10, "weighted_mst": 20, "airwire_crossings": 2,
            "board_width_mm": 10, "board_height_mm": 10, "board_bbox_area_mm2": 100,
            "critical_groups": {"usb": {"weighted_mst": 5}},
        }
        candidate = {
            "total_hpwl_mm": 9, "total_mst_mm": 9, "weighted_mst": 19, "airwire_crossings": 2,
            "board_width_mm": 10, "board_height_mm": 10, "board_bbox_area_mm2": 100,
            "critical_groups": {"usb": {"weighted_mst": 6}},
        }
        result = MODULE.compare(candidate, baseline, ["usb"])
        self.assertFalse(result["improved_without_critical_regression"])

    def test_comparison_rejects_board_outline_growth(self):
        baseline = {
            "total_hpwl_mm": 10, "total_mst_mm": 10, "weighted_mst": 20, "airwire_crossings": 2,
            "board_width_mm": 10, "board_height_mm": 10, "board_bbox_area_mm2": 100,
            "critical_groups": {},
        }
        candidate = {
            "total_hpwl_mm": 8, "total_mst_mm": 8, "weighted_mst": 16, "airwire_crossings": 1,
            "board_width_mm": 11, "board_height_mm": 10, "board_bbox_area_mm2": 110,
            "critical_groups": {},
        }
        result = MODULE.compare(candidate, baseline, [])
        self.assertTrue(result["outline_changed"])
        self.assertFalse(result["improved_without_critical_regression"])


if __name__ == "__main__":
    unittest.main()
