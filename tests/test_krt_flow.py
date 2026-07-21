from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


flow = load("krt_flow", ROOT / "scripts/krt_flow.py")
audit = load("krt_candidate_audit", ROOT / "scripts/krt_candidate_audit.py")


class KrtFlowTests(unittest.TestCase):
    def test_bounded_preview_has_hard_limit(self):
        result = flow.bounded(list(range(20)), 3)
        self.assertEqual(result, {"count": 20, "preview": [0, 1, 2], "truncated": True})

    def test_expand_nets_applies_include_and_exclude(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.kicad_pcb"
            board.write_text(
                '(kicad_pcb (net "gpio1") (net "gpio2") (net "usb_dp"))',
                encoding="utf-8",
            )
            self.assertEqual(flow.expand_nets(board, ["*"], ["usb*"]), ["gpio1", "gpio2"])

    def test_board_metrics_handles_quoted_layer_name(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.kicad_pcb"
            board.write_text(
                '(kicad_pcb\n'
                '  (segment (start 0 0) (end 1 1) (width 0.2) (layer "Signal Inner") (net 1))\n'
                '  (via (at 1 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))\n'
                ')',
                encoding="utf-8",
            )
            metrics = flow.board_metrics(board, ["Signal Inner"])
            self.assertEqual(metrics["forbidden_segments"], {"Signal Inner": 1})
            self.assertEqual(metrics["vias"], 1)

    def test_reconciliation_success_removes_initial_failure(self):
        output = "\n".join([
            'JSON_SUMMARY: {"routed_single":["a"],"failed_single":["b"],"failed_multipoint":[],"multipoint_pads_total":10}',
            'JSON_SUMMARY: {"routed_single":["b"],"failed_single":[],"failed_multipoint":[],"multipoint_pads_total":0}',
        ])
        result = flow.parse_router_summaries(output)
        self.assertEqual(result["failed_single"], [])
        self.assertEqual(result["routed_single"], ["a", "b"])
        self.assertEqual(result["summary_record_count"], 2)

    def test_drc_fingerprint_detects_same_count_replacement(self):
        old = {"type": "clearance", "severity": "error", "items": [{"uuid": "old"}]}
        new = {"type": "clearance", "severity": "error", "items": [{"uuid": "new"}]}
        self.assertNotEqual(audit.violation_identity(old), audit.violation_identity(new))

    def test_protected_flags_block_equals_and_case_variants(self):
        conflicts = flow.protected_extra_conflicts([
            "--layers=F.Cu", "--Impedance=50", "--bus",
        ])
        self.assertEqual(conflicts, ["--impedance", "--layers"])


if __name__ == "__main__":
    unittest.main()
