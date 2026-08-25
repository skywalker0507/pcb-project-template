from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "template-engine/src"))

from pcb_template_engine import API_VERSION, __version__
from pcb_template_engine.manufacturing import (
    compile_registry,
    evaluate_dfm_observation,
    evaluate_gerbers,
    evaluate_pre_export,
    validate_registry,
)
from pcb_template_engine.project import activate, source_digest


class TemplateEngineRegistryTests(unittest.TestCase):
    def policy(self) -> dict[str, object]:
        return {
            "policy_sha256": "A" * 64,
            "board_stack": {"active": "four", "copper_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]},
            "manufacturing": {
                "manufacturer": "demo",
                "order_profile": {
                    "status": "reviewed",
                    "fabrication": {"surface_finish": "ENIG", "solder_mask_color": "black"},
                    "assembly": {"service": "smt", "required_dfm_modules": ["pcb", "smt"]},
                },
                "gerber_file_patterns": {
                    "F.Cu": "*-F_Cu.gtl", "In1.Cu": "*-In1_Cu.g1",
                    "In2.Cu": "*-In2_Cu.g2", "B.Cu": "*-B_Cu.gbl",
                },
                "gerber_rules": [{
                    "id": "antenna", "kind": "copper_keepout", "layer_scope": "all_copper",
                    "geometry": {"kind": "board_bbox_mm", "value": [0, 0, 10, 5]},
                }],
                "special_processes": {"filled_vias": {
                    "status": "confirmation_required",
                    "allowed_confirmed_outcomes": ["approved"],
                    "selected_outcome": None,
                    "evidence": None,
                }},
            },
        }

    def registry(self) -> dict[str, object]:
        return compile_registry(
            self.policy(), engine_version=__version__, engine_api_version=API_VERSION,
            parser={"package": "gerbonara", "version": "1.6.3"},
        )

    def test_engine_contract_verifies_source_lock(self) -> None:
        contract = ROOT / "config/template-engine.json"
        result = activate(ROOT, contract)
        self.assertTrue(result["success"])
        self.assertEqual(result["source_sha256"], source_digest(ROOT / "template-engine"))

    def test_engine_sources_do_not_contain_project_identity(self) -> None:
        content = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "template-engine/src").rglob("*.py"))
        self.assertNotIn("esp32-s3-remote-flashing-probe", content.lower())
        self.assertNotIn("jlcpcb", content.lower())
        self.assertNotIn("u1_", content.lower())

    def test_registry_expands_all_active_copper_layers(self) -> None:
        registry = self.registry()
        keepout = next(rule for rule in registry["rules"] if rule["id"] == "antenna")
        self.assertEqual(keepout["layers"], ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
        self.assertEqual(registry["rules"][0]["kind"], "gerber_file_presence")

    def test_registry_tamper_is_rejected(self) -> None:
        registry = self.registry()
        registry["manufacturer"] = "tampered"
        with self.assertRaises(SystemExit):
            validate_registry(registry, self.policy(), engine_version=__version__, engine_api_version=API_VERSION)

    def test_pending_special_process_fails_pre_export(self) -> None:
        result = evaluate_pre_export(self.registry())
        self.assertFalse(result["success"])
        self.assertEqual(result["pending"], ["filled_vias"])

    def test_dfm_scope_requires_order_parameters_and_all_modules(self) -> None:
        mismatch = evaluate_dfm_observation(self.registry(), {
            "order_parameters": {"surface_finish": "HASL_LEADED", "solder_mask_color": "green"},
            "analysis_modules": {
                "pcb": {"executed": True, "pass": True},
                "smt": {"executed": False, "pass": False},
            },
        })
        self.assertFalse(mismatch["dfm_pass"])
        self.assertEqual(mismatch["mismatches"], ["dfm_module:smt", "solder_mask_color", "surface_finish"])
        matched = evaluate_dfm_observation(self.registry(), {
            "order_parameters": {"surface_finish": "ENIG", "solder_mask_color": "black"},
            "analysis_modules": {
                "pcb": {"executed": True, "pass": True},
                "smt": {"executed": True, "pass": True},
            },
        })
        self.assertTrue(matched["dfm_pass"])

    def test_missing_required_gerber_layer_fails_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "demo-F_Cu.gtl").write_text("", encoding="utf-8")
            result = evaluate_gerbers(
                self.registry(), folder,
                open_gerber=lambda path: SimpleNamespace(objects=[]),
                file_sha256=lambda path: "0" * 64,
            )
        self.assertFalse(result["success"])
        presence = result["rules"][0]
        self.assertFalse(presence["pass"])


if __name__ == "__main__":
    unittest.main()
