from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load():
    spec = importlib.util.spec_from_file_location(
        "prepare_krt_reroute_input", ROOT / "scripts/prepare_krt_reroute_input.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


reroute = load()


class PrepareKrtRerouteInputTests(unittest.TestCase):
    def test_critical_probe_component_must_belong_to_reviewed_group(self):
        group, component = reroute.validate_critical_probe(
            "usb_power_and_3v3", "usb_vbus_raw_escape"
        )
        self.assertEqual(group, "usb_power_and_3v3")
        self.assertEqual(component, "usb_vbus_raw_escape")
        with self.assertRaises(SystemExit):
            reroute.validate_critical_probe("usb_power_and_3v3", "raw_fanout")

    def test_strips_only_selected_net_copper(self):
        board = """(kicad_pcb
  (net 1 "KEEP")
  (net 2 "REDO")
  (segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 1))
  (segment (start 0 1) (end 1 1) (width 0.2) (layer "F.Cu") (net 2))
  (via (at 1 1) (size 0.45) (drill 0.2) (layers "F.Cu" "B.Cu") (net 2))
)"""
        stripped, removed = reroute.strip_selected_copper(board, {"REDO"})
        self.assertIn("(net 1)", stripped)
        self.assertNotIn("(start 0 1)", stripped)
        self.assertNotIn("(via (at 1 1)", stripped)
        self.assertEqual(removed, {"REDO": 2})

    def test_empty_scope_preserves_text(self):
        board = '(kicad_pcb (net 1 "KEEP"))'
        stripped, removed = reroute.strip_selected_copper(board, {"REDO"})
        self.assertEqual(stripped, board)
        self.assertEqual(removed, {})

    def test_bbox_preserves_selected_copper_outside_region(self):
        board = """(kicad_pcb
  (net 2 "REDO")
  (segment (start 1 1) (end 2 1) (width 0.2) (layer "F.Cu") (net 2))
  (segment (start 20 20) (end 21 20) (width 0.2) (layer "F.Cu") (net 2))
)"""
        stripped, removed = reroute.strip_selected_copper(
            board, {"REDO"}, (0.0, 0.0, 5.0, 5.0)
        )
        self.assertNotIn("(start 1 1)", stripped)
        self.assertIn("(start 20 20)", stripped)
        self.assertEqual(removed, {"REDO": 1})


if __name__ == "__main__":
    unittest.main()
