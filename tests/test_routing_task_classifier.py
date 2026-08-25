import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("routing_classifier", ROOT / "scripts/classify_routing_task.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


POLICY = {
    "local_direct": {
        "maximum_pad_endpoints": 2,
        "maximum_span_mm": 5.0,
        "require_no_existing_copper": True,
        "recommended_engine": "explicit_trace_plan",
    },
    "ordered_scopes": [{
        "name": "usb",
        "groups": [
            {"name": "configuration", "nets": ["USB_CC1", "USB_CC2"]},
            {"name": "data", "nets": ["USB_DP_RAW", "USB_DM_RAW"]},
            {"name": "power", "nets": ["USB_VBUS_RAW"]},
        ],
    }],
    "reviewed_multipoint_profiles": {
        "3V3": {
            "sequence": ["main_trunk", "local_decoupling", "low_current_bias_branches"],
            "profiles": ["3v3_main_trunk", "3v3_local_escape", "3v3_low_current_branch"],
        },
    },
    "krt_prerequisite_gate": {
        "name": "power_first",
        "required_connected_nets": ["USB_VBUS_RAW", "USB_5V", "3V3"],
        "exempt_nets": ["USB_VBUS_RAW", "USB_5V", "3V3", "USB_DP", "USB_DM"],
    },
}


class RoutingTaskClassifierTests(unittest.TestCase):
    def test_short_two_endpoint_net_uses_explicit_trace(self):
        metrics = {"USB_CC1": {"pad_endpoint_count": 2, "maximum_pad_span_mm": 3.0, "existing_copper_items": 0}}
        receipt = MODULE.classify_metrics(metrics, ["USB_CC1"], POLICY, lambda net: False)
        self.assertEqual(receipt["recommended_engine"], "explicit_trace_plan")
        self.assertFalse(receipt["krt_allowed"])

    def test_vbus_waits_for_configuration_and_data_groups(self):
        metrics = {"USB_VBUS_RAW": {"pad_endpoint_count": 6, "maximum_pad_span_mm": 10.0, "existing_copper_items": 0}}
        receipt = MODULE.classify_metrics(metrics, ["USB_VBUS_RAW"], POLICY, lambda net: False)
        self.assertEqual(receipt["recommended_engine"], "defer_for_routing_order")
        self.assertEqual({item["net"] for item in receipt["routing_order_blockers"]}, {"USB_CC1", "USB_CC2", "USB_DP_RAW", "USB_DM_RAW"})

    def test_atomic_request_satisfies_its_own_prerequisites(self):
        selected = ["USB_CC1", "USB_CC2", "USB_DP_RAW", "USB_DM_RAW", "USB_VBUS_RAW"]
        metrics = {net: {"pad_endpoint_count": 3, "maximum_pad_span_mm": 8.0, "existing_copper_items": 0} for net in selected}
        receipt = MODULE.classify_metrics(metrics, selected, POLICY, lambda net: True)
        self.assertEqual(receipt["routing_order_blockers"], [])
        self.assertTrue(receipt["krt_allowed"])

    def test_multipoint_power_net_is_decomposed_instead_of_sent_to_krt(self):
        metrics = {"3V3": {"pad_endpoint_count": 14, "maximum_pad_span_mm": 42.0, "existing_copper_items": 18}}
        receipt = MODULE.classify_metrics(metrics, ["3V3"], POLICY, lambda net: False)
        self.assertEqual(receipt["recommended_engine"], "explicit_trace_plan")
        self.assertEqual(receipt["reason"], "reviewed_multipoint_decomposition_required")
        self.assertEqual(receipt["task_decomposition"]["3V3"]["profiles"][0], "3v3_main_trunk")

    def test_low_risk_krt_waits_for_critical_power(self):
        metrics = {"GPIO12": {"pad_endpoint_count": 3, "maximum_pad_span_mm": 12.0, "existing_copper_items": 0}}
        receipt = MODULE.classify_metrics(metrics, ["GPIO12"], POLICY, lambda net: False)
        self.assertEqual(receipt["recommended_engine"], "defer_for_critical_power")
        self.assertFalse(receipt["krt_allowed"])
        self.assertEqual({row["net"] for row in receipt["krt_gate_blockers"]}, {"USB_VBUS_RAW", "USB_5V", "3V3"})

    def test_low_risk_krt_is_allowed_after_critical_power(self):
        metrics = {"GPIO12": {"pad_endpoint_count": 3, "maximum_pad_span_mm": 12.0, "existing_copper_items": 0}}
        receipt = MODULE.classify_metrics(metrics, ["GPIO12"], POLICY, lambda net: True)
        self.assertTrue(receipt["krt_allowed"])
        self.assertEqual(receipt["krt_gate_blockers"], [])


if __name__ == "__main__":
    unittest.main()
