from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


krt = load("krt_flow_template", "scripts/krt_flow.py")
export = load("export_template", "scripts/export_jlcpcb_release.py")
policy = load("pcb_policy_template", "scripts/pcb_policy.py")
freerouting = load("freerouting_template", "scripts/run_freerouting_headless.py")
requirements = load("requirements_template", "scripts/validate_requirements.py")
stage_policy = load("stage_policy_template", "scripts/stage_policy.py")
atopile_build = load("atopile_build_template", "scripts/build_atopile_isolated.py")
jlc_check = load("jlc_check_template", "scripts/check_jlcpcb_release.py")
snapshot = load("snapshot_template", "scripts/create_release_snapshot.py")
houtini_job = load("houtini_job_template", "scripts/create_houtini_job.py")
design_policy = load("design_policy_template", "scripts/design_policy.py")
fab_values = load("fab_values_template", "scripts/apply_reviewed_fab_values.py")


class FlowTemplateTests(unittest.TestCase):
    def test_stack_profiles_are_complete_and_distinct(self) -> None:
        stack = json.loads((ROOT / "config/board-stack.json").read_text(encoding="utf-8"))
        self.assertEqual(stack["profiles"]["two_layer"]["copper_layers"], ["F.Cu", "B.Cu"])
        self.assertIn("In1.Cu", stack["profiles"]["four_layer"]["copper_layers"])

    def test_export_refuses_stack_that_board_does_not_have(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.kicad_pcb"
            board.write_text('(kicad_pcb (layers (0 "F.Cu" signal) (31 "B.Cu" signal)))', encoding="utf-8")
            stack = root / "stack.json"
            stack.write_text(json.dumps({"active": "four", "profiles": {"four": {
                "copper_layers": ["F.Cu", "In1.Cu", "B.Cu"], "gerber_layers": ["F.Cu", "In1.Cu", "B.Cu"],
            }}}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                export.configured_layers(board, stack)

    def test_krt_suggestions_are_actual_non_power_nets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.kicad_pcb"
            board.write_text('(kicad_pcb (net 1 "GND") (net 2 "LED") (net 3 "USB_DP"))', encoding="utf-8")
            suggestions = krt.suggested_low_risk_nets(board, ["*GND*", "USB*"])
            self.assertEqual(suggestions, ["LED"])

    def test_export_requires_selected_manufacturing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text(json.dumps({"selected": False}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                export.require_manufacturing_profile(profile)

    def test_acceptance_loader_rejects_missing_hard_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            path.write_text(json.dumps({"release": {"requires": ["release_pass"]}}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                policy.load_acceptance(path, "release", {"release_pass", "board_sha256_match"})

    def test_selected_profile_still_needs_reviewed_routing_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile.json"
            profile.write_text(json.dumps({"selected": True, "manufacturer": "test", "source": "https://example.com"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                policy.reviewed_routing_rules(profile)

    def test_freerouting_defaults_to_shared_acceptance_policy(self) -> None:
        self.assertTrue(freerouting.DEFAULT_ACCEPTANCE.name == "pcb-acceptance.json")

    def test_requirements_reject_empty_record(self) -> None:
        self.assertIn("record at least one requirement or explicit unknown", requirements.problems({
            "interfaces": [], "rails": [], "connectors": [], "constraints": [], "unknowns": [], "mechanical": {},
        }))

    def test_stage_guard_rejects_export_from_electrical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage.json"
            stage.write_text(json.dumps({"stage": "electrical"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                stage_policy.require_operation("export", stage)

    def test_atopile_failure_classifies_component_data_outage(self) -> None:
        failure = atopile_build.classify_failure("Fetching component data failed: connection error: Name or service not known")
        self.assertEqual(failure["class"], "component_data_unreachable")

    def test_candidate_diff_is_triage_only(self) -> None:
        diff = krt.candidate_diff(
            {"segments": {"F.Cu": 1}, "vias": 0, "widths_mm": [0.2]},
            {"segments": {"F.Cu": 3, "B.Cu": 1}, "vias": 2, "widths_mm": [0.2, 0.3], "forbidden_segments": {}},
            {"new_violation_count": 0, "unconnected_delta": 0},
        )
        self.assertEqual(diff["via_delta"], 2)
        self.assertEqual(diff["segment_delta_by_layer"]["F.Cu"], 2)
        self.assertIn("does not prove", diff["limits"][0])

    def test_jlc_package_check_reports_missing_release_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack = root / "stack.json"
            stack.write_text(json.dumps({"active": "two", "profiles": {"two": {"copper_layers": ["F.Cu", "B.Cu"]}}}), encoding="utf-8")
            report = jlc_check.package_report(root / "missing", stack)
            self.assertFalse(report["success"])
            self.assertIn("gerber_files_present", report["failures"])

    def test_release_snapshot_name_uses_board_hash(self) -> None:
        self.assertEqual(snapshot.snapshot_name("ABCDEF0123456789"), "release-abcdef012345")

    def test_houtini_job_rejects_small_receipt_without_calling_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "receipt.log"
            receipt.write_text("short", encoding="utf-8")
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps({
                "schema": 1, "enabled": True, "max_paths": 1,
                "minimum_total_input_bytes": 100, "allowed_suffixes": [".log"],
                "forbidden_suffixes": [], "delegate": {},
            }), encoding="utf-8")
            original_root = houtini_job.ROOT
            try:
                houtini_job.ROOT = root
                result = houtini_job.create_job([receipt], "test", "test", policy_path, root / "out")
            finally:
                houtini_job.ROOT = original_root
            self.assertFalse(result["eligible"])
            self.assertEqual(result["reason"], "input_below_delegation_threshold")

    def test_policy_hash_changes_when_inputs_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "requirements": root / "requirements.json",
                "board_stack": root / "board-stack.json",
                "manufacturing_profile": root / "manufacturing.json",
                "krt": root / "krt.json",
                "acceptance": root / "acceptance.json",
            }
            files["requirements"].write_text(json.dumps({"schema": 2, "status": "reviewed", "unknowns": [], "critical_nets": [], "placement_regions": []}), encoding="utf-8")
            files["board_stack"].write_text(json.dumps({"active": "two", "profiles": {"two": {"copper_layers": ["F.Cu", "B.Cu"], "forbidden_signal_layers": []}}}), encoding="utf-8")
            files["manufacturing_profile"].write_text(json.dumps({"selected": True, "routing_rules": {"reviewed": True, "values": {"track_width": 0.2, "clearance": 0.2, "via_diameter": 0.6, "via_drill": 0.3}, "evidence": ["card"]}}), encoding="utf-8")
            files["krt"].write_text(json.dumps({"profiles": {"outer_low_risk": {"routing": {"layers": ["F.Cu", "B.Cu"]}}}}), encoding="utf-8")
            files["acceptance"].write_text("{}", encoding="utf-8")
            original = design_policy.INPUTS
            try:
                design_policy.INPUTS = files
                first = design_policy.compile_policy()
                first_hash = design_policy.policy_sha256(first)
                files["requirements"].write_text(json.dumps({"schema": 2, "status": "reviewed", "unknowns": [], "critical_nets": [{"net": "USB_DP"}], "placement_regions": []}), encoding="utf-8")
                second = design_policy.compile_policy()
            finally:
                design_policy.INPUTS = original
            self.assertNotEqual(first_hash, design_policy.policy_sha256(second))
            self.assertTrue(second["capabilities"]["release"])

    def test_physical_review_must_match_board_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.kicad_pcb"
            board.write_text("board", encoding="utf-8")
            review = root / "review.json"
            review.write_text(json.dumps({
                "review_pass": True,
                "board_sha256": __import__("hashlib").sha256(board.read_bytes()).hexdigest().upper(),
                "policy_sha256": "policy",
            }), encoding="utf-8")
            self.assertTrue(export.require_passing_physical_review(board, review, {"policy_sha256": "policy"})["review_pass"])
            with self.assertRaises(SystemExit):
                export.require_passing_physical_review(board, review, {"policy_sha256": "changed"})

    def test_fabrication_values_use_krt_via_size_name(self) -> None:
        self.assertEqual(fab_values.parse_value("via_size=0.6"), ("via_size", 0.6))
        with self.assertRaises(Exception):
            fab_values.parse_value("via_diameter=0.6")


if __name__ == "__main__":
    unittest.main()
