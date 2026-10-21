import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "promote_kicad_physical_candidate",
    ROOT / "scripts/promote_kicad_physical_candidate.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class KicadPhysicalPromotionTests(unittest.TestCase):
    def placement(self):
        return {
            "kind": "kicad_placement",
            "success": True,
            "isolated_candidate": True,
            "before_sha256": "INPUT",
            "board_sha256": "CANDIDATE",
            "placements": [{"reference": "C2"}],
        }

    def test_allows_one_exact_zero_copper_preroute_increase(self):
        result = MODULE.validate_unconnected_policy(
            delta=1,
            allow_preroute_increase=True,
            new_violations=[],
            placement=self.placement(),
            candidate_hash="CANDIDATE",
            target_hash="INPUT",
            target_copper_items=0,
            candidate_copper_items=0,
        )
        self.assertTrue(result["allowed"])

    def test_rejects_exception_when_candidate_has_copper(self):
        with self.assertRaises(SystemExit):
            MODULE.validate_unconnected_policy(
                delta=1,
                allow_preroute_increase=True,
                new_violations=[],
                placement=self.placement(),
                candidate_hash="CANDIDATE",
                target_hash="INPUT",
                target_copper_items=0,
                candidate_copper_items=1,
            )

    def test_rejects_exception_without_explicit_opt_in(self):
        with self.assertRaises(SystemExit):
            MODULE.validate_unconnected_policy(
                delta=1,
                allow_preroute_increase=False,
                new_violations=[],
                placement=self.placement(),
                candidate_hash="CANDIDATE",
                target_hash="INPUT",
                target_copper_items=0,
                candidate_copper_items=0,
            )

    def test_routability_gate_requires_improvement_and_all_probe_groups(self):
        routability = {
            "kind": "kicad_placement_routability", "success": True,
            "candidate": {"board_sha256": "BOARD", "placement_sha256": "PLACEMENT"},
            "comparison": {"improved_without_critical_regression": True},
        }
        probes = [
            {"accepted": True, "placement_sha256": "PLACEMENT", "route_area": "usb"},
            {"accepted": True, "placement_sha256": "PLACEMENT", "probe_group": "power"},
        ]
        result = MODULE.validate_routability_and_probes(routability, probes, "BOARD", ["usb", "power"])
        self.assertTrue(result["routability_improved"])

    def test_routability_gate_rejects_missing_probe_group(self):
        routability = {
            "kind": "kicad_placement_routability", "success": True,
            "candidate": {"board_sha256": "BOARD", "placement_sha256": "PLACEMENT"},
            "comparison": {"improved_without_critical_regression": True},
        }
        with self.assertRaises(SystemExit):
            MODULE.validate_routability_and_probes(routability, [], "BOARD", ["usb"])

    def test_composite_probe_group_requires_every_reviewed_component(self):
        routability = {
            "kind": "kicad_placement_routability", "success": True,
            "candidate": {"board_sha256": "BOARD", "placement_sha256": "PLACEMENT"},
            "comparison": {"improved_without_critical_regression": True},
        }
        probes = [
            {"accepted": True, "placement_sha256": "PLACEMENT", "route_area": "usb", "probe_component": "raw"},
            {"accepted": True, "placement_sha256": "PLACEMENT", "route_area": "usb", "probe_component": "main"},
        ]
        with self.assertRaises(SystemExit):
            MODULE.validate_routability_and_probes(
                routability, probes, "BOARD", ["usb"], {"usb": ["raw", "main", "endpoint"]},
            )
        probes.append({
            "accepted": True, "placement_sha256": "PLACEMENT",
            "route_area": "usb", "probe_component": "endpoint",
        })
        result = MODULE.validate_routability_and_probes(
            routability, probes, "BOARD", ["usb"], {"usb": ["raw", "main", "endpoint"]},
        )
        self.assertEqual(result["passing_probe_components"]["usb"], ["endpoint", "main", "raw"])


if __name__ == "__main__":
    unittest.main()
