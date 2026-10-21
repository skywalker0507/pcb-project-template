import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("apply_kicad_silkscreen_plan", ROOT / "scripts/apply_kicad_silkscreen_plan.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class KicadSilkscreenPlanTests(unittest.TestCase):
    def write_plan(self, payload):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "plan.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(temp.cleanup)
        return path

    def test_accepts_bounded_visibility_plan(self):
        plan = MODULE.load_plan(self.write_plan({
            "schema": 1,
            "kind": "kicad_silkscreen_plan",
            "reference_fields": [{"reference": "R1", "visible": False}],
        }))
        self.assertEqual(plan["reference_fields"][0]["reference"], "R1")

    def test_rejects_half_coordinate(self):
        with self.assertRaises(SystemExit):
            MODULE.load_plan(self.write_plan({
                "schema": 1,
                "kind": "kicad_silkscreen_plan",
                "reference_fields": [{"reference": "R1", "visible": True, "x_mm": 1.0}],
            }))

    def test_accepts_board_text_only_plan(self):
        plan = MODULE.load_plan(self.write_plan({
            "schema": 1,
            "kind": "kicad_silkscreen_plan",
            "board_texts": [{
                "text": "RESET", "x_mm": 1, "y_mm": 2,
                "size_mm": 0.8, "thickness_mm": 0.12,
            }],
        }))
        self.assertEqual(plan["board_texts"][0]["text"], "RESET")

    def test_accepts_existing_board_text_update(self):
        plan = MODULE.load_plan(self.write_plan({
            "schema": 1,
            "kind": "kicad_silkscreen_plan",
            "board_text_updates": [{"text": "RESET", "x_mm": 10, "y_mm": 12}],
        }))
        self.assertEqual(plan["board_text_updates"][0]["x_mm"], 10)


if __name__ == "__main__":
    unittest.main()
