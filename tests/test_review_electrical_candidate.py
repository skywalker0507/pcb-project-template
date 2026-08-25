from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.review_electrical_candidate import candidate_review_passes


ROOT = Path(__file__).resolve().parents[1]


class ElectricalCandidateReviewTests(unittest.TestCase):
    def test_pristine_pre_layout_carrier_defers_physical_drc(self) -> None:
        self.assertTrue(candidate_review_passes(
            drc_exit_code=0,
            render_exit_code=0,
            topology_exit_code=0,
            violations=166,
            unconnected=171,
            pre_layout=True,
        ))

    def test_physical_candidate_cannot_defer_drc(self) -> None:
        self.assertFalse(candidate_review_passes(
            drc_exit_code=0,
            render_exit_code=0,
            topology_exit_code=0,
            violations=1,
            unconnected=0,
            pre_layout=False,
        ))

    def test_invalid_receipt_still_writes_a_machine_readable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / "invalid.json"
            receipt.write_text("{}", encoding="utf-8")
            output = root / "review"
            process = subprocess.run(
                [
                    sys.executable,
                    "scripts/review_electrical_candidate.py",
                    "--receipt",
                    str(receipt),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(process.returncode, 1)
            review = json.loads((output / "candidate-review.json").read_text(encoding="utf-8"))
            self.assertFalse(review["review_pass"])
            self.assertFalse(review["release_ready"])
            self.assertIn("invalid_build_receipt", review["error"])


if __name__ == "__main__":
    unittest.main()
