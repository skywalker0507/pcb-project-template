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
