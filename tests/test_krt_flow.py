from __future__ import annotations

import importlib.util
import json
import ntpath
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


flow = load("krt_flow", ROOT / "scripts/krt_flow.py")
audit = load("krt_candidate_audit", ROOT / "scripts/krt_candidate_audit.py")


class KrtFlowTests(unittest.TestCase):
    def test_candidate_delta_has_a_default_kicad_cli(self):
        self.assertEqual(ntpath.basename(str(audit.DEFAULT_KICAD_CLI)), "kicad-cli.exe" if os.name == "nt" else "kicad-cli")

    def test_bounded_preview_has_hard_limit(self):
        result = flow.bounded(list(range(20)), 3)
        self.assertEqual(result, {"count": 20, "preview": [0, 1, 2], "truncated": True})

    def test_routing_geometry_excursion_is_compact_and_soft(self):
        value = {
            "success": True,
            "nets": [{"net": "A"}, {"net": "B"}],
            "change": {
                "reference_bbox_excursion": {"outside": True, "max_mm": 5.3},
            },
            "quality_warnings": [{
                "type": "route_bbox_excursion", "severity": "soft", "max_mm": 5.3,
            }],
            "limits": "soft only",
        }
        result = flow.concise_routing_geometry(value, 1)
        self.assertTrue(result["success"])
        self.assertEqual(result["nets"]["count"], 2)
        self.assertEqual(result["quality_warnings"]["preview"][0]["severity"], "soft")
        self.assertEqual(result["change"]["reference_bbox_excursion"]["max_mm"], 5.3)

    def test_missing_routing_geometry_is_soft_and_never_reclassifies_route(self):
        warnings = flow.routing_quality_warnings({
            "success": False, "error": "routing_geometry_summary_failed",
        })
        self.assertEqual(warnings[0]["type"], "routing_geometry_unavailable")
        self.assertEqual(warnings[0]["severity"], "soft")

    def test_expand_nets_applies_include_and_exclude(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.kicad_pcb"
            board.write_text(
                '(kicad_pcb (net "gpio1") (net "gpio2") (net "usb_dp"))',
                encoding="utf-8",
            )
            self.assertEqual(flow.expand_nets(board, ["*"], ["usb*"]), ["gpio1", "gpio2"])

    def test_board_metrics_handles_quoted_layer_name(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.kicad_pcb"
            board.write_text(
                '(kicad_pcb\n'
                '  (segment (start 0 0) (end 1 1) (width 0.2) (layer "Signal Inner") (net 1))\n'
                '  (via (at 1 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))\n'
                ')',
                encoding="utf-8",
            )
            metrics = flow.board_metrics(board, ["Signal Inner"])
            self.assertEqual(metrics["forbidden_segments"], {"Signal Inner": 1})
            self.assertEqual(metrics["vias"], 1)

    def test_reconciliation_success_removes_initial_failure(self):
        output = "\n".join([
            'JSON_SUMMARY: {"routed_single":["a"],"failed_single":["b"],"failed_multipoint":[],"multipoint_pads_total":10}',
            'JSON_SUMMARY: {"routed_single":["b"],"failed_single":[],"failed_multipoint":[],"multipoint_pads_total":0}',
        ])
        result = flow.parse_router_summaries(output)
        self.assertEqual(result["failed_single"], [])
        self.assertEqual(result["routed_single"], ["a", "b"])
        self.assertEqual(result["summary_record_count"], 2)

    def test_drc_fingerprint_detects_same_count_replacement(self):
        old = {"type": "clearance", "severity": "error", "items": [{"uuid": "old"}]}
        new = {"type": "clearance", "severity": "error", "items": [{"uuid": "new"}]}
        self.assertNotEqual(audit.violation_identity(old), audit.violation_identity(new))

    def test_drc_fingerprint_treats_stable_uuid_move_as_inherited(self):
        before = {
            "type": "silk_overlap", "severity": "warning", "description": "Silkscreen clearance",
            "items": [{"uuid": "stable", "description": "U3 line on F.Silkscreen", "pos": {"x": 32, "y": 52}}],
        }
        after = {
            "type": "silk_overlap", "severity": "warning", "description": "Silkscreen clearance",
            "items": [{"uuid": "stable", "description": "U3 line on F.Silkscreen", "pos": {"x": 17, "y": 59}}],
        }
        self.assertEqual(audit.violation_identity(before), audit.violation_identity(after))

    def test_drc_fingerprint_tolerates_kicad_overlapping_track_selection(self):
        common = {
            "type": "clearance",
            "severity": "error",
            "description": "Clearance violation ( clearance 0.2000 mm; actual 0.1250 mm)",
        }
        old = {**common, "items": [
            {"uuid": "short", "description": "Track [USB_5V] on B.Cu, length 2.0000 mm", "pos": {"x": 115.1, "y": 108.0}},
            {"uuid": "via", "description": "Via [V3V3] on F.Cu - B.Cu", "pos": {"x": 115.65, "y": 108.95}},
        ]}
        new = {**common, "items": [
            {"uuid": "long", "description": "Track [USB_5V] on B.Cu, length 2.9500 mm", "pos": {"x": 115.1, "y": 107.05}},
            {"uuid": "via", "description": "Via [V3V3] on F.Cu - B.Cu", "pos": {"x": 115.65, "y": 108.95}},
        ]}
        self.assertEqual(audit.violation_identity(old), audit.violation_identity(new))

    def test_protected_flags_block_equals_and_case_variants(self):
        conflicts = flow.protected_extra_conflicts([
            "--layers=F.Cu", "--Impedance=50", "--bus",
        ])
        self.assertEqual(conflicts, ["--impedance", "--layers"])

    def test_latest_krt_preserves_netclasses_without_clearance_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "board.kicad_pcb"
            board.write_text("(kicad_pcb)", encoding="utf-8")
            board.with_suffix(".kicad_pro").write_text(json.dumps({
                "net_settings": {
                    "classes": [{"name": "Default", "clearance": 0.2}],
                },
            }), encoding="utf-8")
            profile = {"routing": {
                "layers": ["F.Cu", "B.Cu"],
                "layer_costs": [1, 3],
                "track_width": 0.2,
                "clearance": 0.2,
                "via_size": 0.6,
                "via_drill": 0.3,
                "fab_overrides": None,
                "extra_args": [],
            }}
            state = {
                "ok": True,
                "python": "python",
                "router": "route.py",
                "netclass_mode": "native_netclasses",
            }
            with mock.patch.object(flow, "tool_state", return_value=state):
                command = flow.build_command({}, root, profile, board, root / "out.kicad_pcb", ["sig"])
            self.assertNotIn("--clearance", command)
            self.assertNotIn("--no-clamp-netclasses", command)
            self.assertIn("--no-fix-drc-settings", command)

    def test_exploration_policy_forces_bounded_non_destructive_search(self):
        config = {
            "exploration": {
                "routing_overrides": {
                    "max_ripup": 0, "max_iterations": 200000,
                    "max_probe_iterations": 5000,
                },
                "environment": {
                    "KICAD_NET_RESCUE": "0", "KICAD_DYNAMIC_ITERATIONS": "0",
                },
            },
        }
        profile = {"routing": {"max_ripup": 8, "max_iterations": 9999999}}
        environment = flow.apply_exploration_policy(config, profile, True)
        self.assertEqual(profile["routing"]["max_ripup"], 0)
        self.assertEqual(profile["routing"]["max_iterations"], 200000)
        self.assertEqual(environment["KICAD_NET_RESCUE"], "0")

    def test_ordered_bundle_profile_is_explicit_and_non_ripping(self):
        config = json.loads((ROOT / "config" / "kicad-routing-tools.json").read_text(encoding="utf-8"))
        profile = config["profiles"]["outer_ordered_bundle"]
        self.assertTrue(profile["require_explicit_include"])
        self.assertEqual(profile["routing"]["max_ripup"], 0)
        self.assertIn("--bus", profile["routing"]["extra_args"])
        self.assertEqual(profile["strategy_evidence"]["net_count"], 5)

    def test_default_krt_policy_batches_low_risk_and_does_not_delegate_failures(self):
        config = json.loads((ROOT / "config" / "kicad-routing-tools.json").read_text(encoding="utf-8"))
        self.assertEqual(config["attempt_policy"]["max_topology_failures"], 2)
        self.assertFalse(config["attempt_policy"]["failure_delegation"])
        self.assertTrue(config["attempt_policy"]["require_explicit_route_area"])
        self.assertTrue(config["attempt_policy"]["require_explicit_strategy_class"])
        self.assertEqual(config["batch_policy"]["mode"], "require_or_explicit_exception")

    def test_physical_critical_probe_profiles_have_explicit_layer_costs(self):
        config = json.loads((ROOT / "config" / "kicad-routing-tools.json").read_text(encoding="utf-8"))
        for name in ("3v3_main_trunk", "ground_plane_access"):
            routing = config["profiles"][name]["routing"]
            self.assertEqual(len(routing["layer_costs"]), len(routing["layers"]))
        self.assertIn("track_width", config["profiles"]["3v3_main_trunk"]["evidence_bound_fields"])

    def test_physical_critical_probe_mode_is_narrowly_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "placement-routability.json").write_text(json.dumps({
                "checkpoint": {
                    "critical_probe_groups": ["ground_return"],
                    "critical_probe_components": {"ground_return": ["local_plane_access"]},
                },
            }), encoding="utf-8")
            args = type("Args", (), {
                "explore": True, "route_area": "ground_return",
                "probe_component": "local_plane_access",
            })()
            with mock.patch.object(flow, "require_operation", return_value={"stage": "physical"}) as guard:
                result = flow.require_critical_probe_mode(args, {"routing": {"max_ripup": 0}}, root)
            self.assertEqual(result["stage"], "physical")
            guard.assert_called_once_with("candidate_route")
            args.route_area = "unreviewed"
            with self.assertRaises(SystemExit):
                flow.require_critical_probe_mode(args, {"routing": {"max_ripup": 0}}, root)

    def test_physical_critical_probe_rejects_wrong_composite_component(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "placement-routability.json").write_text(json.dumps({
                "checkpoint": {
                    "critical_probe_groups": ["usb_data_chain"],
                    "critical_probe_components": {"usb_data_chain": ["raw_fanout"]},
                },
            }), encoding="utf-8")
            args = type("Args", (), {
                "explore": True, "route_area": "usb_data_chain",
                "probe_component": "endpoint_escape",
            })()
            with self.assertRaises(SystemExit):
                flow.require_critical_probe_mode(args, {"routing": {"max_ripup": 0}}, root)

    def test_failure_family_changes_with_placement_or_obstacle_copper(self):
        first = flow.failure_family_signature("outer_low_risk", "PLACEMENT-A", ["B", "A"], ["F.Cu", "B.Cu"], "COPPER-A")
        reordered = flow.failure_family_signature("outer_low_risk", "PLACEMENT-A", ["A", "B"], ["F.Cu", "B.Cu"], "COPPER-A")
        moved = flow.failure_family_signature("outer_low_risk", "PLACEMENT-B", ["A", "B"], ["F.Cu", "B.Cu"], "COPPER-A")
        changed_obstacles = flow.failure_family_signature("outer_low_risk", "PLACEMENT-A", ["A", "B"], ["F.Cu", "B.Cu"], "COPPER-B")
        self.assertEqual(first, reordered)
        self.assertNotEqual(first, moved)
        self.assertNotEqual(first, changed_obstacles)

    def test_success_resets_consecutive_topology_failure_count(self):
        history = [
            {"failure_family": "family", "success": False, "structural_failure": True},
            {"failure_family": "family", "success": True},
            {"failure_family": "other", "success": False, "structural_failure": True},
            {"failure_family": "family", "success": False, "structural_failure": True},
            {"failure_family": "family", "success": False, "structural_failure": False},
        ]
        self.assertEqual(flow.consecutive_family_failures(history, "family"), 1)

    def test_route_area_stall_counts_all_rejections_but_resets_on_acceptance(self):
        history = [
            {"route_area": "usb", "success": False, "outcome_class": "tool_failure"},
            {"route_area": "usb", "success": True, "outcome_class": "accepted"},
            {"route_area": "other", "success": False, "outcome_class": "candidate_drc_regression"},
            {"route_area": "usb", "success": False, "outcome_class": "candidate_drc_regression"},
            {"route_area": "usb", "success": False, "outcome_class": "structural_no_path_or_incomplete"},
        ]
        self.assertEqual(flow.consecutive_area_rejections(history, "usb"), 2)
        health = flow.route_area_health(history, "usb", 2)
        self.assertTrue(health["stalled"])
        self.assertEqual(health["accepted"], 1)

    def test_route_outcome_only_counts_no_path_as_structural(self):
        drc = flow.classify_route_outcome(
            accepted=False, router_exit_code=0, output_exists=True, route_ok=True,
            router_complete=True, connectivity_complete=True, audit_pass=False,
        )
        no_path = flow.classify_route_outcome(
            accepted=False, router_exit_code=0, output_exists=True, route_ok=True,
            router_complete=False, connectivity_complete=False, audit_pass=True,
        )
        self.assertEqual(drc, ("candidate_drc_regression", False))
        self.assertEqual(no_path, ("structural_no_path_or_incomplete", True))

    def test_passing_strategy_resolves_active_route_area_escalation(self):
        prior = [
            {"strategy_signature": "first", "structural_failure": True},
            {"strategy_signature": "second", "structural_failure": True},
        ]
        result = flow.route_area_escalation(
            accepted=True, structural_failure=False,
            prior_area_structural=prior, strategy="passing",
        )
        self.assertFalse(result["routing_review_required"])
        self.assertFalse(result["corridor_review_required"])
        self.assertTrue(result["resolved_by_passing_strategy"])
        self.assertIsNone(result["next"])

    def test_local_obstacle_fingerprint_ignores_far_copper(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board.kicad_pcb"
            board.write_text(
                '(kicad_pcb (segment (start 1 1) (end 2 2) (width 0.2) (layer "F.Cu") (net 1)) '
                '(segment (start 100 100) (end 101 101) (width 0.2) (layer "F.Cu") (net 2)))',
                encoding="utf-8",
            )
            first = flow.copper_obstacle_fingerprint(board, (0, 0, 10, 10))
            whole_first = flow.copper_obstacle_fingerprint(board)
            board.write_text(board.read_text(encoding="utf-8").replace("101 101", "102 102"), encoding="utf-8")
            second = flow.copper_obstacle_fingerprint(board, (0, 0, 10, 10))
            whole_second = flow.copper_obstacle_fingerprint(board)
            self.assertEqual(first, second)
            self.assertNotEqual(whole_first, whole_second)

    def test_drc_cache_reuses_existing_context_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cached.drc.json"
            output.write_text('{"violations":[],"unconnected_items":[]}', encoding="utf-8")
            with mock.patch.object(audit.subprocess, "run") as run:
                report, cache_hit = audit.run_drc(Path("kicad-cli"), Path("board.kicad_pcb"), output)
            self.assertTrue(cache_hit)
            self.assertEqual(report["violations"], [])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
