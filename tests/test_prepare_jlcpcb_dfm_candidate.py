import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
