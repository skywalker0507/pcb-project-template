import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "summarize_kicad_routing_geometry",
    ROOT / "scripts/summarize_kicad_routing_geometry.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RoutingGeometrySummaryTests(unittest.TestCase):
    def test_bbox_excursion_reports_each_side_without_rejecting(self):
        result = MODULE.bbox_excursion((4.7, 9.25, 24.8, 42.1), (12.0, 9.0, 24.0, 42.0))
        self.assertTrue(result["outside"])
        self.assertEqual(result["max_mm"], 7.3)
        self.assertEqual(result["by_side_mm"]["left"], 7.3)
        self.assertEqual(result["by_side_mm"]["right"], 0.8)

    def test_bbox_inside_reference_has_no_excursion(self):
        result = MODULE.bbox_excursion((13.0, 10.0, 23.0, 40.0), (12.0, 9.0, 24.0, 42.0))
        self.assertFalse(result["outside"])
        self.assertEqual(result["max_mm"], 0.0)


if __name__ == "__main__":
    unittest.main()
