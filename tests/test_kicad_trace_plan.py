import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("apply_kicad_trace_plan", ROOT / "scripts/apply_kicad_trace_plan.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class KicadTracePlanTests(unittest.TestCase):
    def write_plan(self, plan):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return directory, path

    def base_plan(self):
        return {
            "schema": 1,
            "kind": "kicad_trace_plan",
            "profile": "usb_connector_esd_fanout",
            "track_width_mm": 0.2,
            "clearance_mm": 0.15,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [{
                "net": "USB_DP_RAW", "layer": "B.Cu",
                "start_mm": [1, 2], "end_mm": [3, 4],
            }],
            "vias": [
                {"net": "GND", "at_mm": [1, 1]},
                {"net": "GND", "at_mm": [2, 1]},
            ],
        }

    def test_exact_textual_removal_handles_kicad_10_named_nets(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "candidate.kicad_pcb"
        path.write_text(
            '(kicad_pcb\n'
            '  (segment (start 21.2 35) (end 21.1 34.9) (width 0.2) (layer "F.Cu") (net "TARGET_RX"))\n'
            '  (segment (start 1 1) (end 2 2) (width 0.2) (layer "F.Cu") (net "KEEP"))\n'
            '  (via (at 21.1 34.9) (size 0.45) (drill 0.2) (layers "F.Cu" "B.Cu") (net "TARGET_RX"))\n'
            '  (via (at 18.3 36.8) (size 0.45) (drill 0.2) (layers "F.Cu" "B.Cu") (net "GND"))\n'
            ')\n',
            encoding="utf-8",
        )
        result = MODULE.remove_exact_copper_blocks(path, {
            "remove_segments": [{
                "net": "TARGET_RX", "layer": "F.Cu",
                "start_mm": [21.2, 35.0], "end_mm": [21.1, 34.9],
            }],
            "remove_vias": [{"net": "TARGET_RX", "at_mm": [21.1, 34.9]}],
            "existing_return_vias": [[18.3, 36.8]],
        })
        text = path.read_text(encoding="utf-8")
        self.assertNotIn('(start 21.2 35)', text)
        self.assertNotIn('(at 21.1 34.9)', text)
        self.assertIn('(start 1 1)', text)
        self.assertIn('(at 18.3 36.8)', text)
        self.assertEqual(result["vias"][0]["drill_mm"], 0.2)

    def test_exact_textual_removal_rejects_missing_return_via(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "candidate.kicad_pcb"
        path.write_text('(kicad_pcb)\n', encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.remove_exact_copper_blocks(path, {
                "remove_segments": [], "remove_vias": [],
                "existing_return_vias": [[18.3, 36.8]],
            })

    def test_applied_net_binding_report_rejects_connectivity_reassignment(self):
        class Position:
            x = 18_800_000
            y = 29_000_000

        class Item:
            def __init__(self, net):
                self.net = net

            def GetNetname(self):
                return self.net

            def GetPosition(self):
                return Position()

        report = MODULE.applied_net_binding_report([
            (Item("VTREF"), "VTREF", "segment", 0),
            (Item("GPIO10"), "GND", "via", 3),
        ])
        self.assertFalse(report["success"])
        self.assertEqual(report["checked_item_count"], 2)
        self.assertEqual(report["mismatches"], [{
            "kind": "via",
            "index": 3,
            "expected_net": "GND",
            "actual_net": "GPIO10",
            "position_mm": [18.8, 29.0],
        }])

    def test_accepts_bounded_connector_crossover_with_return_vias(self):
        directory, path = self.write_plan(self.base_plan())
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["profile"], "usb_connector_esd_fanout")

    def test_usb_cc_local_profile_is_bounded_to_cc_nets_and_outer_layers(self):
        profile = MODULE.PROFILES["usb_cc_local"]
        self.assertEqual(profile["nets"], {"USB_CC1", "USB_CC2"})
        self.assertEqual(profile["layers"], {"F.Cu", "B.Cu"})
        self.assertEqual(profile["via_nets"], {"USB_CC1", "USB_CC2", "GND"})

    def test_geometry_preflight_rejects_trace_crossing_npth(self):
        plan = {
            "segments": [{
                "net": "BOOT0", "layer": "B.Cu",
                "start_mm": [1.2, 38.73], "end_mm": [5.33, 31.6],
            }],
            "vias": [],
        }
        violations = MODULE.hole_clearance_violations(
            plan,
            {"track_width_mm": 0.2, "via_diameter_mm": 0.45, "via_drill_mm": 0.2},
            [{
                "center_mm": [1.984, 36.19], "radius_mm": 0.4953,
                "npth": True, "net": None, "label": "J2:NPTH",
            }],
            hole_clearance_mm=0.25,
            hole_to_hole_mm=0.25,
        )
        self.assertEqual(violations[0]["type"], "hole_clearance")
        self.assertEqual(violations[0]["hole"], "J2:NPTH")

    def test_geometry_preflight_accepts_dogleg_outside_npth(self):
        plan = {
            "segments": [
                {"net": "BOOT0", "layer": "B.Cu", "start_mm": [1.0, 38.73], "end_mm": [1.0, 34.8]},
                {"net": "BOOT0", "layer": "B.Cu", "start_mm": [1.0, 34.8], "end_mm": [5.33, 31.6]},
            ],
            "vias": [],
        }
        violations = MODULE.hole_clearance_violations(
            plan,
            {"track_width_mm": 0.2, "via_diameter_mm": 0.45, "via_drill_mm": 0.2},
            [{
                "center_mm": [1.984, 36.19], "radius_mm": 0.4953,
                "npth": True, "net": None, "label": "J2:NPTH",
            }],
            hole_clearance_mm=0.25,
            hole_to_hole_mm=0.25,
        )
        self.assertEqual(violations, [])

    def test_copper_preflight_rejects_crossing_existing_track(self):
        plan = {
            "segments": [{"net": "USB_CC2", "layer": "F.Cu", "start_mm": [0, 1], "end_mm": [2, 1]}],
            "vias": [],
        }
        obstacles = [{
            "kind": "segment", "net": "USB_VBUS_RAW", "layers": {"F.Cu"},
            "start": (1, 0), "end": (1, 2), "radius": 0.1, "label": "VBUS wall",
        }]
        violations = MODULE.copper_clearance_violations(
            plan, {"track_width_mm": 0.2, "via_diameter_mm": 0.45}, obstacles,
            clearance_mm=0.2,
        )
        self.assertEqual(violations[0]["obstacle_net"], "USB_VBUS_RAW")
        self.assertEqual(violations[0]["type"], "copper_clearance")

    def test_copper_preflight_allows_same_net_connection(self):
        plan = {
            "segments": [{"net": "USB_CC2", "layer": "F.Cu", "start_mm": [0, 0], "end_mm": [1, 0]}],
            "vias": [],
        }
        obstacles = [{
            "kind": "polygon", "net": "USB_CC2", "layers": {"F.Cu"},
            "polygon": [(0.8, -0.3), (1.2, -0.3), (1.2, 0.3), (0.8, 0.3)],
            "label": "destination pad",
        }]
        self.assertEqual(MODULE.copper_clearance_violations(
            plan, {"track_width_mm": 0.2, "via_diameter_mm": 0.45}, obstacles,
            clearance_mm=0.2,
        ), [])

    def test_copper_preflight_checks_planned_different_net_via(self):
        plan = {
            "segments": [{"net": "USB_CC2", "layer": "B.Cu", "start_mm": [0, 0], "end_mm": [2, 0]}],
            "vias": [{"net": "GND", "at_mm": [1, 0.3]}],
        }
        violations = MODULE.copper_clearance_violations(
            plan, {"track_width_mm": 0.2, "via_diameter_mm": 0.45}, [],
            clearance_mm=0.2,
        )
        self.assertEqual(violations[0]["obstacle"], "planned via 0")

    def test_connector_geometry_is_bound_to_reviewed_exception(self):
        self.assertEqual(
            MODULE.reviewed_geometry("usb_connector_esd_fanout"),
            {
                "track_width_mm": 0.2,
                "clearance_mm": 0.15,
                "via_diameter_mm": 0.45,
                "via_drill_mm": 0.2,
            },
        )

    def test_rejects_bottom_copper_without_return_via_pair(self):
        plan = self.base_plan()
        plan["vias"] = [{"net": "GND", "at_mm": [1, 1]}]
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_bottom_copper_can_reuse_explicit_existing_return_vias(self):
        plan = self.base_plan()
        plan["vias"] = []
        plan["existing_return_vias"] = [[1, 1], [2, 1]]
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["existing_return_vias"], plan["existing_return_vias"])

    def test_existing_return_vias_remain_bounded_points(self):
        plan = self.base_plan()
        plan["vias"] = []
        plan["existing_return_vias"] = [[1, 1]] * 9
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_rejects_non_usb_signal_copper(self):
        plan = self.base_plan()
        plan["segments"][0]["net"] = "GND"
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_protected_pair_requires_fcu_and_parallel_evidence(self):
        plan = self.base_plan()
        plan.update({"profile": "usb_device_diff_fcu", "pair_gap_mm": 0.2032, "neckdown_width_mm": 0.2})
        plan["segments"] = [
            {"net": "USB_DP_ESD", "layer": "F.Cu", "start_mm": [0, 0], "end_mm": [0, 5]},
            {"net": "USB_DM_ESD", "layer": "F.Cu", "start_mm": [0.493776, 0], "end_mm": [0.493776, 5]},
        ]
        plan["vias"] = []
        plan["parallel_pairs"] = [{"positive_segment": 0, "negative_segment": 1}]
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        loaded = MODULE.load_plan(path)
        MODULE.validate_parallel_pairs(loaded, {"track_width_mm": 0.290576, "pair_gap_mm": 0.2032})

    def test_protected_pair_geometry_binds_short_endpoint_neckdown_and_impedance_width(self):
        geometry = MODULE.reviewed_geometry("usb_device_diff_fcu")
        self.assertEqual(geometry["neckdown_width_mm"], 0.2)
        self.assertEqual(geometry["track_width_mm"], 0.290576)
        self.assertEqual(geometry["pair_gap_mm"], 0.2032)

    def test_usb_5v_geometry_binds_pad_escape_and_power_widths(self):
        geometry = MODULE.reviewed_geometry("usb_5v_distribution")
        self.assertEqual(geometry["neckdown_width_mm"], 0.2)
        self.assertEqual(geometry["track_width_mm"], 1.27)

    def test_usb_shield_bond_uses_one_transition_and_reviewed_wide_strap(self):
        plan = self.base_plan()
        plan.update({
            "profile": "usb_shield_bond",
            "track_width_mm": 1.27,
            "neckdown_width_mm": 0.2,
            "clearance_mm": 0.2,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [
                {"net": "USB_SHIELD", "layer": "F.Cu", "width_mm": 0.2, "start_mm": [1, 2], "end_mm": [2, 2]},
                {"net": "USB_SHIELD", "layer": "B.Cu", "start_mm": [2, 2], "end_mm": [3, 2]},
            ],
            "vias": [{"net": "USB_SHIELD", "at_mm": [2, 2]}],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        loaded = MODULE.load_plan(path)
        self.assertEqual(loaded["profile"], "usb_shield_bond")
        self.assertEqual(MODULE.reviewed_geometry("usb_shield_bond")["track_width_mm"], 1.27)
        plan["vias"].append({"net": "USB_SHIELD", "at_mm": [2.5, 2]})
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_3v3_main_trunk_binds_esp32_escape_and_power_widths(self):
        geometry = MODULE.reviewed_geometry("3v3_main_trunk")
        self.assertEqual(geometry["neckdown_width_mm"], 0.2)
        self.assertEqual(geometry["track_width_mm"], 1.27)

    def test_ground_return_profile_allows_only_bounded_gnd_vias(self):
        plan = self.base_plan()
        plan.update({"profile": "ground_return_vias", "segments": []})
        plan["vias"] = [{"net": "GND", "at_mm": [10, 10]}]
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["vias"], plan["vias"])

    def test_ground_return_profile_rejects_signal_vias(self):
        plan = self.base_plan()
        plan.update({"profile": "ground_return_vias", "segments": []})
        plan["vias"] = [{"net": "PROBE_SWCLK", "at_mm": [10, 10]}]
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_routing_corridor_adjustment_accepts_bounded_exact_segment_removal(self):
        plan = self.base_plan()
        plan.update({
            "profile": "routing_corridor_adjustment",
            "segments": [{
                "net": "GPIO12", "layer": "F.Cu",
                "start_mm": [1, 2], "end_mm": [3, 4],
            }],
            "vias": [],
            "remove_segments": [{
                "net": "GPIO12", "layer": "B.Cu",
                "start_mm": [4, 5], "end_mm": [6, 7],
            }],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        loaded = MODULE.load_plan(path)
        self.assertEqual(loaded["remove_segments"], plan["remove_segments"])

    def test_routing_corridor_adjustment_rejects_unreviewed_removed_net(self):
        plan = self.base_plan()
        plan.update({
            "profile": "routing_corridor_adjustment",
            "segments": [{
                "net": "GPIO12", "layer": "F.Cu",
                "start_mm": [1, 2], "end_mm": [3, 4],
            }],
            "vias": [],
            "remove_segments": [{
                "net": "USB_DP", "layer": "F.Cu",
                "start_mm": [4, 5], "end_mm": [6, 7],
            }],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_critical_trace_probe_requires_reviewed_composite_component(self):
        self.assertEqual(
            MODULE.validate_critical_probe("usb_data_chain", "raw_fanout"),
            ("usb_data_chain", "raw_fanout"),
        )
        with self.assertRaises(SystemExit):
            MODULE.validate_critical_probe("usb_data_chain", "unknown")

    def test_anchor_groups_require_bounded_reviewed_pad_or_via_anchors(self):
        plan = self.base_plan()
        plan["anchor_groups"] = [{
            "net": "USB_DP_RAW",
            "pads": ["J1:A6", "U2:6"],
            "vias": [],
        }]
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["anchor_groups"], plan["anchor_groups"])
        plan["anchor_groups"][0]["pads"] = ["J1:A6"]
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_acceptance_requires_anchor_group_for_every_routed_net(self):
        plan = self.base_plan()
        with self.assertRaises(SystemExit):
            MODULE.validate_acceptance_anchors(plan)
        plan["anchor_groups"] = [{
            "net": "USB_DP_RAW",
            "pads": ["J1:A6", "U2:6"],
            "vias": [],
        }]
        MODULE.validate_acceptance_anchors(plan)

    def test_via_only_plan_does_not_invent_pad_anchor_requirement(self):
        plan = self.base_plan()
        plan["segments"] = []
        MODULE.validate_acceptance_anchors(plan)

    def test_high_current_ground_return_is_width_bound_and_fcu_only(self):
        plan = self.base_plan()
        plan.update({
            "profile": "high_current_ground_return",
            "track_width_mm": 1.27,
            "clearance_mm": 0.2,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [{
                "net": "GND", "layer": "F.Cu",
                "start_mm": [1, 2], "end_mm": [3, 2],
            }],
            "vias": [
                {"net": "GND", "at_mm": [3, 1.5]},
                {"net": "GND", "at_mm": [3, 2.0]},
                {"net": "GND", "at_mm": [3, 2.5]},
            ],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["profile"], "high_current_ground_return")
        self.assertEqual(
            MODULE.reviewed_geometry("high_current_ground_return"),
            {
                "track_width_mm": 1.27,
                "clearance_mm": 0.2,
                "via_diameter_mm": 0.45,
                "via_drill_mm": 0.2,
            },
        )
        plan["segments"][0]["layer"] = "B.Cu"
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_buck_feedback_sense_uses_reviewed_default_geometry(self):
        plan = self.base_plan()
        plan.update({
            "profile": "buck_feedback_sense",
            "track_width_mm": 0.2,
            "clearance_mm": 0.2,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [{
                "net": "3V3", "layer": "F.Cu",
                "start_mm": [1, 2], "end_mm": [3, 2],
            }],
            "vias": [],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["profile"], "buck_feedback_sense")
        self.assertEqual(MODULE.reviewed_geometry("buck_feedback_sense")["track_width_mm"], 0.2)

        plan["segments"][0]["layer"] = "B.Cu"
        plan["vias"] = [
            {"net": "3V3", "at_mm": [1, 2]},
            {"net": "GND", "at_mm": [1, 1]},
            {"net": "GND", "at_mm": [2, 1]},
        ]
        path.write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(MODULE.load_plan(path)["segments"][0]["layer"], "B.Cu")

    def test_3v3_local_escape_is_fcu_only_and_has_no_vias(self):
        plan = self.base_plan()
        plan.update({
            "profile": "3v3_local_escape",
            "track_width_mm": 0.2,
            "clearance_mm": 0.2,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [{
                "net": "3V3", "layer": "F.Cu",
                "start_mm": [1, 2], "end_mm": [3, 2],
            }],
            "vias": [],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["profile"], "3v3_local_escape")
        plan["vias"] = [{"net": "3V3", "at_mm": [3, 2]}]
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_3v3_low_current_branch_rejects_bottom_layer_and_vias(self):
        plan = self.base_plan()
        plan.update({
            "profile": "3v3_low_current_branch",
            "track_width_mm": 0.2,
            "clearance_mm": 0.2,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [{
                "net": "3V3", "layer": "F.Cu",
                "start_mm": [1, 2], "end_mm": [3, 2],
            }],
            "vias": [],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["profile"], "3v3_low_current_branch")
        plan["segments"][0]["layer"] = "B.Cu"
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_3v3_low_current_layer_transition_allows_one_reviewed_power_via(self):
        plan = self.base_plan()
        plan.update({
            "profile": "3v3_low_current_layer_transition",
            "track_width_mm": 0.2,
            "clearance_mm": 0.2,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [
                {"net": "3V3", "layer": "F.Cu", "start_mm": [1, 2], "end_mm": [2, 2]},
                {"net": "3V3", "layer": "B.Cu", "start_mm": [2, 2], "end_mm": [3, 2]},
            ],
            "vias": [{"net": "3V3", "at_mm": [2, 2]}],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        loaded = MODULE.load_plan(path)
        self.assertEqual(loaded["profile"], "3v3_low_current_layer_transition")
        self.assertFalse(MODULE.PROFILES[loaded["profile"]]["requires_return_vias"])

        plan["vias"].append({"net": "3V3", "at_mm": [2, 3]})
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)

    def test_3v3_main_trunk_is_power_width_bound_and_requires_return_vias_on_bcu(self):
        plan = self.base_plan()
        plan.update({
            "profile": "3v3_main_trunk",
            "track_width_mm": 1.27,
            "clearance_mm": 0.2,
            "via_diameter_mm": 0.45,
            "via_drill_mm": 0.2,
            "segments": [{
                "net": "3V3", "layer": "B.Cu",
                "start_mm": [1, 2], "end_mm": [3, 4],
            }],
            "vias": [
                {"net": "3V3", "at_mm": [1, 2]},
                {"net": "GND", "at_mm": [1, 1]},
                {"net": "GND", "at_mm": [2, 1]},
            ],
        })
        directory, path = self.write_plan(plan)
        self.addCleanup(directory.cleanup)
        self.assertEqual(MODULE.load_plan(path)["profile"], "3v3_main_trunk")
        self.assertEqual(MODULE.reviewed_geometry("3v3_main_trunk")["track_width_mm"], 1.27)
        plan["vias"] = [{"net": "3V3", "at_mm": [1, 2]}]
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(SystemExit):
            MODULE.load_plan(path)


if __name__ == "__main__":
    unittest.main()
