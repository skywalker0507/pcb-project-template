from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


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

    def test_drc_fingerprint_tolerates_kicad_overlapping_track_selection(self):
        common = {
            "type": "clearance",
            "severity": "error",
            "description": "Clearance violation ( clearance 0.2000 mm; actual 0.1250 mm)",
        }
        old = {**common, "items": [
            {"uuid": "short", "description": "Track [USB_5V] on B.Cu, length 2.0000 mm", "pos": {"x": 115.1, "y": 108.0}},
            {"uuid": "via", "description": "Via [V3V3] on F.Cu - B.Cu", "pos": {"x": 115.65, "y": 108.95}},
        ]}
        new = {**common, "items": [
            {"uuid": "long", "description": "Track [USB_5V] on B.Cu, length 2.9500 mm", "pos": {"x": 115.1, "y": 107.05}},
            {"uuid": "via", "description": "Via [V3V3] on F.Cu - B.Cu", "pos": {"x": 115.65, "y": 108.95}},
        ]}
        self.assertEqual(audit.violation_identity(old), audit.violation_identity(new))

    def test_protected_flags_block_equals_and_case_variants(self):
        conflicts = flow.protected_extra_conflicts([
            "--layers=F.Cu", "--Impedance=50", "--bus",
        ])
        self.assertEqual(conflicts, ["--impedance", "--layers"])

    def test_latest_krt_preserves_netclasses_without_clearance_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.kicad_pcb"
            board.write_text("(kicad_pcb)", encoding="utf-8")
            board.with_suffix(".kicad_pro").write_text(json.dumps({
                "net_settings": {
                    "classes": [{"name": "Default", "clearance": 0.2}],
                },
            }), encoding="utf-8")
            profile = {"routing": {
                "layers": ["F.Cu", "B.Cu"],
                "layer_costs": [1, 3],
                "track_width": 0.2,
                "clearance": 0.2,
                "via_size": 0.6,
                "via_drill": 0.3,
                "fab_overrides": None,
                "extra_args": [],
            }}
            state = {
                "ok": True,
                "python": "python",
                "router": "route.py",
                "netclass_mode": "native_netclasses",
            }
            with mock.patch.object(flow, "tool_state", return_value=state):
                command = flow.build_command({}, root, profile, board, root / "out.kicad_pcb", ["sig"])
            self.assertNotIn("--clearance", command)
            self.assertNotIn("--no-clamp-netclasses", command)
            self.assertIn("--no-fix-drc-settings", command)


if __name__ == "__main__":
    unittest.main()
