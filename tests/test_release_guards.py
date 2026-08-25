from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


audit = load("audit_kicad_release", "scripts/audit_kicad_release.py")
export = load("export_jlcpcb_release", "scripts/export_jlcpcb_release.py")


class ReleaseGuardTests(unittest.TestCase):
    def test_four_layer_export_accepts_power_plane_layer_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.kicad_pcb"
            board.write_text(
                '(kicad_pcb (layers (0 "F.Cu" signal) (4 "In1.Cu" power) '
                '(6 "In2.Cu" power) (2 "B.Cu" signal)))', encoding="utf-8",
            )
            stack = root / "stack.json"
            stack.write_text(json.dumps({
                "active": "four",
                "profiles": {"four": {
                    "copper_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                    "gerber_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                }},
            }), encoding="utf-8")
            self.assertEqual(
                export.configured_layers(board, stack),
                "F.Cu,In1.Cu,In2.Cu,B.Cu",
            )

    def test_empty_baseline_rejects_unconnected_items(self) -> None:
        report = "[unconnected_items]:\n@(1,2): U1.1 -> U2.1\n"
        unexpected, missing = audit.baseline_differences(
            report, audit.Counter({"unconnected_items": 1}),
            {"allowed_violation_items": {}, "expected_category_counts": {}},
        )
        self.assertEqual(missing, [])
        self.assertEqual(len(unexpected), 1)
        self.assertIn("unconnected_items", unexpected[0])

    def test_allowlist_requires_exact_drc_objects(self) -> None:
        report = "[clearance]:\n@(1,2): Track A\n"
        unexpected, missing = audit.baseline_differences(
            report, audit.Counter({"clearance": 1}),
            {"allowed_violation_items": {"clearance": ["@(1,2): Track A"]}},
        )
        self.assertEqual(unexpected, [])
        self.assertEqual(missing, [])

    def test_board_bound_baseline_metadata_does_not_weaken_exact_items(self) -> None:
        report = "[silk_overlap]:\n@(1,2): Segment A\n"
        unexpected, missing = audit.baseline_differences(
            report, audit.Counter({"silk_overlap": 1}),
            {
                "reviewed_board_sha256": "A" * 64,
                "allowed_violation_items": {"silk_overlap": ["@(1,2): Segment A"]},
                "expected_category_counts": {"silk_overlap": 1},
            },
        )
        self.assertEqual(unexpected, [])
        self.assertEqual(missing, [])

    def test_export_requires_matching_passing_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.kicad_pcb"
            board.write_text("(kicad_pcb)", encoding="utf-8")
            audit_path = root / "audit.json"
            audit_path.write_text(json.dumps({
                "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
                "release_pass": True,
            }), encoding="utf-8")
            self.assertTrue(export.require_passing_audit(board, audit_path)["release_pass"])
            audit_path.write_text(json.dumps({"board_sha256": "wrong", "release_pass": True}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                export.require_passing_audit(board, audit_path)


if __name__ == "__main__":
    unittest.main()
