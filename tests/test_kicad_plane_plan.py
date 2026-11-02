import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import apply_kicad_plane_plan as planes


class PlaneCandidateTests(unittest.TestCase):
    def test_outer_layers_require_isolation_and_do_not_overwrite_input_or_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.kicad_pcb"
            source.write_text("retained source")
            output = Path(directory) / "candidate.kicad_pcb"
            for args in [(None, None), (source, None), (None, output), (source, source)]:
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    planes.candidate_paths(*args, "B.Cu")
            self.assertEqual(planes.candidate_paths(source, output, "B.Cu"), (source.resolve(), output.resolve()))
            self.assertFalse(output.exists())
            output.write_text("retained candidate")
            with self.assertRaises(SystemExit):
                planes.candidate_paths(source, output, "F.Cu")
            self.assertEqual(source.read_text(), "retained source")
            self.assertEqual(output.read_text(), "retained candidate")

    def test_legacy_inner_plane_target_remains_supported(self):
        with patch.object(planes, "configured_board", return_value=Path("active.kicad_pcb")):
            source, output = planes.candidate_paths(None, None, "In1.Cu")
            self.assertEqual(source, output)

    def test_plan_rejects_non_copper_and_non_finite_coordinates(self):
        plan = {"schema": 1, "kind": "kicad_plane_plan", "layer": "F.Cu", "net": "GND", "outline_mm": [[0, 0], [40, 0], [40, 30], [0, 30]]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            for layer in ("F.Cu", "B.Cu", "In1.Cu", "In2.Cu"):
                path.write_text(json.dumps({**plan, "layer": layer}))
                self.assertEqual(planes.load_plan(path)["layer"], layer)
            for invalid in ({**plan, "layer": "F.CrtYd"}, {**plan, "outline_mm": [[0, 0], [40, float("nan")], [0, 30]]}, {**plan, "outline_mm": [[0, 0], [True, 2], [0, 30]]}):
                path.write_text(json.dumps(invalid))
                with self.assertRaises(SystemExit):
                    planes.load_plan(path)
