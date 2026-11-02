import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import contextlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "promote_kicad_physical_candidate",
    ROOT / "scripts/promote_kicad_physical_candidate.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
import check_kicad_placement_immutability as REUSE
import record_physical_review as REVIEW
import artifact_store as STORE


class KicadPhysicalPromotionTests(unittest.TestCase):
    def test_promotion_allows_project_rules_without_optional_dru(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate, target = Path(folder) / "candidate.kicad_pcb", Path(folder) / "target.kicad_pcb"
            candidate.write_text("board"); candidate.with_suffix(".kicad_pro").write_text("project rules")
            MODULE.validate_companions(candidate, target)
            self.assertFalse(candidate.with_suffix(".kicad_dru").exists())
            target.with_suffix(".kicad_dru").write_text("existing custom rule")
            with self.assertRaisesRegex(SystemExit, "omits active custom rules"):
                MODULE.validate_companions(candidate, target)
            candidate.with_suffix(".kicad_dru").write_text("reviewed custom rule")
            MODULE.validate_companions(candidate, target)
            candidate.with_suffix(".kicad_pro").unlink()
            with self.assertRaisesRegex(SystemExit, "Required candidate companion"):
                MODULE.validate_companions(candidate, target)

    def test_published_library_table_resolves_the_same_reviewed_resources(self):
        from check_kicad_footprint_equivalence import libraries
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / "elec/layout/sim"
            work.mkdir(parents=True)
            source = work / "board.kicad_pcb"; source.write_text("unchanged accepted board")
            table = work / "fp-lib-table"
            table.write_text('(fp_lib_table (lib (name "Reviewed")(uri "${KIPRJMOD}/../../../parts/Reviewed.pretty")))')
            released = root / "build/05-placement/release/test"
            run = STORE.ArtifactRun("physical", "05-placement", "test", "promotion", root / "tmp", root / "evidence", released, root / "run.json")
            (root / "build/05-placement").mkdir(parents=True)
            original = table.read_bytes()
            with mock.patch.object(STORE, "ROOT", root), mock.patch.object(STORE, "STAGE_RESULTS_INDEX", root / "build/STAGE_RESULTS.json"), mock.patch.object(STORE, "stage_directory", return_value=root / "build/05-placement"):
                result = STORE.publish_stage_artifact(run, source, "placed.kicad_pcb")
            self.assertEqual(libraries(table, work), libraries(released / "fp-lib-table", released))
            self.assertEqual(table.read_bytes(), original)
            self.assertEqual((root / result["artifact"]).read_bytes(), source.read_bytes())

    def test_physical_review_requires_validated_inheritance_not_poses_alone(self):
        value = {"kind": "kicad_placement_immutability", "success": True, "candidate_sha256": "BOARD"}
        self.assertFalse(REVIEW.placement_evidence_matches(value, "BOARD"))
        with mock.patch.object(REVIEW, "validate_inheritance", return_value={"success": True}) as validate:
            self.assertTrue(REVIEW.placement_evidence_matches(value, "BOARD", Path("proof.json")))
            validate.assert_called_once_with(Path("proof.json"), "BOARD")
        with mock.patch.object(REVIEW, "validate_inheritance", side_effect=SystemExit("stale dependency")):
            with self.assertRaisesRegex(SystemExit, "stale dependency"):
                REVIEW.placement_evidence_matches(value, "BOARD", Path("proof.json"))

    @contextlib.contextmanager
    def reuse_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def write(name, value):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
                return path
            source = write("reference.kicad_pcb", "original placement")
            candidate = write("candidate.kicad_pcb", "same placement plus copper")
            baseline = write("baseline.kicad_pcb", "original unrouted comparison")
            contract = write("config/placement-semantics.json", {"reviewed": True})
            policy = {"weights": {"default": 1}, "checkpoint": {"critical_probe_groups": ["power"]}}
            old_policy = write("reference-policy.json", policy)
            write("config/placement-routability.json", policy)
            semantic = write("evidence/placement-semantics.json", {
                "kind": "kicad_placement_semantics", "success": True,
                "candidate_sha256": REUSE.sha256(source), "contract": str(contract), "contract_sha256": REUSE.sha256(contract),
            })
            score = write("evidence/routability.json", {
                "kind": "kicad_placement_routability", "success": True,
                "candidate": {"board_sha256": REUSE.sha256(source)},
                "comparison": {"improved_without_critical_regression": True},
            })
            inputs = {"config/placement-routability.json": REUSE.sha256(old_policy)}
            for name in ("check_kicad_placement_semantics.py", "check_kicad_placement_routability.py", "check_kicad_footprint_equivalence.py"):
                path = write("scripts/" + name, "checker")
                inputs["scripts/" + name] = REUSE.sha256(path)
            for name in ("pcbnew.py", "_pcbnew.pyd", "python.exe"):
                path = write("runtime/" + name, "runtime")
                inputs[str(path)] = REUSE.sha256(path)
            checkpoint = write("checkpoint.json", {
                "kind": "pcb_pipeline_summary", "success": True, "inputs_stable": True,
                "board_sha256": REUSE.sha256(source), "baseline_board_sha256": REUSE.sha256(baseline),
                "requested": {"baseline_board": str(baseline)}, "checkpoint_inputs": inputs,
                "evidence_hashes": {str(p): REUSE.sha256(p) for p in (semantic, score)},
            })
            with mock.patch.object(REUSE, "ROOT", root), mock.patch.object(REUSE, "native_layout_signature", return_value="SAME") as native:
                yield root, source, candidate, checkpoint, old_policy, native

    def test_inherits_exact_placement_without_rewriting_original_receipts(self):
        with self.reuse_fixture() as (root, source, candidate, checkpoint, policy, native):
            score = root / "evidence/routability.json"
            original = score.read_bytes()
            inherited = REUSE.inherit_checkpoint(source, candidate, checkpoint, policy)
            self.assertTrue(inherited["success"])
            self.assertEqual(native.call_count, 2)
            self.assertEqual(score.read_bytes(), original)

    def test_reuse_rejects_changed_pad_net_or_outline_even_with_same_poses(self):
        with self.reuse_fixture() as (_, source, candidate, checkpoint, policy, native):
            native.side_effect = ["ORIGINAL", "PAD_OR_NET_OR_OUTLINE_CHANGED"]
            with self.assertRaisesRegex(SystemExit, "Placement inputs changed"):
                REUSE.inherit_checkpoint(source, candidate, checkpoint, policy)

    def test_reuse_accepts_more_probe_coverage_but_rejects_metric_changes(self):
        with self.reuse_fixture() as (root, source, candidate, checkpoint, policy, _):
            current = root / "config/placement-routability.json"
            value = REUSE.load(current)
            value["checkpoint"]["critical_probe_groups"].append("clock")
            current.write_text(json.dumps(value))
            self.assertTrue(REUSE.inherit_checkpoint(source, candidate, checkpoint, policy)["success"])
            value["weights"]["default"] = 2
            current.write_text(json.dumps(value))
            with self.assertRaisesRegex(SystemExit, "metric policy changed"):
                REUSE.inherit_checkpoint(source, candidate, checkpoint, policy)

    def test_reuse_requires_original_policy_and_checker_hashes(self):
        for target in ("reference-policy.json", "scripts/check_kicad_placement_semantics.py", "runtime/_pcbnew.pyd", "baseline.kicad_pcb"):
            with self.subTest(target=target), self.reuse_fixture() as (root, source, candidate, checkpoint, policy, _):
                (root / target).write_text("changed")
                with self.assertRaisesRegex(SystemExit, "dependency changed"):
                    REUSE.inherit_checkpoint(source, candidate, checkpoint, policy)

    def test_reuse_rejects_failed_or_unstable_reference_checkpoint(self):
        for key in ("success", "inputs_stable"):
            with self.subTest(key=key), self.reuse_fixture() as (_, source, candidate, checkpoint, policy, _):
                value = REUSE.load(checkpoint); value[key] = False
                checkpoint.write_text(json.dumps(value))
                with self.assertRaisesRegex(SystemExit, "passing, stable checkpoint"):
                    REUSE.inherit_checkpoint(source, candidate, checkpoint, policy)

    def test_inheritance_consumer_rejects_stale_dependencies_or_wrong_evidence(self):
        with self.reuse_fixture() as (root, source, candidate, checkpoint, policy, _):
            inherited = REUSE.inherit_checkpoint(source, candidate, checkpoint, policy)
            proof = root / "inheritance.json"
            proof.write_text(json.dumps({"kind": "kicad_placement_immutability", "success": True,
                "input": str(source), "input_sha256": REUSE.sha256(source), "candidate": str(candidate),
                "candidate_sha256": REUSE.sha256(candidate), "evidence_inheritance": inherited}))
            candidate_hash = REUSE.sha256(candidate)
            self.assertTrue(REUSE.validate_inheritance(proof, candidate_hash)["success"])
            with self.assertRaisesRegex(SystemExit, "does not bind supplied"):
                REUSE.validate_inheritance(proof, candidate_hash, routability=root / "unrelated.json")
            for target in (candidate, source, root / "config/placement-semantics.json", root / "config/placement-routability.json"):
                original = target.read_bytes(); target.write_text("changed")
                with self.subTest(target=target.name), self.assertRaisesRegex(SystemExit, "dependency changed"):
                    REUSE.validate_inheritance(proof, candidate_hash)
                target.write_bytes(original)
            candidate.with_suffix(".kicad_dru").write_text("new unreviewed custom rules")
            with self.assertRaisesRegex(SystemExit, "dependency changed"):
                REUSE.validate_inheritance(proof, candidate_hash)

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
