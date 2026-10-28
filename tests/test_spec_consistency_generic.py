from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_spec_consistency import consistency_problems, impedance_problems


class GenericSpecConsistencyTests(unittest.TestCase):
    def project(self, root):
        (root / "config").mkdir()
        for name in ("requirements", "product-decisions", "design-data", "selected-parts", "jlcpcb-parts"):
            data = {"schema":1,"project":"fixture","revision":"Rev T","status":"electrical_review","parts":[],"board_sha256":None}
            if name == "requirements":
                data.update(schema=2,controlled_impedance={})
            (root / f"config/{name}.json").write_text(json.dumps(data))
        (root / "HARDWARE_SPEC.md").write_text("# Fixture — Rev T\n", encoding="utf-8")
        (root / "config/pcb-flow.json").write_text(json.dumps({"project":{"name":"fixture"}}))

    def evidence(self, root):
        evidence = {"schema":2,"stackup":{"template":"fixture-two-layer","layers":2,"physical_layers":[
            {"name":"F.Cu","kind":"copper","thickness_mm":0.02},
            {"name":"dielectric","kind":"core","thickness_mm":0.4},
            {"name":"B.Cu","kind":"copper","thickness_mm":0.02}]},
            "usb2_differential":{"target_ohm":100,"pcb_geometry_mm":{"base_width":0.18,"pair_gap":0.16},
                "rounded_geometry_verification_response":{"dResultValid":1,"dImpedance":99.8}}}
        path = root / "impedance.json"
        path.write_text(json.dumps(evidence))
        declaration = {"usb2":{"evidence":"impedance.json","target_ohm":100,"stack_template":"fixture-two-layer","geometry_mm":{"base_width":0.18,"pair_gap":0.16}}}
        return evidence, path, declaration

    def test_no_usb_project_does_not_inherit_usb_stack_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            self.assertEqual(consistency_problems(root), [])

    def test_other_reviewed_stack_and_geometry_can_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, declaration = self.evidence(root)
            self.assertEqual(impedance_problems(root, declaration), [])

    def test_declared_geometry_stack_and_target_remain_strict(self):
        for mutation, expected in (("geometry", "geometry does not match"),("stack", "stack template"),("target", "target impedance"),("calculator", "calculator verification"),("layer-count", "copper layer count")):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence, path, declaration = self.evidence(root)
                if mutation == "geometry": evidence["usb2_differential"]["pcb_geometry_mm"]["base_width"] = 0.3
                elif mutation == "stack": evidence["stackup"]["template"] = "different"
                elif mutation == "target": evidence["usb2_differential"]["target_ohm"] = 90
                elif mutation == "calculator": evidence["usb2_differential"]["rounded_geometry_verification_response"]["dResultValid"] = 0
                else: evidence["stackup"]["layers"] = 4
                path.write_text(json.dumps(evidence))
                self.assertTrue(any(expected in item for item in impedance_problems(root, declaration)))

    def test_declared_channel_cannot_skip_evidence(self):
        self.assertIn("evidence path is missing", " ".join(impedance_problems(ROOT, {"usb2":{}})))
        self.assertTrue(impedance_problems(ROOT, None))

    def test_inherited_project_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.project(root)
            path = root / "config/product-decisions.json"
            data = json.loads(path.read_text())
            data["project"] = "inherited-project"
            path.write_text(json.dumps(data))
            self.assertTrue(any("project identity mismatch" in item for item in consistency_problems(root)))


if __name__ == "__main__":
    unittest.main()
