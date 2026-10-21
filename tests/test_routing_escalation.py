from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("routing_escalation", ROOT / "scripts/assess_routing_escalation.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RoutingEscalationTests(unittest.TestCase):
    def test_two_independent_no_path_failures_only_enable_placement_review(self):
        rows = [
            {"structural": True, "outcome_class": "structural_no_path_or_incomplete", "strategy_signature": "A", "new_violation_types": []},
            {"structural": True, "outcome_class": "structural_no_path_or_incomplete", "strategy_signature": "B", "new_violation_types": []},
        ]
        result = MODULE.assess(rows)
        self.assertTrue(result["corridor_review_required"])
        self.assertTrue(result["placement_review_eligible"])
        self.assertFalse(result["placement_change_authorized"])

    def test_drc_and_tool_failures_do_not_send_work_to_placement(self):
        rows = [
            {"structural": False, "outcome_class": "candidate_drc_regression", "strategy_signature": "A", "new_violation_types": ["clearance"]},
            {"structural": False, "outcome_class": "tool_failure", "strategy_signature": "B", "new_violation_types": []},
        ]
        result = MODULE.assess(rows)
        self.assertFalse(result["routing_review_required"])
        self.assertFalse(result["placement_review_eligible"])

    def test_repeated_structural_drc_across_strategies_enables_review_not_move(self):
        rows = [
            {"structural": True, "outcome_class": "candidate_drc_regression", "strategy_signature": "A", "new_violation_types": ["board_edge_clearance"]},
            {"structural": True, "outcome_class": "candidate_drc_regression", "strategy_signature": "B", "new_violation_types": ["board_edge_clearance"]},
        ]
        result = MODULE.assess(rows)
        self.assertTrue(result["placement_review_eligible"])
        self.assertFalse(result["placement_change_authorized"])


if __name__ == "__main__":
    unittest.main()
