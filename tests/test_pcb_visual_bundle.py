import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "render_kicad_visual_bundle",
    ROOT / "scripts/render_kicad_visual_bundle.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PcbVisualBundleTests(unittest.TestCase):
    def test_review_camera_bounds_both_axes_for_wide_and_tall_boards(self):
        for ratio in (0.1, 0.75, 1.0, 4.0 / 3.0, 10.0):
            with self.subTest(ratio=ratio):
                width, height, zoom = MODULE.render_frame(ratio)
                self.assertGreater(width, 0)
                self.assertEqual(width, height)
                self.assertGreater(zoom, 0)
                self.assertLessEqual(zoom * max(1, ratio), 0.75 + 1e-10)
        with self.assertRaises(SystemExit):
            MODULE.render_frame(0)

    def test_rule_areas_are_never_drawn_as_solid_copper_and_unfilled_zones_fail_closed(self):
        rule_area = Mock()
        rule_area.GetIsRuleArea.return_value = True
        board = Mock()
        board.Zones.return_value = [rule_area]
        self.assertEqual(list(MODULE.filled_zone_polygons(board, 0)), [])
        unfilled = Mock()
        unfilled.GetIsRuleArea.return_value = False
        unfilled.GetLayerSet.return_value.Contains.return_value = True
        unfilled.IsFilled.return_value = False
        board.Zones.return_value = [unfilled]
        with self.assertRaises(SystemExit):
            list(MODULE.filled_zone_polygons(board, 0))

    def test_zone_holes_remain_empty_but_do_not_erase_copper_from_another_region(self):
        try:
            import PIL
        except ImportError:
            self.skipTest("Pillow is available in the KiCad render runtime")
        outer = ((1, 1), (18, 1), (18, 18), (1, 18))
        hole = ((5, 5), (14, 5), (14, 14), (5, 14))
        island = ((8, 8), (11, 8), (11, 11), (8, 11))
        mask = MODULE.fill_mask((20, 20), [("GND", outer, (hole,)), ("other", island, ())], lambda p: p)
        self.assertEqual(mask.getpixel((2, 2)), 255)
        self.assertEqual(mask.getpixel((6, 6)), 0)
        self.assertEqual(mask.getpixel((9, 9)), 255)

    def test_changed_geometry_crop_includes_added_and_removed_copper(self):
        stable = ("track", "GND")
        removed = ("track", "OLD")
        added = ("track", "NEW")
        bounds, counts = MODULE.changed_geometry_bounds(
            {
                stable: (10.0, 10.0, 11.0, 11.0),
                removed: (1.0, 2.0, 3.0, 4.0),
            },
            {
                stable: (10.0, 10.0, 11.0, 11.0),
                added: (20.0, 21.0, 22.0, 23.0),
            },
            2.0,
        )
        self.assertEqual(bounds, (-1.0, 0.0, 24.0, 25.0))
        self.assertEqual(counts, {"added": 1, "removed": 1})

    def test_requested_and_changed_bounds_are_unioned(self):
        self.assertEqual(
            MODULE.merge_bounds((5.0, 5.0, 10.0, 10.0), (1.0, 7.0, 20.0, 9.0)),
            (1.0, 5.0, 20.0, 10.0),
        )

    def test_no_copper_delta_has_no_automatic_crop(self):
        geometry = {("track", "GND"): (1.0, 1.0, 2.0, 2.0)}
        bounds, counts = MODULE.changed_geometry_bounds(geometry, geometry, 2.0)
        self.assertIsNone(bounds)
        self.assertEqual(counts, {"added": 0, "removed": 0})


if __name__ == "__main__":
    unittest.main()
