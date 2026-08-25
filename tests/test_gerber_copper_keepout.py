from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gerber_keepout", ROOT / "scripts/check_gerber_copper_keepout.py")
gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gate)


class GerberCopperKeepoutTests(unittest.TestCase):
    def test_kicad_bbox_is_mirrored_to_gerber_y(self) -> None:
        self.assertEqual(gate.gerber_bbox([0, 0, 44, 6.05]), ((0.0, -6.05), (44.0, -0.0)))

    def test_overlap_is_strict_and_tolerates_edge_touch(self) -> None:
        keepout = ((0.0, -6.05), (44.0, 0.0))
        self.assertTrue(gate.boxes_intersect(((0.5, -5.0), (43.5, -0.5)), keepout))
        self.assertFalse(gate.boxes_intersect(((0.5, -10.0), (43.5, -6.05)), keepout))


if __name__ == "__main__":
    unittest.main()
