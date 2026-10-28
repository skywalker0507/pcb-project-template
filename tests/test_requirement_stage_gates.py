from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import design_policy
import validate_requirements as validator
import create_electrical_freeze as freeze
import prepare_kicad_physical_candidate as physical
import check_release_readiness as readiness
import create_release_snapshot as snapshot
import release_inputs
import test_reviewed_requirements_gate as contract_tests
import test_stage_navigation_counts as navigation_tests

reviewed = contract_tests.reviewed


def debt(name, *gates):
    return {"id": name, "description": "Pending " + name, "blocks": list(gates)}


def prototype(unknowns):
    return {**reviewed(unknowns), "release_target": "prototype", "prototype_validation": {
        "purpose": "Controlled first-board validation",
        "restrictions": "Bench use only; hardware acceptance has not passed.",
        "tests": [{"unknown_id": item["id"], "procedure": "Measure and record " + item["id"]}
                  for item in unknowns if "production_release" in item["blocks"]],
    }}


class RequirementStageGateTests(unittest.TestCase):
    def policy(self, requirements, callback=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = {
                "requirements": requirements,
                "board_stack": {"active": "two", "profiles": {"two": {"copper_layers": ["F.Cu", "B.Cu"], "forbidden_signal_layers": []}}},
                "manufacturing_profile": {"selected": True, "routing_rules": {"reviewed": True, "values": {
                    "track_width": 0.2, "clearance": 0.2, "via_size": 0.6, "via_drill": 0.3}}},
                "manufacturing_rules": {"schema": 2, "gerber_rules": []},
                "krt": {"profiles": {"outer_low_risk": {"routing": {"layers": ["F.Cu", "B.Cu"]}}}},
                "acceptance": {},
            }
            inputs = {}
            for name, value in values.items():
                path = root / (name + ".json")
                path.write_text(json.dumps(value), encoding="utf-8")
                inputs[name] = path
            with mock.patch.object(design_policy, "ROOT", root), mock.patch.object(design_policy, "INPUTS", inputs):
                result = design_policy.compile_policy()
                return callback(root, result, inputs) if callback else result

    def test_later_evidence_does_not_block_freeze_but_remains_registered(self):
        requirements = reviewed([debt("stack", "physical_start"), debt("assembly", "release"), debt("waveform", "production_release")])
        self.assertEqual(validator.problems(requirements, True, gate="electrical_freeze"), [])
        self.assertTrue(validator.problems(requirements, True, gate="physical_start"))
        self.assertEqual(len(requirements["unknowns"]), 3)

    def test_early_debt_is_cumulative_at_all_later_gates(self):
        requirements = reviewed([debt("pin-review", "electrical_freeze")])
        for gate in ("electrical_freeze", "physical_start", "release", "production_release"):
            with self.subTest(gate=gate):
                self.assertTrue(validator.problems(requirements, True, gate=gate))

    def test_legacy_strings_block_and_malformed_scopes_fail_closed(self):
        for item in ["unscoped legacy obligation", {"id": "missing"}, debt("typo", "prodution_release"),
                     {"id": "false", "blocks": False}, {"id": "null", "blocks": None},
                     {"id": "scalar", "blocks": "release"}, 12]:
            with self.subTest(item=item):
                self.assertTrue(validator.problems(reviewed([item]), True, gate="electrical_freeze"))

    def test_unknown_gate_is_rejected(self):
        with self.assertRaises(ValueError):
            validator.problems(reviewed(), True, gate="electrcal_freeze")

    def test_default_production_release_cannot_ignore_hardware_debt(self):
        result = self.policy(reviewed([debt("waveform", "production_release")]))
        self.assertTrue(result["capabilities"]["physical"])
        self.assertTrue(result["capabilities"]["candidate_routing"])
        self.assertFalse(result["capabilities"]["release"])

    def test_prototype_requires_explicit_complete_test_plan(self):
        good = prototype([debt("waveform", "production_release")])
        cases = []
        for field in ("purpose", "restrictions", "tests"):
            bad = copy.deepcopy(good); bad["prototype_validation"].pop(field)
            cases.append(bad)
        bad = copy.deepcopy(good); bad["prototype_validation"]["tests"] = []
        cases.append(bad)
        bad = copy.deepcopy(good); bad["release_target"] = "prototpye"
        cases.append(bad)
        for tests in ([{"unknown_id": "waveform", "procedure": ""}],
                      [{"unknown_id": "other", "procedure": "Measure"}],
                      good["prototype_validation"]["tests"] * 2):
            bad = copy.deepcopy(good); bad["prototype_validation"]["tests"] = tests
            cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad):
                self.assertTrue(validator.problems(bad, True))
                self.assertFalse(self.policy(bad)["capabilities"]["release"])

    def test_prototype_can_reach_manufacturing_without_claiming_production_acceptance(self):
        requirements = prototype([debt("waveform", "production_release")])
        result = self.policy(requirements)
        self.assertTrue(result["capabilities"]["release"])
        self.assertFalse(result["capabilities"]["production_release"])
        self.assertEqual(result["requirements"]["release_target"], "prototype")
        self.assertEqual(result["requirements"]["blocking_unknowns_by_gate"]["production_release"], ["waveform"])
        self.assertEqual(result["requirements"]["open_unknowns"], requirements["unknowns"])

    def test_prototype_still_requires_source_physical_and_manufacturing_review(self):
        for gate in ("electrical_freeze", "physical_start", "release"):
            with self.subTest(gate=gate):
                result = self.policy(prototype([debt("unfinished", gate)]))
                self.assertFalse(result["capabilities"]["release"])

    def test_target_or_test_procedure_change_invalidates_policy_hash(self):
        first = prototype([debt("waveform", "production_release")])
        changed = copy.deepcopy(first); changed["prototype_validation"]["tests"][0]["procedure"] += " at cold start"
        production = copy.deepcopy(first); production["release_target"] = "production"
        hashes = [design_policy.policy_sha256(self.policy(value)) for value in (first, changed, production)]
        self.assertEqual(len(set(hashes)), 3)

    def test_policy_rejects_missing_requirement_structure_at_physical_and_release(self):
        value = reviewed(); value.pop("mechanical")
        result = self.policy(value)
        self.assertFalse(result["capabilities"]["physical"])
        self.assertFalse(result["capabilities"]["release"])

    def test_prototype_does_not_skip_manufacturing_rules(self):
        result = self.policy(prototype([debt("waveform", "production_release")]))
        # Recompile using the actual default unselected manufacturing inputs.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "requirements.json"
            path.write_text(json.dumps(prototype([debt("waveform", "production_release")])), encoding="utf-8")
            inputs = {**design_policy.INPUTS, "requirements": path}
            unselected = root / "manufacturing.json"
            unselected.write_text(json.dumps({"selected": False}), encoding="utf-8")
            inputs["manufacturing_profile"] = unselected
            with mock.patch.object(design_policy, "INPUTS", inputs):
                result = design_policy.compile_policy()
            self.assertFalse(result["capabilities"]["release"])
            self.assertIn("manufacturing_profile_not_selected", result["capability_issues"]["release"])

    def test_export_scope_carries_unfinished_debt_and_is_never_production_ready_for_prototype(self):
        requirements = prototype([debt("waveform", "production_release")])
        policy = self.policy(requirements)
        policy["policy_sha256"] = design_policy.policy_sha256(policy)
        scope = design_policy.release_scope(policy, "A" * 64)
        self.assertFalse(scope["production_ready"])
        self.assertEqual(scope["open_unknowns"], requirements["unknowns"])
        self.assertEqual(scope["prototype_validation"], requirements["prototype_validation"])

    def test_existing_passing_release_policy_is_stale_after_target_or_debt_change(self):
        policy = self.policy(prototype([debt("waveform", "production_release")]))
        policy["policy_sha256"] = design_policy.policy_sha256(policy)
        changed = self.policy(reviewed([debt("waveform", "production_release")]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old-pass.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with mock.patch.object(design_policy, "compile_policy", return_value=changed):
                with self.assertRaisesRegex(SystemExit, "stale"):
                    design_policy.require_policy("release", path)

    def test_real_readiness_receipt_becomes_stale_when_requirements_target_changes(self):
        def check(root, policy, inputs):
            board = root / "board.kicad_pcb"; board.write_bytes(b"opaque readiness fixture")
            (root / "config").mkdir(); (root / "config/pcb-flow.json").write_text('{}', encoding="utf-8")
            with mock.patch.object(readiness, "ROOT", root), mock.patch.object(release_inputs, "ROOT", root), \
                 mock.patch.object(readiness, "compile_policy", return_value=policy), \
                 mock.patch.object(readiness, "DESIGN_POLICY_OUTPUT", root / "not-compiled.json"), \
                 mock.patch.object(readiness, "MANUFACTURING_REGISTRY_ARTIFACT", root / "no-registry.json"), \
                 mock.patch.object(readiness, "resolve_release_input", return_value=(None, None, None)), \
                 mock.patch.object(readiness, "run_drc_summary", return_value={"pre_route": {}, "unconnected": {"count": 0}}):
                result = readiness.build_receipt(board)
                path = root / "readiness.json"; path.write_text(json.dumps(result), encoding="utf-8")
                self.assertIsNotNone(release_inputs.fresh_release_readiness(board, path))
                changed = json.loads(inputs["requirements"].read_text()); changed["release_target"] = "production"
                inputs["requirements"].write_text(json.dumps(changed), encoding="utf-8")
                self.assertIsNone(release_inputs.fresh_release_readiness(board, path))
        self.policy(prototype([debt("waveform", "production_release")]), check)

    def test_snapshot_rejects_relabelled_prototype_scope_before_creating_output(self):
        policy = self.policy(prototype([debt("waveform", "production_release")]))
        policy["policy_sha256"] = design_policy.policy_sha256(policy)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); board = root / "board.kicad_pcb"; board.write_bytes(b"opaque snapshot fixture")
            package = root / "package"; package.mkdir()
            digest = snapshot.sha256(board)
            (root / "check.json").write_text(json.dumps({"success": True, "package": str(package.resolve())}), encoding="utf-8")
            (root / "audit.json").write_text(json.dumps({"release_pass": True, "board_sha256": digest}), encoding="utf-8")
            scope = design_policy.release_scope(policy, digest); scope["production_ready"] = True
            (package / "release-scope.json").write_text(json.dumps(scope), encoding="utf-8")
            argv = ["snapshot", "--board", str(board), "--package", str(package), "--package-check", str(root / "check.json"),
                    "--audit-json", str(root / "audit.json"), "--registry-pre-export", str(root / "pending.json"),
                    "--gerber-rules", str(root / "pending.json"), "--output-root", str(root / "releases")]
            with mock.patch.object(snapshot, "require_operation"), mock.patch.object(snapshot, "require_policy", return_value=policy), \
                 mock.patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(SystemExit, "Export release scope does not match"):
                    snapshot.main()
            self.assertFalse((root / "releases").exists())

    def test_contract_and_freeze_share_stage_scope(self):
        requirements = reviewed([debt("assembly", "release")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, result = contract_tests.ReviewedRequirementsGateTests().run_contract(root, requirements)
            self.assertEqual(code, 0, result)
            freeze.require_reviewed_requirements(root / "requirements.json")

    def test_navigation_does_not_block_freeze_on_release_only_debt(self):
        helper = navigation_tests.StageNavigationCountTests()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper.project(root, status="reviewed", unknowns=[debt("assembly", "release")])
            result = helper.navigation(root, {"stage": "requirements", "status": "in_progress"})
            self.assertTrue(result["success"], result)

    def test_physical_entrypoint_rejects_current_debt_before_old_freeze_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "config").mkdir()
            (root / "config/requirements.json").write_text(json.dumps(reviewed([debt("stack", "physical_start")])), encoding="utf-8")
            argv = ["physical-start", "--electrical-freeze", str(root / "old-pass.json")]
            with mock.patch.object(physical, "ROOT", root), mock.patch.object(physical, "current_stage", return_value={"stage": "physical"}), \
                 mock.patch.object(physical, "selected_backend", return_value=("kicad", {"role": "primary"})), \
                 mock.patch.object(physical, "read_config", return_value={}), mock.patch.object(physical, "load_release_freeze") as load, \
                 mock.patch.object(physical, "create_run") as create, mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "Requirements are not ready for physical start"):
                    physical.main()
                load.assert_not_called(); create.assert_not_called()
            self.assertFalse((root / "elec").exists())


if __name__ == "__main__":
    unittest.main()
