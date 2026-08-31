import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "prepare_jlcpcb_dfm_candidate", ROOT / "scripts/prepare_jlcpcb_dfm_candidate.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PrepareJlcpcbDfmCandidateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.board = Path(self.directory.name) / "candidate.kicad_pcb"
        self.audit = Path(self.directory.name) / "audit.json"
        self.board.write_bytes(b"board")
        self.board_hash = hashlib.sha256(b"board").hexdigest().upper()

    def write_audit(self, **overrides):
        payload = {
            "schema": 1,
            "success": True,
            "baseline_pass": True,
            "candidate_sha256": self.board_hash,
            "candidate_counts": {"unconnected_items": 0},
            "unconnected_delta": 0,
            "new_violations": [],
        }
        payload.update(overrides)
        self.audit.write_text(json.dumps(payload), encoding="utf-8")

    def test_accepts_exact_board_checkpoint_for_non_release_bundle(self):
        self.write_audit()
        result = MODULE.require_passing_dfm_candidate_audit(self.board, self.audit)
        self.assertEqual(result["candidate_sha256"], self.board_hash)

    def test_rejects_checkpoint_with_unconnected_items(self):
        self.write_audit(candidate_counts={"unconnected_items": 1})
        with self.assertRaises(SystemExit):
            MODULE.require_passing_dfm_candidate_audit(self.board, self.audit)

    def test_rejects_checkpoint_for_different_board(self):
        self.write_audit(candidate_sha256="0" * 64)
        with self.assertRaises(SystemExit):
            MODULE.require_passing_dfm_candidate_audit(self.board, self.audit)

    def test_gerber_zip_is_independent_of_source_mtime(self):
        source = Path(self.directory.name) / "gerber"
        source.mkdir()
        (source / "board-F_Cu.gtl").write_bytes(b"same copper")
        first = Path(self.directory.name) / "first.zip"
        second = Path(self.directory.name) / "second.zip"
        MODULE.write_deterministic_zip(source, first)
        time.sleep(0.01)
        (source / "board-F_Cu.gtl").touch()
        MODULE.write_deterministic_zip(source, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_kicad_creation_dates_are_normalized(self):
        source = Path(self.directory.name) / "normalize"
        source.mkdir()
        gerber = source / "board-F_Cu.gtl"
        drill = source / "board-PTH.drl"
        job = source / "board-job.gbrjob"
        gerber.write_text(
            "G04 Created by KiCad (PCBNEW 10) date 2026-08-27 17:00:00*\n"
            "%TF.CreationDate,2026-08-27T17:00:00+08:00*%\nX1Y1D02*\n",
            encoding="utf-8",
        )
        drill.write_text(
            "; DRILL file KiCad 10 date 2026-08-27T17:00:00\n"
            "; #@! TF.CreationDate,2026-08-27T17:00:00+08:00\nM48\n",
            encoding="utf-8",
        )
        job.write_text(json.dumps({"Header": {"CreationDate": "now"}}), encoding="utf-8")
        MODULE.normalize_kicad_export_metadata(source)
        self.assertNotIn("2026-08-27", gerber.read_text(encoding="utf-8"))
        self.assertNotIn("2026-08-27", drill.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(job.read_text(encoding="utf-8"))["Header"]["CreationDate"], "1980-01-01T00:00:00+00:00")

    def test_stdout_index_omits_nested_package_report(self):
        payload = {
            "success": True,
            "board_sha256": "A" * 64,
            "gerber_zip_sha256": "B" * 64,
            "gerber_file_count": 12,
            "bom_cpl_designators_match": True,
            "gerber_rules_pass": True,
            "work_packet_manifest": "packet.json",
            "package_sanity": {"failures": [], "checks": {"assembly_row_count": 42}, "bulk": "x" * 10000},
        }
        result = MODULE.stdout_index(payload)
        self.assertEqual(result["package_failure_count"], 0)
        self.assertEqual(result["assembly_row_count"], 42)
        self.assertNotIn("package_sanity", result)


if __name__ == "__main__":
    unittest.main()
