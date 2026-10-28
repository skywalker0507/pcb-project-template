from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


doctor = load("stage_doctor_counts", "scripts/stage_doctor.py")
boot = load("session_boot_counts", "scripts/session_boot.py")


class StageNavigationCountTests(unittest.TestCase):
    def project(self, root, status="draft", unknowns=None):
        (root / "config").mkdir()
        payloads = {
            "stage-checklist.json": {"stages": {"requirements": {"number": 1}}},
            "execution-policy.json": {"continuation": {"statuses": ["in_progress"]}},
            "pcb-flow.json": {"project": {}},
            "board-stack.json": {"active": "four_layer"},
            "requirements.json": {"schema": 2, "status": status,
                                  "interfaces": [{"name": "power"}], "rails": [], "connectors": [],
                                  "constraints": [], "critical_nets": [], "placement_regions": [],
                                  "mechanical": {}, "unknowns": [] if unknowns is None else unknowns},
        }
        for name, data in payloads.items():
            (root / "config" / name).write_text(json.dumps(data), encoding="utf-8")

    def navigation(self, root, stage):
        with mock.patch.object(doctor, "ROOT", root), \
             mock.patch.object(doctor, "current_stage", return_value=stage), \
             mock.patch.object(doctor, "handoff_integrity_issues", return_value=[]):
            return doctor.receipt()

    def test_draft_unknowns_block_review_and_survive_both_compaction_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids = [f"unresolved-{i}" for i in range(9)]
            self.project(root, unknowns=[{"id": name, "blocks": ["electrical_freeze"]} for name in ids])
            nav = self.navigation(root, {"stage": "requirements", "status": "in_progress", "open_items": ids})
            self.assertFalse(nav["success"])
            self.assertEqual(nav["blocker_count"], 2)
            self.assertEqual(nav["open_item_count"], 9)
            self.assertEqual(nav['next_action'], 'resolve_electrical_review')
            self.assertIsNone(nav['next_command'])
            with mock.patch.object(boot, "ROOT", root), mock.patch.object(boot, "stage_receipt", return_value=nav):
                receipt = boot.build_receipt()
            workset = boot.workset(receipt)
            self.assertEqual(workset["open_items"], ids[:4])
            self.assertEqual(workset["open_item_count"], 9)
            self.assertTrue(workset["open_items_truncated"])
            self.assertEqual(boot.stdout_index(receipt)["open_item_count"], 9)

    def test_basic_input_errors_keep_the_draft_validator_as_next_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            path = root / "config/requirements.json"
            data = json.loads(path.read_text()); data.pop("mechanical")
            path.write_text(json.dumps(data), encoding="utf-8")
            nav = self.navigation(root, {"stage": "requirements", "status": "in_progress"})
            self.assertFalse(nav["success"])
            self.assertFalse(nav["next_command"].endswith("--require-reviewed"))

    def test_bad_optional_check_config_does_not_crash_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            (root / 'config/project-checks.json').write_text('{bad json')
            nav = self.navigation(root, {'stage': 'requirements', 'status': 'in_progress'})
            self.assertEqual(nav['next_command'], 'py -3.14 scripts/pcbctl.py verify project --plan')
            self.assertFalse(nav['success'])

    def test_reviewed_inputs_with_nonblocking_notes_do_not_gain_new_blockers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root, status="reviewed", unknowns=[{"id": "future-note", "blocks": []}])
            nav = self.navigation(root, {"stage": "requirements", "status": "in_progress"})
            self.assertTrue(nav["success"])
            self.assertEqual(nav["blocker_count"], 0)
            self.assertEqual(nav['next_command'], 'py -3.14 scripts/pcbctl.py build --plan')

    def test_blocker_count_survives_stage_and_workset_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            blockers = [f"external-{i}" for i in range(17)]
            nav = self.navigation(root, {"stage": "requirements", "status": "blocked_external_dependency", "open_items": blockers})
            self.assertEqual(nav["blocker_count"], 17)
            self.assertEqual(len(nav["blockers"]), 8)
            with mock.patch.object(boot, "ROOT", root), mock.patch.object(boot, "stage_receipt", return_value=nav):
                receipt = boot.build_receipt()
            workset = boot.workset(receipt)
            self.assertEqual(workset["blocker_count"], 17)
            self.assertEqual(workset["blockers"], blockers[:4])
            self.assertTrue(workset["blockers_truncated"])
            self.assertEqual(boot.stdout_index(receipt)["blocker_count"], 17)
            self.assertLess(len(json.dumps(workset)), 1800)


if __name__ == "__main__":
    unittest.main()
