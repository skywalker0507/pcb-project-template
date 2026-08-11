from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
    def test_bounded_task_does_not_activate_stage_completion_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage.json"
            stage.write_text(json.dumps({
                "stage": "routing", "status": "in_progress", "next_actions": ["Route another net"],
            }), encoding="utf-8")
            base = [sys.executable, str(ROOT / "scripts/check_turn_exit.py"), "--stage-file", str(stage)]
            bounded = subprocess.run(base, capture_output=True, text=True, encoding="utf-8", check=False)
            bounded_receipt = json.loads(bounded.stdout)
            self.assertEqual(bounded.returncode, 0)
            self.assertFalse(bounded_receipt["gate_active"])
            self.assertFalse(bounded_receipt["continue_required"])

            continuous = subprocess.run(
                [*base, "--continuous-scope"], capture_output=True, text=True, encoding="utf-8", check=False,
            )
            continuous_receipt = json.loads(continuous.stdout)
            self.assertEqual(continuous.returncode, 3)
            self.assertTrue(continuous_receipt["gate_active"])
            self.assertTrue(continuous_receipt["continue_required"])

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
        flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
        board = ROOT / flow["project"]["board"]
        board_sha = hashlib.sha256(board.read_bytes()).hexdigest().upper()
        result = turn_exit.evaluate(
            {
                "stage": "routing", "status": "complete", "next_actions": [],
                "completion_verified": True, "completion_basis": "pipeline",
                "board": str(board), "board_sha256": board_sha,
                "pipeline": {"success": True, "board_sha256": board_sha},
            },
            POLICY,
            "stage_complete",
        )
        self.assertTrue(result["final_allowed"])

    def test_complete_stage_without_evidence_is_rejected(self) -> None:
        result = turn_exit.evaluate(
            {"stage": "routing", "status": "complete", "next_actions": []},
            POLICY,
            "stage_complete",
        )
        self.assertFalse(result["final_allowed"])
        self.assertIn("stage_completion_is_not_evidence_verified", result["issues"])

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
