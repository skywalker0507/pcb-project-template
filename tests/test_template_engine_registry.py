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
    evaluate_cost_policy,
    evaluate_dfm_observation,
    evaluate_gerbers,
    evaluate_pre_export,
    evaluate_quote_observation,
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
                "cost_review": {
                    "status": "reviewed",
                    "pricing_model": "live_quote_only",
                    "currency": "CNY",
                    "drivers": [{
                        "id": "filled_vias_cost", "selection": "epoxy filled", "selected": True,
                        "decision": "required", "cost_risk": "quote_required",
                        "reason": "geometry requires filling", "evidence": ["review.json"],
                        "lower_cost_alternative": "move vias", "quote_aliases": ["Via filling"],
                    }, {
                        "id": "kelvin_test", "selection": "four wire", "selected": False,
                        "decision": "not_required", "cost_risk": "quote_required",
                        "reason": "no Kelvin requirement", "evidence": [],
                        "lower_cost_alternative": "normal e-test", "quote_aliases": ["Four wire test"],
                    }],
                },
            },
        }

    def registry(self) -> dict[str, object]:
        return compile_registry(
            self.policy(), engine_version=__version__, engine_api_version=API_VERSION,
            parser={"package": "gerbonara", "version": "1.6.3"},
        )

    def quote(self, *, status: str, label: str, amount: float, selection: str | None = None) -> dict[str, object]:
        return {
            "schema": 2,
            "kind": "manufacturing_quote_observation",
            "source": {"provider": "demo", "channel": "test"},
            "observed_at": "2026-08-27T12:00:00+08:00",
            "currency": "CNY",
            "quantity": 5,
            "total": amount,
            "breakdown_scope": "complete",
            "work_packet_content_sha256": "B" * 64,
            "artifacts": {
                role: {"sha256": character * 64}
                for role, character in (("board", "1"), ("gerber_zip", "2"), ("bom", "3"), ("cpl", "4"))
            },
            "order_parameters": {
                "surface_finish": "ENIG", "solder_mask_color": "black", "assembly_service": "smt",
            },
            "approval": {"status": status, "reviewer": "tester" if status == "accepted" else None},
            "line_items": [{"label": label, "category": "surcharge", "selection": selection, "amount": amount}],
        }

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

    def test_cost_policy_accepts_justified_selection_and_rejects_unselected_quote_line(self) -> None:
        policy = evaluate_cost_policy(self.registry())
        self.assertTrue(policy["success"])
        self.assertEqual(policy["selected_surcharge_drivers"], ["filled_vias_cost"])
        quote = evaluate_quote_observation(
            self.registry(), self.quote(status="accepted", label="Four wire test", amount=100),
        )
        self.assertFalse(quote["success"])
        self.assertEqual(quote["unexpected_lines"], ["Four wire test"])

    def test_quote_review_requires_explicit_approval(self) -> None:
        quote = evaluate_quote_observation(self.registry(), self.quote(
            status="pending", label="Via filling", selection="epoxy filled and capped", amount=80,
        ))
        self.assertFalse(quote["success"])
        self.assertFalse(quote["approval_pass"])

    def test_quote_review_accepts_exact_bound_live_quote(self) -> None:
        quote = evaluate_quote_observation(self.registry(), self.quote(
            status="accepted", label="Via filling", selection="epoxy filled and capped", amount=80,
        ))
        self.assertTrue(quote["success"], quote)
        self.assertTrue(quote["binding_pass"])

    def test_quote_review_accepts_base_cost_and_rejects_unreconciled_total(self) -> None:
        observation = self.quote(status="accepted", label="PCB base", amount=20)
        observation["line_items"][0]["category"] = "fabrication_base"
        quote = evaluate_quote_observation(self.registry(), observation)
        self.assertTrue(quote["success"], quote)
        observation["total"] = 21
        mismatch = evaluate_quote_observation(self.registry(), observation)
        self.assertFalse(mismatch["success"])
        self.assertIn("quote_line_items_do_not_reconcile_to_total", mismatch["binding_problems"])

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
