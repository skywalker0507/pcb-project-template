import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("record_pcb_visual_review", ROOT / "scripts/record_pcb_visual_review.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PcbVisualReviewTests(unittest.TestCase):
    def assessment(self):
        return {
            "schema": 1,
            "kind": "pcb_visual_assessment",
            "verdict": "revise",
            "ratings": {
                "layout": 2,
                "critical_routing": 3,
                "return_paths": 3,
                "silkscreen": 2,
                "manufacturability": 3,
            },
            "findings": [{
                "category": "layout",
                "severity": "high",
                "observation": "Critical blocks are unnecessarily far apart.",
                "action": "Rework placement before locking more copper.",
            }],
        }

    def test_accepts_structured_revise_assessment(self):
        MODULE.validate_assessment(self.assessment())

    def test_passing_review_rejects_high_findings(self):
        value = self.assessment()
        value["verdict"] = "pass"
        with self.assertRaises(SystemExit):
            MODULE.validate_assessment(value)

    def test_requires_all_visual_categories(self):
        value = self.assessment()
        del value["ratings"]["silkscreen"]
        with self.assertRaises(SystemExit):
            MODULE.validate_assessment(value)

    def test_passing_visual_review_requires_passing_semantics(self):
        with self.assertRaises(SystemExit):
            MODULE.validate_semantic_review(
                {"kind": "kicad_placement_semantics", "candidate_sha256": "ABC", "success": False},
                "ABC",
                "pass",
            )

    def test_revise_can_bind_failed_semantics(self):
        self.assertFalse(MODULE.validate_semantic_review(
            {"kind": "kicad_placement_semantics", "candidate_sha256": "ABC", "success": False},
            "ABC",
            "revise",
        ))

    def test_local_review_uses_three_way_verdict_without_five_ratings(self):
        MODULE.validate_local_assessment({
            "schema": 1,
            "kind": "local_pcb_visual_assessment",
            "verdict": "awkward",
            "findings": ["The escape bends around an avoidable pad fence."],
        })


if __name__ == "__main__":
    unittest.main()
