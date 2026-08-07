from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ElectricalCandidateReviewTests(unittest.TestCase):
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
