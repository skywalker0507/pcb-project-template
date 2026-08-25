import importlib.util
from pathlib import Path
import sys
import unittest


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
