from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


turn_exit = load("check_turn_exit_template", "scripts/check_turn_exit.py")
POLICY = json.loads((ROOT / "config/execution-policy.json").read_text(encoding="utf-8"))


class ContinuousExecutionTests(unittest.TestCase):
    def test_in_progress_stage_cannot_be_reported_complete(self) -> None:
        result = turn_exit.evaluate(
            {"stage": "routing", "status": "in_progress", "next_actions": ["Try a different candidate."]},
            POLICY,
            "stage_complete",
        )
        self.assertFalse(result["final_allowed"])
        self.assertTrue(result["continue_required"])
        self.assertIn("remaining_next_actions", result["issues"])

    def test_complete_stage_requires_no_remaining_actions(self) -> None:
        result = turn_exit.evaluate(
            {"stage": "routing", "status": "complete", "next_actions": []},
            POLICY,
            "stage_complete",
        )
        self.assertTrue(result["final_allowed"])

    def test_blocker_requires_repeated_machine_readable_evidence(self) -> None:
        result = turn_exit.evaluate(
            {"stage": "routing", "status": "blocked", "next_actions": []},
            POLICY,
            "blocked",
        )
        self.assertFalse(result["final_allowed"])
        self.assertIn("missing_blocking_evidence", result["issues"])

        result = turn_exit.evaluate(
            {
                "stage": "routing",
                "status": "blocked",
                "next_actions": [],
                "blocking_evidence": {
                    "condition": "Required external manufacturer rule is unavailable.",
                    "observations": 3,
                    "safe_alternatives_exhausted": True,
                    "why_user_input_required": "The owner must provide or approve a source.",
                },
            },
            POLICY,
            "blocked",
        )
        self.assertTrue(result["final_allowed"])

    def test_user_decision_must_be_material_and_explained(self) -> None:
        stage = {"stage": "routing", "status": "in_progress", "next_actions": []}
        self.assertFalse(turn_exit.evaluate(stage, POLICY, "user_decision")["final_allowed"])
        self.assertTrue(
            turn_exit.evaluate(
                stage,
                POLICY,
                "user_decision",
                detail="Connector size changes the product envelope.",
                material_decision=True,
            )["final_allowed"]
        )


if __name__ == "__main__":
    unittest.main()
