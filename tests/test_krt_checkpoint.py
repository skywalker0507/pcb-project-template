import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("krt_checkpoint", ROOT / "scripts/krt_checkpoint.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class KrtCheckpointTests(unittest.TestCase):
    def test_terminal_manifest_wins_over_process_state(self):
        result = MODULE.classify_state({}, {"status": "complete", "success": True}, True)
        self.assertTrue(result["terminal"])
        self.assertTrue(result["success"])
        self.assertEqual(result["next"], "finalize")

    def test_running_manifest_is_non_terminal(self):
        result = MODULE.classify_state({}, {"status": "in_progress"}, True)
        self.assertEqual(result["state"], "running")
        self.assertFalse(result["terminal"])

    def test_dead_process_without_terminal_manifest_is_orphaned(self):
        result = MODULE.classify_state({}, {"status": "in_progress"}, False)
        self.assertEqual(result["state"], "orphaned")
        self.assertTrue(result["terminal"])
        self.assertFalse(result["success"])

    def test_start_forwards_route_options_without_separator(self):
        args = MODULE.parse_cli([
            "start", "--run-id", "demo", "--profile", "outer_low_risk",
            "--input", "board.kicad_pcb", "--nets", "A", "B",
        ])
        self.assertEqual(args.command, "start")
        self.assertEqual(args.run_id, "demo")
        self.assertEqual(args.route_args, [
            "--profile", "outer_low_risk", "--input", "board.kicad_pcb",
            "--nets", "A", "B",
        ])

    def test_start_strips_optional_separator(self):
        args = MODULE.parse_cli([
            "start", "--run-id", "demo", "--", "--profile", "outer_low_risk",
            "--input", "board.kicad_pcb",
        ])
        self.assertEqual(args.route_args, [
            "--profile", "outer_low_risk", "--input", "board.kicad_pcb",
        ])


if __name__ == "__main__":
    unittest.main()
