from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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
dfm_candidate = load("dfm_candidate_template", "scripts/prepare_jlcpcb_dfm_candidate.py")
snapshot = load("snapshot_template", "scripts/create_release_snapshot.py")
houtini_job = load("houtini_job_template", "scripts/create_houtini_job.py")
easyeda_contract = load("easyeda_kirouting_contract_template", "scripts/easyeda_kirouting_contract.py")
easyeda_writeback = load("easyeda_writeback_template", "scripts/apply_easyeda_route_plan.py")
design_policy = load("design_policy_template", "scripts/design_policy.py")
fab_values = load("fab_values_template", "scripts/apply_reviewed_fab_values.py")
electrical_backend = load("electrical_backend_template", "scripts/electrical_backend.py")
physical_backend = load("physical_backend_template", "scripts/physical_backend.py")
easyeda_parity = load("easyeda_parity_template", "scripts/check_easyeda_import_parity.py")
electrical_freeze = load("electrical_freeze_template", "scripts/create_electrical_freeze.py")
tool_entrypoints = load("tool_entrypoints_template", "scripts/check_tool_entrypoints.py")
script_search = load("script_search_template", "scripts/find_existing_script.py")
backend_architecture = load("backend_architecture_template", "scripts/check_backend_architecture.py")
pcbctl = load("pcbctl_template", "scripts/pcbctl.py")
session_boot = load("session_boot_template", "scripts/session_boot.py")
stage_doctor = load("stage_doctor_template", "scripts/stage_doctor.py")
stage_handoff = load("stage_handoff_template", "scripts/create_stage_handoff.py")
toolchain_receipt = load("toolchain_receipt_template", "scripts/record_toolchain_receipt.py")
init_project = load("init_project_template", "scripts/init_project.py")


def parity_manifest(*, reference: str = "U1", pads: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema": 2,
        "normalization": {"length_unit": "nm", "rotation_unit": "microdegree"},
        "board_sha256": "A" * 64,
        "nets": ["GND", "VCC"],
        "board_outline": [{"kind": "segment", "start_nm": [0, 0], "end_nm": [1_000_000, 0]}],
        "holes": [{"id": "H1", "drill_nm": 3_000_000}],
        "components": [{
            "reference": reference,
            "footprint": "Package:One",
            "part_identity": "PART-1",
            "side": "F.Cu",
            "rotation_udeg": 0,
            "pads": pads or [{
                "number": "1", "net": "GND", "drill_nm": None,
                "pad_type": "smd", "geometry_signature": {"shape": "rect", "size_nm": [1_000, 2_000]},
            }],
        }],
    }


class FlowTemplateTests(unittest.TestCase):
    def test_agent_facing_surface_is_bounded(self) -> None:
        instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(instructions.split()), 650)
        self.assertNotIn("--no-clamp-netclasses", instructions)
        receipt = session_boot.build_receipt()
        self.assertEqual(receipt["context"]["default_read"], ["AGENTS.md", "build/WORKSET.json"])
        self.assertEqual(receipt["context"]["read_now"], ["build/WORKSET.json"])
        self.assertEqual(receipt["command_help"], "py -3.14 scripts/pcbctl.py --help")
        self.assertNotIn("core_commands", receipt)
        self.assertNotIn("release_requires", receipt)
        self.assertNotIn("tool_entrypoints", receipt)
        index = session_boot.stdout_index(receipt)
        self.assertEqual(index["read_now"], ["build/WORKSET.json"])
        self.assertNotIn("open_items", index)

    def test_handoff_refreshes_and_verifies_compact_workset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "build/WORKSET.json"
            original_root = stage_handoff.ROOT

            def compile_workset(*args, **kwargs):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({
                    "stage": "routing",
                    "status": "in_progress",
                    "objective": "Route the next block",
                }), encoding="utf-8")
                return subprocess.CompletedProcess(args[0], 0, stdout='{"stage":"routing"}')

            try:
                stage_handoff.ROOT = root
                with mock.patch.object(stage_handoff.subprocess, "run", side_effect=compile_workset):
                    receipt = stage_handoff.refresh_workset("routing", "Route the next block")
            finally:
                stage_handoff.ROOT = original_root
            self.assertEqual(receipt["path"], "build/WORKSET.json")
            self.assertEqual(receipt["stage"], "routing")

    def test_pcbctl_is_only_a_thin_dispatcher_to_existing_scripts(self) -> None:
        self.assertEqual(set(pcbctl.COMMANDS), {"status", "build", "freeze", "physical-start", "place", "route", "verify", "release"})
        for target in pcbctl.COMMANDS.values():
            self.assertTrue((ROOT / target[0]).is_file())
        routed = pcbctl.command_argv("route", ["check", "--input", "board.kicad_pcb"])
        self.assertEqual(routed[1:], ["scripts/krt_flow.py", "check", "--input", "board.kicad_pcb"])
        released = pcbctl.command_argv("release", ["--run-id", "test"])
        self.assertEqual(released[1:], ["scripts/run_pcb_pipeline.py", "--export", "--run-id", "test"])
        scored = pcbctl.command_argv("place", ["score", "--board", "candidate.kicad_pcb"])
        self.assertEqual(scored[1:], ["scripts/check_kicad_placement_routability.py", "--board", "candidate.kicad_pcb"])
        queried = pcbctl.command_argv("place", ["query", "--board", "candidate.kicad_pcb", "--ref", "U1"])
        self.assertEqual(queried[1:], ["scripts/query_pcb_geometry.py", "--board", "candidate.kicad_pcb", "--ref", "U1"])
        delta = pcbctl.command_argv("verify", ["delta", "--input", "base.kicad_pcb", "--candidate", "candidate.kicad_pcb"])
        self.assertEqual(delta[1:], ["scripts/krt_candidate_audit.py", "--input", "base.kicad_pcb", "--candidate", "candidate.kicad_pcb"])
        checkpoint = pcbctl.command_argv("verify", ["--tier", "checkpoint", "--board", "candidate.kicad_pcb", "--baseline-board", "base.kicad_pcb"])
        self.assertEqual(checkpoint[1:], ["scripts/run_pcb_pipeline.py", "--tier", "checkpoint", "--board", "candidate.kicad_pcb", "--baseline-board", "base.kicad_pcb"])
        escalated = pcbctl.command_argv("route", ["escalate", "--scope", "usb_input"])
        self.assertEqual(escalated[1:], ["scripts/assess_routing_escalation.py", "--scope", "usb_input"])
        started = pcbctl.command_argv("route", ["start", "--run-id", "route-1", "--profile", "outer_low_risk"])
        self.assertEqual(started[1:], ["scripts/krt_checkpoint.py", "start", "--run-id", "route-1", "--profile", "outer_low_risk"])
        with self.assertRaises(ValueError):
            pcbctl.command_argv("place", ["unknown-action"])
        with self.assertRaises(ValueError):
            pcbctl.command_argv("verify", ["unknown-action"])

    def test_default_flow_has_five_stages_and_three_validation_tiers(self) -> None:
        stages = json.loads((ROOT / "config/stage-checklist.json").read_text(encoding="utf-8"))["stages"]
        self.assertEqual(list(stages), ["requirements", "electrical", "physical", "routing", "release"])
        tiers = json.loads((ROOT / "config/validation-tiers.json").read_text(encoding="utf-8"))["tiers"]
        self.assertEqual(set(tiers), {"quick", "checkpoint", "release"})
        self.assertFalse(tiers["quick"]["full_pipeline"])
        self.assertTrue(tiers["release"]["full_pipeline"])

    def test_physical_stage_allows_isolated_krt_critical_probes(self) -> None:
        policy = json.loads((ROOT / "config/ai-tool-policy.json").read_text(encoding="utf-8"))
        self.assertIn("krt", policy["stages"]["physical"])
        self.assertIn("critical route probes", policy["krt"]["role"])
        self.assertIn("never modifies the active board", policy["krt"]["role"])

    def test_routing_to_physical_return_requires_eligible_escalation(self) -> None:
        with self.assertRaises(SystemExit):
            stage_handoff.validate_routing_return("routing", "physical", None)
        self.assertIsNone(stage_handoff.validate_routing_return("electrical", "physical", None))
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "escalation.json"
            receipt.write_text(json.dumps({
                "kind": "routing_escalation",
                "placement_review_eligible": True,
                "placement_change_authorized": False,
            }), encoding="utf-8")
            result = stage_handoff.validate_routing_return("routing", "physical", receipt)
            self.assertEqual(result["sha256"], hashlib.sha256(receipt.read_bytes()).hexdigest().upper())

    def test_stage_navigation_uses_the_configured_project_board(self) -> None:
        original_stage = stage_doctor.current_stage
        original_policy = stage_doctor.compile_policy
        try:
            stage_doctor.current_stage = lambda: {
                "stage": "routing", "status": "in_progress", "objective": "test",
                "open_items": [], "next_actions": [],
            }
            stage_doctor.compile_policy = lambda: {
                "capabilities": {"candidate_routing": True},
                "capability_issues": {"candidate_routing": []},
            }
            navigation = stage_doctor.receipt()
        finally:
            stage_doctor.current_stage = original_stage
            stage_doctor.compile_policy = original_policy
        flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
        self.assertIn(flow["project"]["board"], navigation["next_command"])
        self.assertNotIn("elec/layout/default/default.kicad_pcb", navigation["next_command"])

    def test_routing_navigation_continues_from_sha_bound_handoff_candidate(self) -> None:
        original_stage = stage_doctor.current_stage
        original_policy = stage_doctor.compile_policy
        try:
            with tempfile.TemporaryDirectory() as directory:
                candidate = Path(directory) / "accepted candidate.kicad_pcb"
                candidate.write_text("candidate", encoding="utf-8")
                stage_doctor.current_stage = lambda: {
                    "stage": "routing", "status": "in_progress", "objective": "test",
                    "open_items": [], "next_actions": [], "board": str(candidate),
                    "board_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest().upper(),
                }
                stage_doctor.compile_policy = lambda: {
                    "capabilities": {"candidate_routing": True},
                    "capability_issues": {"candidate_routing": []},
                }
                navigation = stage_doctor.receipt()
                self.assertIn(str(candidate), navigation["next_command"])
                self.assertNotIn("elec/layout/esp32-s3", navigation["next_command"])
        finally:
            stage_doctor.current_stage = original_stage
            stage_doctor.compile_policy = original_policy

    def test_navigation_never_uses_help_as_a_stage_action(self) -> None:
        original_stage = stage_doctor.current_stage
        original_policy = stage_doctor.compile_policy
        try:
            physical_board = ROOT / json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))["project"]["board"]
            physical_initialized = physical_board.is_file() and physical_board.with_suffix(".physical-origin.json").is_file()
            for stage_name, expected_action, expected_input, expect_command in ((
                "physical",
                "continue_kicad_placement" if physical_initialized else "create_kicad_physical_candidate",
                "bounded_placement_plan" if physical_initialized else None,
                not physical_initialized,
            ),):
                stage_doctor.current_stage = lambda name=stage_name: {
                    "stage": name, "status": "in_progress", "objective": "test",
                    "open_items": [], "next_actions": [],
                }
                stage_doctor.compile_policy = lambda: {
                    "capabilities": {"physical": True},
                    "capability_issues": {"physical": []},
                }
                navigation = stage_doctor.receipt()
                self.assertEqual(navigation["next_action"], expected_action)
                if expect_command:
                    self.assertEqual(navigation["next_command"], "py -3.14 scripts/pcbctl.py physical-start")
                else:
                    self.assertIsNone(navigation["next_command"])
                if expected_input:
                    self.assertIn(expected_input, navigation["required_inputs"])
        finally:
            stage_doctor.current_stage = original_stage
            stage_doctor.compile_policy = original_policy

    def test_physical_navigation_respects_explicit_critical_probe_work(self) -> None:
        action, command, inputs = stage_doctor.physical_navigation(
            True, ["bounded_critical_probe_plan"]
        )
        self.assertEqual(action, "continue_physical_critical_probes")
        self.assertIsNone(command)
        self.assertEqual(inputs, ["bounded_critical_probe_plan"])

    def test_physical_navigation_respects_explicit_corridor_review(self) -> None:
        action, command, inputs = stage_doctor.physical_navigation(
            True, ["bounded_3v3_corridor_strategy"]
        )
        self.assertEqual(action, "continue_physical_corridor_review")
        self.assertIsNone(command)
        self.assertEqual(inputs, ["bounded_3v3_corridor_strategy"])

    def test_electrical_navigation_does_not_repeat_build_while_auth_input_is_required(self) -> None:
        original_stage = stage_doctor.current_stage
        try:
            stage_doctor.current_stage = lambda: {
                "stage": "electrical", "status": "in_progress", "objective": "test",
                "open_items": ["authenticate"], "next_actions": [],
                "required_inputs": ["interactive_atopile_authentication"],
            }
            navigation = stage_doctor.receipt()
        finally:
            stage_doctor.current_stage = original_stage
        self.assertEqual(navigation["next_action"], "authenticate_atopile")
        self.assertIsNone(navigation["next_command"])
        self.assertEqual(navigation["required_inputs"], ["interactive_atopile_authentication"])

    def test_blocked_electrical_navigation_preserves_auth_required_input(self) -> None:
        original_stage = stage_doctor.current_stage
        try:
            stage_doctor.current_stage = lambda: {
                "stage": "electrical", "status": "blocked_external_dependency", "objective": "test",
                "open_items": ["signed out"], "next_actions": [],
                "required_inputs": ["interactive_atopile_authentication"],
            }
            navigation = stage_doctor.receipt()
        finally:
            stage_doctor.current_stage = original_stage
        self.assertFalse(navigation["success"])
        self.assertEqual(navigation["next_action"], "authenticate_atopile")
        self.assertEqual(navigation["required_inputs"], ["interactive_atopile_authentication"])
        self.assertIsNone(navigation["next_command"])

    def test_electrical_only_toolchain_refresh_preserves_full_tool_records(self) -> None:
        existing = {
            "schema": 1,
            "mode": "manual",
            "electrical_backend": {"active": "tscircuit"},
            "tscircuit": {"package_version": "old"},
            "kicad_routing_tools": {"commit": "KRT"},
            "freerouting": {"sha256": "FREE"},
            "houtini_lm": {"commit": "HOUTINI"},
            "konnect": {"binary_sha256": "KONNECT"},
        }
        refreshed = toolchain_receipt.merge_receipt(
            existing,
            {"electrical_backend": {"active": "atopile"}, "atopile": {"version": "0.15.7"}},
            electrical_only=True,
            recorded_at="2026-08-10T00:00:00+00:00",
        )
        self.assertEqual(refreshed["schema"], 2)
        self.assertEqual(refreshed["mode"], "manual")
        self.assertNotIn("tscircuit", refreshed)
        self.assertEqual(refreshed["atopile"]["version"], "0.15.7")
        for key in ("kicad_routing_tools", "freerouting", "houtini_lm", "konnect"):
            self.assertEqual(refreshed[key], existing[key])
            self.assertIn(key, refreshed["recording"]["preserved_unchecked_sections"])

    def test_review_and_release_defaults_follow_pcb_flow_board(self) -> None:
        flow = json.loads((ROOT / "config/pcb-flow.json").read_text(encoding="utf-8"))
        self.assertEqual(policy.configured_board(ROOT), (ROOT / flow["project"]["board"]).resolve())
        consumers = [
            "apply_kicad_rules.py", "audit_kicad_release.py", "create_release_snapshot.py",
            "export_jlcpcb_release.py", "extract_pcb_context.py", "record_physical_review.py",
        ]
        for name in consumers:
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("elec/layout/default/default.kicad_pcb", text, name)

    def test_atopile_is_the_default_production_electrical_backend(self) -> None:
        config = electrical_backend.read_config()
        name, backend = electrical_backend.selected_backend(config)
        self.assertEqual(name, "atopile")
        self.assertEqual(backend["platform"], "wsl-native")
        self.assertEqual(backend["status"], "primary")
        self.assertNotIn("circuit_json", config["contract"]["required_artifacts"])
        self.assertEqual(electrical_backend.validate_backend(config, name, backend)["entry"], "src/main.ato:App")

    def test_tscircuit_is_kept_as_a_separate_experimental_adapter(self) -> None:
        config = electrical_backend.read_config()
        name, backend = electrical_backend.selected_backend(config, "tscircuit")
        self.assertEqual(name, "tscircuit")
        self.assertEqual(backend["status"], "experimental")
        self.assertEqual(backend["driver"], "scripts/build_tscircuit_isolated.py")

    def test_physical_backend_roles_are_separate_from_routing_and_electrical(self) -> None:
        config = physical_backend.read_config()
        name, backend = physical_backend.selected_backend(config)
        self.assertEqual(name, "kicad")
        self.assertEqual(backend["role"], "primary")
        _, optional = physical_backend.selected_backend(config, "easyeda_pro")
        self.assertEqual(optional["role"], "optional_compatibility")
        architecture = json.loads((ROOT / "config/backend-architecture.json").read_text(encoding="utf-8"))
        self.assertEqual(architecture["routing_engine"]["active"], "kicad_routing_tools")
        self.assertTrue(architecture["routing_engine"]["independent_of_gui_backend"])
        self.assertTrue(backend_architecture.audit()["success"])

    def test_easyeda_parity_strictly_rejects_pad_net_changes(self) -> None:
        source = parity_manifest()
        imported = json.loads(json.dumps(source))
        imported["components"][0]["pads"][0]["net"] = "VCC"
        result = easyeda_parity.compare(source, imported)
        self.assertFalse(result["parity_pass"])
        self.assertTrue(result["strict_connectivity_failure"])
        self.assertIn("U1", result["failures"]["pad_to_net_mapping"])

    def test_easyeda_parity_accepts_identical_normalized_manifests(self) -> None:
        manifest = parity_manifest(reference="R1")
        self.assertTrue(easyeda_parity.compare(manifest, json.loads(json.dumps(manifest)))["parity_pass"])

    def test_easyeda_parity_covers_physical_identity_fields(self) -> None:
        source = parity_manifest(reference="J1")
        imported = json.loads(json.dumps(source))
        imported["board_outline"][0]["end_nm"] = [2_000_000, 0]
        imported["holes"][0]["drill_nm"] = 2_100_000
        imported["components"][0].update({"footprint": "Connector:B", "side": "B.Cu", "rotation_udeg": 180_000_000})
        imported["components"][0]["pads"][0]["drill_nm"] = 600_000
        failures = easyeda_parity.compare(source, imported)["failures"]
        self.assertTrue({"board_outline", "holes", "footprints", "side", "rotation", "pad_geometry"}.issubset(failures))

    def test_parity_supports_duplicate_logical_pad_numbers(self) -> None:
        pads = [
            {"number": "1", "net": "GND", "drill_nm": None, "pad_type": "smd", "geometry_signature": {"x_nm": 0}},
            {"number": "1", "net": "GND", "drill_nm": None, "pad_type": "smd", "geometry_signature": {"x_nm": 1_000}},
        ]
        source = parity_manifest(pads=pads)
        imported = json.loads(json.dumps(source))
        imported["components"][0]["pads"].reverse()
        self.assertTrue(easyeda_parity.compare(source, imported)["parity_pass"])

    def test_final_electrical_parity_ignores_physical_changes_but_rejects_connectivity_changes(self) -> None:
        frozen = parity_manifest()
        final = json.loads(json.dumps(frozen))
        final["components"][0]["side"] = "B.Cu"
        final["components"][0]["rotation_udeg"] = 90_000_000
        final["board_outline"][0]["end_nm"] = [2_000_000, 0]
        self.assertTrue(easyeda_parity.compare_electrical(frozen, final)["electrical_parity_pass"])
        final["components"][0]["pads"][0]["net"] = "VCC"
        self.assertFalse(easyeda_parity.compare_electrical(frozen, final)["electrical_parity_pass"])

    def test_manifest_rejects_floating_geometry(self) -> None:
        manifest = parity_manifest()
        manifest["holes"][0]["drill_nm"] = 3.0
        with self.assertRaises(SystemExit):
            easyeda_parity.validate_manifest(manifest)

    def test_freeze_promotion_moves_bundle_into_immutable_release_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "tmp" / "bundle"
            bundle.mkdir(parents=True)
            (bundle / "frozen.kicad_pcb").write_text("board", encoding="utf-8")
            release = root / "release" / "freeze-1"
            electrical_freeze.promote_bundle(bundle, release)
            self.assertFalse(bundle.exists())
            self.assertTrue((release / "frozen.kicad_pcb").is_file())
            with self.assertRaises(SystemExit):
                electrical_freeze.promote_bundle(root / "other", release)

    def test_manufacturing_gate_requires_final_parity_and_live_immutable_freeze_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "final.kicad_pcb"
            freeze_root = root / "release"
            bundle = freeze_root / "freeze-1"
            bundle.mkdir(parents=True)
            frozen_board = bundle / "frozen.kicad_pcb"
            frozen_manifest = bundle / "frozen-electrical-manifest.json"
            source_manifest = bundle / "source-manifest.json"
            board.write_text("final", encoding="utf-8")
            frozen_board.write_text("frozen", encoding="utf-8")
            frozen_manifest.write_text("{}", encoding="utf-8")
            source_manifest.write_text("{}", encoding="utf-8")
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest().upper()
            registry = json.loads((ROOT / "build/00-shared/evidence/design-policy/manufacturing-registry.json").read_text(encoding="utf-8"))
            freeze_path = bundle / "electrical-freeze.json"
            freeze = {
                "schema": 2, "kind": "electrical_freeze", "success": True, "freeze_pass": True,
                "freeze_id": "freeze-1",
                "frozen_board": str(frozen_board), "frozen_board_sha256": digest(frozen_board),
                "frozen_manifest": str(frozen_manifest), "frozen_manifest_sha256": digest(frozen_manifest),
                "source_manifest": str(source_manifest), "source_manifest_sha256": digest(source_manifest),
            }
            freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
            receipts = {
                "import.json": {
                    "schema": 1, "kind": "easyeda_import_parity", "success": True, "parity_pass": True,
                    "source_board_sha256": digest(frozen_board), "source_manifest_sha256": digest(frozen_manifest),
                },
                "final.json": {
                    "schema": 1, "kind": "final_electrical_parity", "success": True, "final_electrical_parity_pass": True,
                    "electrical_freeze_sha256": digest(freeze_path), "frozen_manifest_sha256": digest(frozen_manifest),
                    "final_board_sha256": digest(board),
                },
                "drc.json": {
                    "schema": 1, "kind": "physical_drc", "success": True, "drc_pass": True,
                    "backend": "easyeda_pro", "board_sha256": digest(board),
                },
                "dfm.json": {
                    "schema": 2, "kind": "manufacturing_dfm", "success": True, "dfm_pass": True,
                    "backend": "jlcpcb", "board_sha256": digest(board),
                    "policy_sha256": registry["policy_sha256"], "registry_sha256": registry["registry_sha256"],
                    "order_parameters": {"surface_finish": "ENIG", "solder_mask_color": "black"},
                    "analysis_modules": {
                        "pcb": {"executed": True, "pass": True},
                        "smt": {"executed": True, "pass": True},
                    },
                },
            }
            for name, value in receipts.items():
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            command = [
                sys.executable, "scripts/check_manufacturing_gates.py", "--board", str(board),
                "--electrical-freeze", str(freeze_path), "--import-parity", str(root / "import.json"),
                "--final-electrical-parity", str(root / "final.json"), "--easyeda-drc", str(root / "drc.json"),
                "--jlc-dfm", str(root / "dfm.json"), "--output", str(root / "gate.json"),
                "--freeze-root", str(freeze_root),
            ]
            self.assertEqual(subprocess.run(command, cwd=ROOT, check=False, capture_output=True).returncode, 0)
            outside = root / "electrical-freeze.json"
            outside.write_bytes(freeze_path.read_bytes())
            outside_command = list(command)
            outside_command[outside_command.index(str(freeze_path))] = str(outside)
            self.assertNotEqual(subprocess.run(outside_command, cwd=ROOT, check=False, capture_output=True).returncode, 0)
            frozen_board.unlink()
            self.assertNotEqual(subprocess.run(command, cwd=ROOT, check=False, capture_output=True).returncode, 0)

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

    def test_export_requires_sha_bound_manufacturing_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.kicad_pcb"
            board.write_text("board", encoding="utf-8")
            gate = root / "gate.json"
            gate.write_text(json.dumps({
                "kind": "manufacturing_gate",
                "manufacturing_gate_pass": True,
                "board_sha256": __import__("hashlib").sha256(board.read_bytes()).hexdigest().upper(),
            }), encoding="utf-8")
            self.assertTrue(export.require_passing_manufacturing_gate(board, gate)["manufacturing_gate_pass"])
            gate.write_text(json.dumps({"kind": "manufacturing_gate", "manufacturing_gate_pass": True, "board_sha256": "wrong"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                export.require_passing_manufacturing_gate(board, gate)

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

    def test_freerouting_extracts_declared_classes_without_full_dsn_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dsn = Path(directory) / "candidate.dsn"
            dsn.write_text('(network (class "Power Rails" GND VCC) (class signals RX TX))', encoding="utf-8")
            self.assertEqual(freerouting.dsn_net_classes(dsn), {"Power Rails", "signals"})

    def test_freerouting_command_is_always_headless(self) -> None:
        command = freerouting.build_freerouting_command(
            Path("freerouting.jar"), Path("input.dsn"), Path("output.ses"),
            heap_gb=4,
            policy={"optimizer_enabled": False, "max_passes": 10, "max_threads": 2},
            ignored_net_classes=["GND", "POWER"],
        )
        self.assertIn("--gui.enabled=false", command)
        self.assertEqual(command.count("--gui.enabled=false"), 1)
        self.assertEqual(command[command.index("-inc") + 1], "GND,POWER")
        self.assertNotIn("--gui.enabled=true", command)

    def test_reviewed_tool_entrypoints_have_no_direct_freerouting_bypass(self) -> None:
        result = tool_entrypoints.audit()
        self.assertTrue(result["success"], result["problems"])
        self.assertEqual(result["freerouting"]["entrypoint"], "scripts/run_freerouting_headless.py")

    def test_existing_script_search_finds_freerouting_wrapper(self) -> None:
        result = script_search.search(["freerouting"], limit=3)
        self.assertIn("scripts/run_freerouting_headless.py", [item["path"] for item in result["results"]])

    def test_artifact_store_has_eight_stages_and_three_lanes(self) -> None:
        artifacts = json.loads((ROOT / "config/build-artifacts.json").read_text(encoding="utf-8"))
        self.assertEqual(len(artifacts["stages"]), 8)
        self.assertEqual(artifacts["lanes"], {"tmp": "tmp", "evidence": "evidence", "release": "release"})
        self.assertEqual(artifacts["context_budget"]["max_context_slice_bytes"], 6000)

    def test_krt_artifact_paths_do_not_change_electrical_policy_identity(self) -> None:
        value = {
            "tool": {"commit": "abc"},
            "profiles": {"outer_low_risk": {"routing": {"layers": ["F.Cu", "B.Cu"]}}},
            "guardrails": {"forbidden_signal_layers": []},
            "artifacts": {"log_dir": "build/old"},
        }
        first = design_policy.object_sha256(design_policy.semantic_input("krt", value))
        value["artifacts"]["log_dir"] = "build/06-routing/tmp"
        second = design_policy.object_sha256(design_policy.semantic_input("krt", value))
        self.assertEqual(first, second)

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

    def test_atopile_direct_network_mode_removes_proxy_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://proxy", "ALL_PROXY": "socks://proxy", "WSLENV": "HTTP_PROXY/u:KEEP/u"},
            clear=False,
        ):
            env = atopile_build.wsl_process_environment("direct")
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("ALL_PROXY", env)
        self.assertEqual(env.get("WSLENV"), "KEEP/u")

    def test_electrical_build_rejects_reviewed_input_mutation(self) -> None:
        text = (ROOT / "scripts/build_electrical.py").read_text(encoding="utf-8")
        self.assertIn("reviewed_inputs_mutated_during_build", text)
        self.assertIn('post_status["source_sha256"] != status["source_sha256"]', text)

    def test_physical_environment_requires_easyeda_bridge(self) -> None:
        text = (ROOT / "scripts/check_pcb_environment.py").read_text(encoding="utf-8")
        self.assertIn('current_stage == "physical"', text)
        self.assertIn('"easyeda-bridge"', text)

    def test_easyeda_kirouting_mvp_rejects_existing_target_copper(self) -> None:
        config = {
            "routing_contract": {
                "ui_rule_fields_forbidden": ["track_width"],
                "mvp": {
                    "profile": "outer_low_risk", "maximum_requested_nets": 1,
                    "require_target_nets_without_existing_copper": True,
                },
            },
        }
        payload = {
            "board": {}, "components": [], "nets": ["GPIO10"],
            "existing_tracks": [{"net": "GPIO10"}], "existing_vias": [],
            "routing_config": {"nets_to_route": ["GPIO10"], "track_width": 99},
        }
        with self.assertRaisesRegex(ValueError, "no existing copper"):
            easyeda_contract.validate_mvp_request(payload, config)

    def test_easyeda_kirouting_mvp_ignores_ui_rules_and_fingerprints_board(self) -> None:
        config = {
            "routing_contract": {
                "ui_rule_fields_forbidden": ["track_width", "clearance"],
                "mvp": {
                    "profile": "outer_low_risk", "maximum_requested_nets": 1,
                    "require_target_nets_without_existing_copper": True,
                },
            },
        }
        payload = {
            "board": {"outline": []}, "components": [], "nets": ["GPIO10"],
            "existing_tracks": [], "existing_vias": [],
            "routing_config": {"nets_to_route": ["GPIO10"], "track_width": 99, "clearance": 99},
        }
        result = easyeda_contract.validate_mvp_request(payload, config)
        self.assertEqual(result["nets"], ["GPIO10"])
        self.assertEqual(result["ignored_ui_rule_fields"], ["clearance", "track_width"])
        self.assertEqual(len(result["board_fingerprint"]), 64)

    def test_easyeda_project_bridge_never_imports_official_routing_runner(self) -> None:
        text = (ROOT / "scripts/easyeda_kirouting_bridge.py").read_text(encoding="utf-8")
        self.assertNotIn("from routing_runner import", text)
        self.assertIn('str(KRT_FLOW), "route"', text)
        self.assertIn('"127.0.0.1"', text)

    def test_easyeda_writeback_requires_one_completed_mil_net(self) -> None:
        route = easyeda_writeback.normalize_plan({
            "status": "completed", "units_mm": False,
            "tracks": [{"net": "GPIO10"}], "vias": [],
        })
        self.assertEqual(route["net"], "GPIO10")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            easyeda_writeback.normalize_plan({
                "status": "completed", "units_mm": False,
                "tracks": [{"net": "GPIO10"}, {"net": "GPIO11"}], "vias": [],
            })

    def test_easyeda_writeback_geometry_verification_is_exact(self) -> None:
        route = {
            "net": "GPIO10",
            "tracks": [{"net": "GPIO10", "layer": 1, "startX": 10.2, "startY": 20.4, "endX": 30.2, "endY": 40.4, "width": 8.1}],
            "vias": [],
        }
        payload = {
            "existing_tracks": [{"net": "GPIO10", "layer": 1, "startX": 10, "startY": 20, "endX": 30, "endY": 40, "width": 8}],
            "existing_vias": [],
        }
        self.assertTrue(easyeda_writeback.verify_geometry(payload, route)["success"])
        payload["existing_tracks"][0]["endX"] = 31
        self.assertFalse(easyeda_writeback.verify_geometry(payload, route)["success"])

    def test_atopile_failure_classifies_easyeda_tls_timeout(self) -> None:
        failure = atopile_build.classify_failure(
            "EasyEDA API is unavailable: Failed to fetch footprint data for C12891: "
            "the handshake operation timed out"
        )
        self.assertEqual(failure["class"], "component_data_unreachable")

    def test_atopile_failure_classifies_required_sign_in(self) -> None:
        failure = atopile_build.classify_failure(
            "Part picking on atopile 0.15.8 requires sign-in. Run `ato auth login`."
        )
        self.assertEqual(failure["class"], "atopile_auth_required")

    def test_atopile_failure_classifies_footprint_resource_mismatch(self) -> None:
        failure = atopile_build.classify_failure(
            "Footprint `USB_C_Receptacle` doesn't exist in library `Reviewed_USB`"
        )
        self.assertEqual(failure["class"], "footprint_resource_mismatch")

    def test_atopile_picker_preflight_only_triggers_for_explicit_part_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "main.ato").write_text("module App:\n    pass\n", encoding="utf-8")
            self.assertFalse(atopile_build.source_requires_part_picker(source))
            (source / "parts.ato").write_text(
                'module SelectedPart:\n    lcsc_id = "C12891"\n', encoding="utf-8"
            )
            self.assertTrue(atopile_build.source_requires_part_picker(source))

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

    def test_dfm_candidate_binds_reviewed_parts_to_exact_board_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "parts.json"
            destination = root / "assembly-parts-bound.json"
            original = {"schema": 1, "board_sha256": None, "parts": [{"refs": ["R1"]}]}
            source.write_text(json.dumps(original), encoding="utf-8")
            bound = dfm_candidate.bind_assembly_parts_source(source, destination, "ABC123")
            self.assertEqual(bound["board_sha256"], "ABC123")
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
            recorded = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(recorded["derived_from_sha256"], dfm_candidate.sha256(source))

    def test_release_snapshot_name_uses_board_hash(self) -> None:
        self.assertEqual(snapshot.snapshot_name("ABCDEF0123456789"), "release-abcdef012345")

    def test_contract_and_architecture_checks_reuse_configured_contracts(self) -> None:
        contract_text = (ROOT / "scripts/validate_electrical_contract.py").read_text(encoding="utf-8")
        architecture_text = (ROOT / "scripts/check_backend_architecture.py").read_text(encoding="utf-8")
        self.assertIn("validate_manifest(manifest", contract_text)
        self.assertNotIn('!= "src/main.ato:App"', architecture_text)

    def test_completed_handoff_requires_machine_verified_evidence(self) -> None:
        issues = stage_doctor.handoff_integrity_issues({"stage": "routing", "status": "complete"})
        self.assertIn("handoff_completion_lacks_passing_evidence", issues)

    def test_fresh_project_initialization_resets_stale_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "handoff/stages/CURRENT_STAGE.json"
            stage.parent.mkdir(parents=True)
            stage.write_text(json.dumps({"stage": "release", "status": "complete"}), encoding="utf-8")
            original_root = init_project.ROOT
            try:
                init_project.ROOT = root
                init_project.ensure_stage_bootstrap("new-project")
            finally:
                init_project.ROOT = original_root
            value = json.loads(stage.read_text(encoding="utf-8"))
            self.assertEqual((value["project"], value["stage"], value["status"]), ("new-project", "requirements", "not_started"))
            self.assertFalse(value["completion_verified"])

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
                # This unit test exercises the receipt-size gate, independently
                # of whichever workflow stage the developer currently has open.
                with mock.patch.object(houtini_job, "require_tool", return_value={}):
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
                "manufacturing_rules": root / "manufacturing-rules.json",
                "krt": root / "krt.json",
                "acceptance": root / "acceptance.json",
            }
            files["requirements"].write_text(json.dumps({"schema": 2, "status": "reviewed", "unknowns": [], "critical_nets": [], "placement_regions": []}), encoding="utf-8")
            files["board_stack"].write_text(json.dumps({"active": "two", "profiles": {"two": {"copper_layers": ["F.Cu", "B.Cu"], "forbidden_signal_layers": []}}}), encoding="utf-8")
            files["manufacturing_profile"].write_text(json.dumps({"selected": True, "routing_rules": {"reviewed": True, "values": {"track_width": 0.2, "clearance": 0.2, "via_diameter": 0.6, "via_drill": 0.3}, "evidence": ["card"]}}), encoding="utf-8")
            files["manufacturing_rules"].write_text(json.dumps({"schema": 2, "gerber_file_patterns": {"F.Cu": "*-F_Cu.gtl", "B.Cu": "*-B_Cu.gbl"}, "gerber_rules": []}), encoding="utf-8")
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

    def test_krt_profile_may_explicitly_restrict_routing_to_layer_subset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack = root / "board-stack.json"
            stack.write_text(json.dumps({
                "active": "four",
                "profiles": {"four": {
                    "copper_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                    "forbidden_signal_layers": ["In1.Cu", "In2.Cu"],
                }},
            }), encoding="utf-8")
            config = {
                "board_stack": "board-stack.json",
                "guardrails": {"forbidden_signal_layers": ["In1.Cu", "In2.Cu"]},
            }
            profile = {
                "allow_layer_subset": True,
                "routing": {"layers": ["F.Cu"]},
            }
            krt.require_active_stack(config, root, profile)

    def test_krt_layer_subset_requires_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stack = root / "board-stack.json"
            stack.write_text(json.dumps({
                "active": "two",
                "profiles": {"two": {
                    "copper_layers": ["F.Cu", "B.Cu"],
                    "forbidden_signal_layers": [],
                }},
            }), encoding="utf-8")
            config = {"board_stack": "board-stack.json", "guardrails": {"forbidden_signal_layers": []}}
            with self.assertRaises(SystemExit):
                krt.require_active_stack(config, root, {"routing": {"layers": ["F.Cu"]}})

    def test_usb_device_escape_profile_explicitly_opts_into_fcu_subset(self) -> None:
        config = json.loads((ROOT / "config" / "kicad-routing-tools.json").read_text(encoding="utf-8"))
        profile = config["profiles"]["usb_device_escape_fcu"]
        self.assertIs(profile.get("allow_layer_subset"), True)
        self.assertEqual(profile["routing"]["layers"], ["F.Cu"])

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
                "visual_review_pass": True,
                "evidence": {"visual": ["visual-review.json"]},
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
