from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


geometry = load("query_pcb_geometry_optimizations", "scripts/query_pcb_geometry.py")
drc_summary = load("summarize_kicad_drc_optimizations", "scripts/summarize_kicad_drc.py")
stage_doctor = load("stage_doctor_optimizations", "scripts/stage_doctor.py")
footprints = load("footprint_equivalence_optimizations", "scripts/check_kicad_footprint_equivalence.py")
pcbctl = load("pcbctl_optimizations", "scripts/pcbctl.py")
release_inputs = load("release_inputs_optimizations", "scripts/release_inputs.py")
release_readiness = load("release_readiness_optimizations", "scripts/check_release_readiness.py")
pipeline = load("pipeline_board_selection_optimizations", "scripts/run_pcb_pipeline.py")
krt_flow = load("krt_flow_stdout_optimizations", "scripts/krt_flow.py")
trace_plan = load("trace_plan_stdout_optimizations", "scripts/apply_kicad_trace_plan.py")
release_review = load("release_review_prepare_optimizations", "scripts/prepare_release_review.py")
process_confirmation = load("process_confirmation_optimizations", "scripts/confirm_special_process.py")


class RoutingWorkflowOptimizationTests(unittest.TestCase):
    def test_checkpoint_reuse_is_decided_before_artifact_run_creation(self) -> None:
        source = (ROOT / "scripts/run_pcb_pipeline.py").read_text(encoding="utf-8")
        main_source = source[source.index("def main()") :]
        reuse_branch = main_source.index("if args.reuse_checkpoint:")
        run_creation = main_source.index("run = create_run(")
        self.assertLess(reuse_branch, run_creation)

    def test_corridor_bbox_includes_width_and_clearance(self) -> None:
        self.assertEqual(
            geometry.corridor_bbox([1.0, 4.0, 3.0, 2.0], 0.2, 0.3),
            (0.6, 1.6, 3.4, 4.4),
        )

    def test_remaining_summary_groups_by_function_and_net(self) -> None:
        report = {
            "unconnected_items": [
                {"items": [
                    {"description": "F.Cu 上 C9 的焊盘 2 [GND]"},
                    {"description": "F.Cu 上 R17 的焊盘 2 [GND]"},
                ]},
                {"items": [
                    {"description": "F.Cu 上 SW1 的焊盘 3 [GND]"},
                    {"description": "过孔 [GND]"},
                ]},
            ]
        }
        result = drc_summary.remaining_summary(
            report, ["R17", "SW1", "C9"],
            {"C9": "target_control", "R17": "target_control", "SW1": "user_interface"},
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["by_net"], {"GND": 2})
        self.assertEqual({tuple(item["regions"]) for item in result["groups"]}, {("target_control",), ("user_interface",)})

    def test_pre_route_summary_exposes_release_debt(self) -> None:
        result = drc_summary.pre_route_summary({"violations": [
            {"type": "silk_overlap"}, {"type": "silk_overlap"},
            {"type": "lib_footprint_mismatch"},
        ]})
        self.assertFalse(result["ready_for_routing"])
        self.assertEqual(result["release_debt_count"], 3)
        self.assertEqual(result["buckets"]["presentation"], {"silk_overlap": 2})

    def test_owned_release_debt_does_not_block_broad_routing(self) -> None:
        result = drc_summary.pre_route_summary(
            {"violations": [{"type": "silk_overlap"}]},
            {"categories": {"silk_overlap": {"disposition": "fix_before_release"}}},
        )
        self.assertTrue(result["ready_for_routing"])
        self.assertTrue(result["release_cleanup_required"])
        self.assertEqual(result["unowned_categories"], [])

    def test_routing_navigation_stops_after_zero_unconnected_and_visual_pass(self) -> None:
        board_hash = "A" * 64
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audit_path = root / "audit.json"
            visual_path = root / "visual.json"
            candidate_path = root / "candidate.json"
            audit_path.write_text(json.dumps({
                "success": True, "baseline_pass": True, "candidate_sha256": board_hash,
                "candidate_counts": {"unconnected_items": 0},
            }), encoding="utf-8")
            visual_path.write_text(json.dumps({
                "kind": "pcb_visual_review", "success": True, "review_pass": True,
                "tier": "checkpoint", "board_sha256": board_hash,
            }), encoding="utf-8")
            candidate_path.write_text(json.dumps({
                "kind": "routing_candidate", "candidate_sha256": board_hash,
                "audit": str(audit_path),
            }), encoding="utf-8")
            stage = {"candidate_receipts": [str(candidate_path), str(visual_path)]}
            action, command, required = stage_doctor.routing_checkpoint_navigation(stage, "candidate.kicad_pcb", board_hash)
        self.assertEqual(action, "finalize_routing_checkpoint")
        self.assertIn("checkpoint-finalize", command)
        self.assertEqual(required, [])

    def test_fp_lib_table_resolution_handles_kiprjmod(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            table = root / "fp-lib-table"
            table.write_text('(fp_lib_table (lib (name "Demo")(type "KiCad")(uri "${KIPRJMOD}/parts/Demo")))', encoding="utf-8")
            self.assertEqual(footprints.libraries(table, root)["Demo"], (root / "parts/Demo").resolve())

    def test_release_navigation_refreshes_compact_readiness_first(self) -> None:
        action, command, required = stage_doctor.release_navigation(
            {"open_items": ["Resolve silk overlap"]}, "candidate.kicad_pcb",
        )
        self.assertEqual(action, "refresh_release_readiness")
        self.assertIn("verify readiness", command)
        self.assertEqual(required, [])

    def test_release_input_index_tracks_failed_evidence_without_passing_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            receipt = root / "dfm.json"
            index = root / "INPUTS.json"
            payload = {
                "schema": 2, "kind": "manufacturing_dfm", "success": False,
                "dfm_pass": False, "board_sha256": "A" * 64,
            }
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            value = release_inputs.register_release_input("jlc_dfm", receipt, payload, index_path=index)
            record = value["inputs"]["jlc_dfm"]
            self.assertFalse(record["success"])
            self.assertEqual(record["board_sha256"], "A" * 64)
            self.assertIsNotNone(release_inputs.valid_record("jlc_dfm", record))

    def test_release_readiness_dependency_manifest_invalidates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            board = root / "board.kicad_pcb"
            dependency_path = root / "policy.json"
            manifest = root / "current.dependencies.json"
            readiness_path = root / "current.json"
            board.write_text("board", encoding="utf-8")
            dependency_path.write_text("policy", encoding="utf-8")
            manifest.write_text(json.dumps({
                "dependencies": {str(dependency_path): hashlib.sha256(dependency_path.read_bytes()).hexdigest().upper()},
            }), encoding="utf-8")
            readiness_path.write_text(json.dumps({
                "kind": "release_readiness",
                "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
                "dependency_manifest": str(manifest),
                "dependency_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest().upper(),
            }), encoding="utf-8")
            self.assertIsNotNone(release_inputs.fresh_release_readiness(board, readiness_path))
            dependency_path.write_text("changed", encoding="utf-8")
            self.assertIsNone(release_inputs.fresh_release_readiness(board, readiness_path))

    def test_invalid_current_release_input_fails_closed_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            index = root / "INPUTS.json"
            index.write_text(json.dumps({
                "kind": "release_input_index",
                "inputs": {"jlc_dfm": {"path": str(root / "missing.json"), "sha256": "BAD"}},
            }), encoding="utf-8")
            path, payload, source = release_inputs.resolve_release_input(
                {"manufacturing": {"gate_receipts": {"jlc_dfm": "fallback.json"}}},
                "jlc_dfm", index_path=index,
            )
            self.assertEqual(path, (root / "missing.json").resolve())
            self.assertIsNone(payload)
            self.assertEqual(source, "invalid_index")

    def test_readiness_prioritizes_local_deterministic_debt(self) -> None:
        blockers = []
        release_readiness.add_blocker(
            blockers, "external_dfm", "manufacturing", "rerun_dfm", external=True,
        )
        release_readiness.add_blocker(
            blockers, "drc_debt", "drc", "review_drc",
        )
        blockers.sort(key=lambda item: {"drc": 0, "manufacturing": 1}[item["domain"]])
        action, command, required = release_readiness.first_action(blockers)
        self.assertEqual(action, "review_drc")
        self.assertIsNone(command)
        self.assertEqual(required, ["external_dfm"])

    def test_review_packet_extracts_only_library_mismatch_refs(self) -> None:
        report = """[lib_footprint_mismatch]:
    @(1,1): Footprint U1 differs
    @(2,2): Footprint J2 differs
[lib_footprint_mismatch]:
    @(3,3): Footprint U4 differs
[silk_overlap]:
    Footprint R3 overlaps
"""
        self.assertEqual(release_review.mismatch_refs(report), ["J2", "U1", "U4"])

    def test_special_process_confirmation_is_explicit_and_allowlisted(self) -> None:
        profile = {"special_processes": {"filled_vias": {
            "status": "confirmation_required",
            "allowed_confirmed_outcomes": ["epoxy_filled"],
            "selected_outcome": None,
            "evidence": None,
        }}}
        updated = process_confirmation.confirmed_profile(
            profile, "filled_vias", "epoxy_filled", "evidence/confirmation.json",
        )
        self.assertEqual(updated["special_processes"]["filled_vias"]["status"], "confirmed")
        self.assertIsNone(profile["special_processes"]["filled_vias"]["selected_outcome"])
        with self.assertRaises(ValueError):
            process_confirmation.confirmed_profile(profile, "filled_vias", "ink_plugged", "evidence.json")

    def test_release_review_packet_reuse_requires_fresh_tool_and_artifact_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            board = root / "board.kicad_pcb"
            artifact = root / "bundle.json"
            producer = root / "producer.py"
            packet_path = root / "packet.json"
            board.write_text("board", encoding="utf-8")
            artifact.write_text("bundle", encoding="utf-8")
            producer.write_text("tool", encoding="utf-8")
            packet = {
                "kind": "release_review_packet", "success": True,
                "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
                "artifacts": {"visual_bundle": str(artifact)},
                "artifact_sha256": {"visual_bundle": hashlib.sha256(artifact.read_bytes()).hexdigest().upper()},
                "producer_dependencies": {str(producer): hashlib.sha256(producer.read_bytes()).hexdigest().upper()},
            }
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            with mock.patch.object(release_review, "resolve_release_input", return_value=(packet_path, packet, "index")):
                self.assertIsNotNone(release_review.reusable_packet({}, board))
                producer.write_text("changed", encoding="utf-8")
                self.assertIsNone(release_review.reusable_packet({}, board))

    def test_release_defaults_to_hash_checked_adopted_routing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            board = root / "build/06-routing/release/run/routed.kicad_pcb"
            board.parent.mkdir(parents=True)
            board.write_text("board", encoding="utf-8")
            pointer = root / "build/06-routing/CURRENT.json"
            pointer.write_text(json.dumps({
                "artifact": "build/06-routing/release/run/routed.kicad_pcb",
                "artifact_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
            }), encoding="utf-8")
            with mock.patch.object(pipeline, "ROOT", root):
                self.assertEqual(pipeline.adopted_routing_board(), board.resolve())

    def test_pipeline_stdout_is_an_index_not_full_receipt(self) -> None:
        summary = {
            "success": True, "release_pass": False, "board_sha256": "A" * 64,
            "requested": {"tier": "checkpoint"}, "passed_steps": ["drc", "connectivity"],
            "run": {"run_id": "test"},
            "artifacts": {"pipeline_summary": "build/summary.json", "bulk": "x" * 10000},
            "context_metrics": {"duration_ms": 1234}, "failed_step": None,
        }
        result = pipeline.stdout_index(summary)
        self.assertEqual(result["passed_step_count"], 2)
        self.assertEqual(result["duration_ms"], 1234)
        self.assertNotIn("artifacts", result)

    def test_checkpoint_reuse_fails_closed_after_producer_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            board = root / "board.kicad_pcb"
            producer = root / "scripts/audit.py"
            receipt_path = root / "pipeline-summary.json"
            manifest_path = root / "checkpoint-dependencies.json"
            producer.parent.mkdir()
            board.write_text("board", encoding="utf-8")
            producer.write_text("producer", encoding="utf-8")
            manifest_path.write_text(json.dumps({
                "schema": 1, "kind": "pcb_pipeline_dependencies",
                "dependencies": {
                    "scripts/audit.py": hashlib.sha256(producer.read_bytes()).hexdigest().upper(),
                },
            }), encoding="utf-8")
            receipt_path.write_text(json.dumps({
                "schema": 2, "kind": "pcb_pipeline_summary", "success": True,
                "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
                "baseline_board_sha256": None,
                "requested": {"tier": "checkpoint"},
                "producer_dependency_manifest": "checkpoint-dependencies.json",
                "producer_dependency_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper(),
            }), encoding="utf-8")
            with mock.patch.object(pipeline, "ROOT", root):
                self.assertIsNotNone(pipeline.reusable_checkpoint(receipt_path, board, None))
                producer.write_text("changed", encoding="utf-8")
                self.assertIsNone(pipeline.reusable_checkpoint(receipt_path, board, None))

    def test_route_stdout_omits_bulk_geometry(self) -> None:
        value = krt_flow.route_stdout_index({
            "success": True, "route_ok": True, "outcome_class": "accepted",
            "router": {"routed_single": 2, "failed_single": {"count": 0}},
            "audit": {"baseline_pass": True, "new_violation_count": 0},
            "guardrails": {"input_preserved": True, "project_rules_preserved": True, "copper_scope_preserved": True},
            "quality_warnings": [], "routing_geometry": {"bulk": "x" * 10000},
        })
        self.assertTrue(value["guardrails_pass"])
        self.assertEqual(value["router"]["routed_single"], 2)
        self.assertNotIn("routing_geometry", value)

    def test_trace_stdout_omits_full_connectivity_receipt(self) -> None:
        value = trace_plan.trace_stdout_index({
            "success": True, "accepted": True, "nets": ["GPIO1"],
            "geometry_preflight": {"success": True, "bulk": "x" * 10000},
            "net_binding": {"success": True},
            "connectivity": {"success": True, "details": "x" * 10000},
            "audit": {"success": True, "unconnected_delta": 0, "new_violation_count": 0},
        }, ROOT / "build/receipt.json")
        self.assertTrue(value["connectivity_pass"])
        self.assertNotIn("connectivity", value)

    def test_new_commands_remain_under_existing_surface(self) -> None:
        self.assertEqual(
            pcbctl.command_argv("verify", ["remaining", "--board", "candidate.kicad_pcb"])[1:4],
            ["scripts/summarize_kicad_drc.py", "--mode", "remaining"],
        )
        self.assertEqual(
            pcbctl.command_argv("verify", ["readiness", "--board", "candidate.kicad_pcb"])[1],
            "scripts/check_release_readiness.py",
        )
        self.assertEqual(
            pcbctl.command_argv("verify", ["parity", "--board", "candidate.kicad_pcb"])[1],
            "scripts/refresh_final_electrical_parity.py",
        )
        self.assertEqual(
            pcbctl.command_argv("verify", ["prepare-review", "--board", "candidate.kicad_pcb"])[1],
            "scripts/prepare_release_review.py",
        )
        self.assertEqual(
            pcbctl.command_argv("verify", ["finalize-review", "--packet", "packet.json"])[1],
            "scripts/finalize_release_review.py",
        )
        self.assertEqual(
            pcbctl.command_argv("verify", ["workflow"])[1],
            "scripts/profile_workflow.py",
        )
        self.assertEqual(
            pcbctl.command_argv("verify", ["template"])[1],
            "scripts/run_template_checks.py",
        )
        self.assertEqual(
            pcbctl.command_argv("release", ["confirm-process", "--process", "filled_vias"])[1],
            "scripts/confirm_special_process.py",
        )
        self.assertEqual(
            pcbctl.command_argv("route", ["checkpoint-finalize", "--board", "candidate.kicad_pcb"])[1],
            "scripts/finalize_routing_checkpoint.py",
        )


if __name__ == "__main__":
    unittest.main()
