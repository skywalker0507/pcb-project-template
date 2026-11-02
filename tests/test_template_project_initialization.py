from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import electrical_backend as backend
import electrical_freeze_contract as freeze
import init_project as initializer
import prepare_kicad_physical_candidate as physical
import run_template_checks as template_checks
from validate_requirements import problems as requirement_problems


class ProjectInitializationTests(unittest.TestCase):
    def fixture(self, root):
        (root / "config").mkdir()
        for source in (ROOT / "config").iterdir():
            if source.suffix in {".json", ".txt"}:
                shutil.copy2(source, root / "config" / source.name)
        flow = json.loads((root / "config/pcb-flow.json").read_text())
        flow["project"]["board"] = "elec/layout/reviewed/reviewed.kicad_pcb"
        (root / "config/pcb-flow.json").write_text(json.dumps(flow))
        initializer.ensure_stage_bootstrap(flow["project"]["name"])
        (root / "src").mkdir()
        (root / "src/main.ato").write_text("module App:\n    pass\n")
        board = root / flow["project"]["board"]
        board.parent.mkdir(parents=True)
        board.write_bytes(b"reviewed-board-fixture")
        return flow, board

    def test_reconfigure_preserves_identity_authority_and_progress(self):
        with tempfile.TemporaryDirectory(prefix="pcb owner's project ") as directory, mock.patch.object(initializer, "ROOT", Path(directory).resolve()):
            root = Path(directory).resolve()
            flow, board = self.fixture(root)
            paths = [*root.glob("config/*.json"), root / "handoff/stages/CURRENT_STAGE.json", board]
            before = {path: path.read_bytes() for path in paths}
            initializer.configure_project(None, "Ubuntu-24.04", True, True, 2, None)
            for path in paths:
                if path.name == "pcb-flow.json":
                    self.assertEqual(json.loads(path.read_bytes()), json.loads(before[path]))
                else:
                    self.assertEqual(path.read_bytes(), before[path], str(path))
            config = tomllib.loads((root / ".codex/config.toml").read_text())
            command = config["mcp_servers"]["atopile"]["args"][-1]
            self.assertIn("'\"'\"'", command)  # apostrophe is shell quoted inside valid TOML
            self.assertFalse((root / "config/project-initialization.json").exists())
            with self.assertRaisesRegex(SystemExit, "--new-project"):
                initializer.configure_project("different-project", "Ubuntu-24.04", True, False, 2, None)
            self.assertEqual(json.loads((root / "config/pcb-flow.json").read_text()), flow)

    def test_reconfigure_keeps_backend_and_mcp_distro_together_and_check_failure_fails(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(initializer, "ROOT", Path(directory).resolve()):
            root = Path(directory).resolve()
            self.fixture(root)
            initializer.configure_project(None, "My-Distro", True, False, 2, None)
            initializer.configure_project(None, None, True, False, 2, None)
            config = tomllib.loads((root / ".codex/config.toml").read_text())
            selected = json.loads((root / "config/electrical-backend.json").read_text())
            self.assertEqual(selected["backends"]["atopile"]["distro"], "My-Distro")
            self.assertEqual(config["mcp_servers"]["atopile"]["args"][1], "My-Distro")
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", ["init", "--check", "--no-konnect", "--force-config"]), mock.patch.object(initializer.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)), contextlib.redirect_stdout(stdout):
                self.assertEqual(initializer.main(), 7)
            self.assertFalse(json.loads(stdout.getvalue())["success"])

    def test_new_project_archives_reviews_clears_adoption_and_preserves_cad(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(initializer, "ROOT", Path(directory).resolve()):
            root = Path(directory).resolve()
            _, board = self.fixture(root)
            authority = {path.name: path.read_bytes() for path in root.glob("config/*.json")}
            pointer = root / "build/03-electrical/CURRENT.json"
            pointer.parent.mkdir(parents=True)
            pointer.write_text('{"success":true}')
            release = root / "build/04-electrical-freeze/release/historical/electrical-freeze.json"
            release.parent.mkdir(parents=True)
            release.write_text('{"historical":true}')
            initializer.configure_project("new-board", "Ubuntu-24.04", True, False, 4, "atopile", False)
            scope = json.loads((root / "config/project-initialization.json").read_text())
            archive = root / scope["archive"]
            for name in ("requirements.json", "product-decisions.json", "selected-parts.json", "footprint-provenance.json", "design-data.json", "jlcpcb-parts.json"):
                self.assertEqual((archive / "config" / name).read_bytes(), authority[name])
                draft = json.loads((root / "config" / name).read_text())
                self.assertEqual((draft["project"], draft["status"]), ("new-board", "draft"))
            self.assertFalse(pointer.exists())
            self.assertEqual((archive / pointer.relative_to(root)).read_text(), '{"success":true}')
            self.assertTrue(release.exists())
            self.assertEqual(board.read_bytes(), b"reviewed-board-fixture")
            self.assertEqual(json.loads((root / "handoff/stages/CURRENT_STAGE.json").read_text())["stage"], "requirements")
            requirements = json.loads((root / "config/requirements.json").read_text())
            self.assertEqual(requirement_problems(requirements), [])
            self.assertTrue(requirement_problems(requirements, require_reviewed=True))
            with mock.patch.object(freeze, "ROOT", root):
                release.write_text(json.dumps({"schema": 2, "kind": "electrical_freeze", "success": True, "freeze_pass": True}))
                with self.assertRaisesRegex(SystemExit, "different project initialization"):
                    freeze.load_release_freeze(release, release.parent.parent)

    def test_new_project_clears_inherited_source_preflights_after_archiving(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(initializer, "ROOT", Path(directory).resolve()):
            root = Path(directory).resolve()
            self.fixture(root)
            path = root / "config/electrical-backend.json"
            config = json.loads(path.read_text())
            for item in config["backends"].values():
                item["source_preflight"] = {"script": "old-project.py", "inputs": ["old-rules.json"]}
            path.write_text(json.dumps(config))
            inherited = path.read_bytes()
            initializer.configure_project(None, None, True, False, 4, "atopile", True)
            self.assertEqual(path.read_bytes(), inherited)
            initializer.configure_project("fresh", None, True, False, 4, "atopile", False)
            current = json.loads(path.read_text())
            self.assertTrue(all("source_preflight" not in item for item in current["backends"].values()))
            scope = json.loads((root / "config/project-initialization.json").read_text())
            self.assertEqual((root / scope["archive"] / "config/electrical-backend.json").read_bytes(), inherited)

    def test_new_project_records_starting_runtime_but_reconfiguration_preserves_inventory(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(initializer, "ROOT", Path(directory).resolve()):
            root = Path(directory).resolve()
            self.fixture(root)
            (root / "scripts").mkdir()
            (root / "scripts/shared.py").write_text("# initial shared implementation\n")
            shutil.copytree(ROOT / "template-engine", root / "template-engine", ignore=shutil.ignore_patterns("__pycache__"))
            inventory = root / initializer.RUNTIME_LOCK
            inventory.write_text('{"historical":"inventory must be archived before reset"}')
            old = inventory.read_bytes()
            initializer.configure_project(None, None, True, False, 4, "atopile", True)
            self.assertEqual(inventory.read_bytes(), old)
            initializer.configure_project("fresh", None, True, False, 2, "atopile", False)
            scope = json.loads((root / "config/project-initialization.json").read_text())
            self.assertEqual((root / scope["archive"] / initializer.RUNTIME_LOCK).read_bytes(), old)
            lock = json.loads(inventory.read_text())
            self.assertEqual(lock, initializer.runtime_inventory(root))
            self.assertIn("scripts/shared.py", lock["files"])

    def test_new_project_isolates_physical_manufacturing_and_routing_authority(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(initializer, "ROOT", Path(directory).resolve()):
            root = Path(directory).resolve()
            _, old_board = self.fixture(root)
            before = {p.name: p.read_bytes() for p in (root / "config").iterdir()}
            # Old indexes and reports may remain on disk, but cannot be selected.
            for name in ("build/CURRENT.json", "build/03-electrical/BUILD.json", "build/07-release/INPUTS.json"):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{"old":true}')
            initializer.configure_project("fresh", None, True, False, 2, "atopile", False)
            load = lambda name: json.loads((root / "config" / name).read_text(encoding="utf-8"))
            archive = root / load("project-initialization.json")["archive"]
            for name, content in before.items():
                self.assertEqual((archive / "config" / name).read_bytes(), content)
            for name in ("placement-semantics.json", "placement-routability.json", "manufacturing-profile.json", "manufacturing-rules.json", "routing-planner.json"):
                self.assertEqual(load(name)["status"], "draft")
            self.assertFalse(load("manufacturing-profile.json")["selected"])
            self.assertEqual(load("placement-semantics.json")["roles"], [])
            self.assertEqual(load("pcb-release-baseline.json")["allowed_violation_items"], {})
            self.assertEqual(set(load("kicad-routing-tools.json")["profiles"]), {"outer_low_risk"})
            self.assertIsNone(load("kicad-routing-tools.json")["profiles"]["outer_low_risk"]["routing"]["track_width"])
            flow = load("pcb-flow.json")
            self.assertFalse(flow["audit"]["require_in1_ground"])
            self.assertNotEqual(root / flow["project"]["board"], old_board)
            self.assertNotIn("validate_power_component_review.py", str(flow["checks"]))
            for path in flow["manufacturing"]["gate_receipts"].values():
                self.assertFalse((root / path).exists())
            for name in ("usb-esd-placement-evidence.json", "buck-bootstrap-placement-evidence.json", "jlcpcb-impedance-evidence.json", "krt-fab-overrides.txt"):
                self.assertFalse((root / "config" / name).exists())
            for name in ("build/CURRENT.json", "build/03-electrical/BUILD.json", "build/07-release/INPUTS.json"):
                self.assertFalse((root / name).exists())
            self.assertEqual(old_board.read_bytes(), b"reviewed-board-fixture")

    def test_new_project_archives_reference_manifest_without_removing_reference_files(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(initializer, "ROOT", Path(directory).resolve()):
            root=Path(directory).resolve();self.fixture(root)
            manifest=root/'config/source-references.json';manifest.write_text('{"schema":1,"files":[]}')
            reference=root/'references/vendor.pdf';reference.parent.mkdir();reference.write_bytes(b'original reference')
            initializer.configure_project(None,None,True,False,4,'atopile',True)
            self.assertTrue(manifest.exists())
            initializer.configure_project('fresh',None,True,False,4,'atopile',False)
            scope=json.loads((root/'config/project-initialization.json').read_text())
            self.assertEqual((root/scope['archive']/'config/source-references.json').read_text(),'{"schema":1,"files":[]}')
            self.assertFalse(manifest.exists())
            self.assertEqual(reference.read_bytes(),b'original reference')

    def test_nested_resources_and_stale_physical_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "hardware/control"
            library = source / "resources/deep/Part.pretty"
            library.mkdir(parents=True)
            (source / "main.ato").write_text("module App:\n    pass\n")
            (library / "Part.kicad_mod").write_text("fixture")
            config = {"active": "atopile", "backends": {"atopile": {"source_root": "hardware/control", "entry": "main.ato:App"}}}
            selected = config["backends"]["atopile"]
            self.assertEqual(backend.source_entry(selected, root=root), (source, source / "main.ato", "App"))
            with self.assertRaisesRegex(ValueError, "inside"):
                backend.source_entry(selected, "../outside.ato:App", root=root)
            project = root / "elec/layout/candidate"
            project.mkdir(parents=True)
            with mock.patch.object(physical, "ROOT", root), mock.patch.object(physical, "read_electrical", return_value=config), mock.patch.object(physical, "selected_electrical", return_value=("atopile", selected)):
                table, created = physical.ensure_reviewed_fp_table(project)
                self.assertTrue(created)
                self.assertEqual(physical.libraries(table, project), {"Part": library})
                table.write_text('(fp_lib_table (lib (name "Part")(type "KiCad")(uri "${KIPRJMOD}/obsolete")))')
                before = table.read_bytes()
                with self.assertRaisesRegex(SystemExit, "stale"):
                    physical.ensure_reviewed_fp_table(project)
                self.assertEqual(before, table.read_bytes())
            duplicate = source / "other/Part.pretty"
            duplicate.mkdir(parents=True)
            (duplicate / "Part.kicad_mod").write_text("fixture")
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                backend.footprint_libraries(source)

    def test_physical_start_uses_frozen_resources_and_stale_table_leaves_no_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            freeze_path = root / "build/04-electrical-freeze/release/fixture/electrical-freeze.json"
            source = freeze_path.parent / "provenance/source"
            library = source / "deep/Frozen.pretty"
            library.mkdir(parents=True)
            (library / "One.kicad_mod").write_text("frozen fixture")
            project = root / "elec/layout/board"
            project.mkdir(parents=True)
            table, _ = physical.ensure_reviewed_fp_table(project, source)
            self.assertEqual(physical.libraries(table, project), {"Frozen": library})
            table.write_text('(fp_lib_table (lib (name "Frozen")(type "KiCad")(uri "${KIPRJMOD}/obsolete-live-source")))')
            before = table.read_bytes()
            output = project / "board.kicad_pcb"
            (root / "config").mkdir()
            (root / "config/requirements.json").write_text(json.dumps({
                "schema": 2, "status": "reviewed", "interfaces": [{"name": "fixture"}],
                "rails": [], "connectors": [], "constraints": [], "mechanical": {},
                "unknowns": [], "critical_nets": [], "placement_regions": [],
            }), encoding="utf-8")
            args = ["physical-start", "--electrical-freeze", str(freeze_path), "--output", str(output)]
            with mock.patch.object(physical, "ROOT", root), mock.patch.object(sys, "argv", args), mock.patch.object(physical, "current_stage", return_value={"stage": "physical"}), mock.patch.object(physical, "read_config", return_value={}), mock.patch.object(physical, "selected_backend", return_value=("kicad", {"role": "primary"})), mock.patch.object(physical, "load_release_freeze", return_value=({"source_sha256": backend.source_sha256(source)}, {})), mock.patch.object(physical, "create_run") as create_run:
                with self.assertRaisesRegex(SystemExit, "stale"):
                    physical.main()
                create_run.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".kicad_pro").exists())
            self.assertEqual(before, table.read_bytes())


class FrameworkUpdateTests(unittest.TestCase):
    def fixture(self, directory):
        base = Path(directory).resolve()
        source, target = base / "source", base / "project"
        for root in (source, target):
            (root / "scripts").mkdir(parents=True)
            (root / "config").mkdir()
            for name in ("pcbctl.py", "session_boot.py", "shared.py"):
                (root / "scripts" / name).write_text(f"# reviewed {name}\n")
            (root / "config/pcb-flow.json").write_text('{"project":"preserve me"}')
            (root / "config/template-engine.json").write_text('{"schema":1,"source":{"type":"installed"},"version":"1"}')
        (target / "scripts/project_only.py").write_text("# custom checker\n")
        (target / "board.kicad_pcb").write_bytes(b"CAD fixture must remain unchanged")
        validation = source / "build/checks.json"
        self.validate(source, validation)
        return source, target, validation

    def validate(self, source, validation):
        validation.parent.mkdir(parents=True, exist_ok=True)
        validation.write_text(json.dumps({
            "kind": "template_regression_checks", "success": True,
            "validation_inputs": initializer.framework_validation_inputs(source),
            "checks": [{"name": name, "success": True} for name in
                       ("unit-tests", "template-engine", "backend-architecture", "tool-entrypoints")],
        }))

    def seed(self, source, target, validation):
        plan = initializer.framework_sync_plan(source, target, validation)
        self.assertTrue(plan["validation_current"])
        initializer.apply_framework_sync(plan, plan["plan_sha256"])

    def tree(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_seed_is_target_readonly_until_apply_and_preserves_project_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            before = self.tree(target)
            with mock.patch.object(initializer, "ROOT", source), contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(initializer.sync_framework_cli(target, validation, False, None), 0)
            preview = json.loads(stdout.getvalue())
            self.assertEqual(preview["file_count"], 3)
            self.assertEqual(preview["update_count"], 0)
            self.assertEqual(before, self.tree(target))
            self.assertTrue(Path(preview["details"]).is_file())
            plan = initializer.framework_sync_plan(source, target, validation)
            receipt = initializer.apply_framework_sync(plan, plan["plan_sha256"])
            for name, payload in before.items():
                self.assertEqual((target / name).read_bytes(), payload)
            self.assertTrue((target / initializer.RUNTIME_LOCK).is_file())
            self.assertEqual(receipt["updated"], 0)
            same = initializer.framework_sync_plan(source, target, validation)
            self.assertFalse(same["lock_update"])
            self.assertEqual(initializer.apply_framework_sync(same, same["plan_sha256"])["updated"], 0)

    def test_three_way_updates_and_additions_keep_verified_old_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            self.seed(source, target, validation)
            original = (target / "scripts/shared.py").read_bytes()
            (source / "scripts/shared.py").write_text("# fixed shared implementation\n")
            (source / "scripts/new.py").write_text("# new shared entry\n")
            self.validate(source, validation)
            plan = initializer.framework_sync_plan(source, target, validation)
            actions = {p["path"]: p["action"] for p in plan["files"]}
            self.assertEqual(actions["scripts/shared.py"], "update")
            self.assertEqual(actions["scripts/new.py"], "add")
            result = initializer.apply_framework_sync(plan, plan["plan_sha256"])
            self.assertEqual(result["updated"], 2)
            receipt = json.loads(Path(result["receipt"]).read_text())
            self.assertEqual((target / receipt["backup"] / "original/scripts/shared.py").read_bytes(), original)
            self.assertEqual((target / "scripts/shared.py").read_bytes(), (source / "scripts/shared.py").read_bytes())
            self.assertEqual((target / "scripts/project_only.py").read_text(), "# custom checker\n")

    def test_missing_baseline_local_change_deletion_and_upstream_removal_block_whole_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            shared = target / "scripts/shared.py"
            shared.write_text("# local unreviewed difference\n")
            plan = initializer.framework_sync_plan(source, target, validation)
            self.assertIn("conflict_untracked_baseline", [p["action"] for p in plan["files"]])
            before = self.tree(target)
            with self.assertRaisesRegex(ValueError, "blocked"):
                initializer.apply_framework_sync(plan, plan["plan_sha256"])
            self.assertEqual(before, self.tree(target))
            shared.write_bytes((source / "scripts/shared.py").read_bytes())
            self.seed(source, target, validation)
            (source / "scripts/pcbctl.py").write_text("# eligible shared change\n")
            for alteration in ("modify", "delete", "remove_upstream"):
                with self.subTest(alteration=alteration):
                    shared.write_bytes((source / "scripts/shared.py").read_bytes())
                    if alteration == "modify":
                        shared.write_text("# local edit\n")
                    elif alteration == "delete":
                        shared.unlink()
                    else:
                        (source / "scripts/shared.py").unlink()
                    self.validate(source, validation)
                    plan = initializer.framework_sync_plan(source, target, validation)
                    before = self.tree(target)
                    with self.assertRaisesRegex(ValueError, "blocked"):
                        initializer.apply_framework_sync(plan, plan["plan_sha256"])
                    self.assertEqual(before, self.tree(target))

    def test_stale_preview_source_or_target_and_test_receipt_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            self.seed(source, target, validation)
            (source / "scripts/shared.py").write_text("# new version\n")
            self.validate(source, validation)
            plan = initializer.framework_sync_plan(source, target, validation)
            (target / "scripts/shared.py").write_text("# concurrent local edit\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                initializer.apply_framework_sync(plan, plan["plan_sha256"])
            (target / "scripts/shared.py").write_text("# reviewed shared.py\n")
            (source / "scripts/shared.py").write_text("# changed after preview\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                initializer.apply_framework_sync(plan, plan["plan_sha256"])
            plan = initializer.framework_sync_plan(source, target, validation)
            self.assertFalse(plan["validation_current"])
            with self.assertRaisesRegex(ValueError, "blocked"):
                initializer.apply_framework_sync(plan, plan["plan_sha256"])
            self.validate(source, validation)
            proof = json.loads(validation.read_text()); proof["checks"] = proof["checks"][1:]
            validation.write_text(json.dumps(proof))
            self.assertFalse(initializer.framework_sync_plan(source, target, validation)["validation_current"])

    def test_engine_mismatch_and_malformed_lock_do_not_mutate_project(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            (target / "config/template-engine.json").write_text('{"source":{"type":"installed"},"version":"2"}')
            plan = initializer.framework_sync_plan(source, target, validation)
            self.assertFalse(plan["engine_compatible"])
            before = self.tree(target)
            with self.assertRaisesRegex(ValueError, "blocked"):
                initializer.apply_framework_sync(plan, plan["plan_sha256"])
            self.assertEqual(before, self.tree(target))
            (target / initializer.RUNTIME_LOCK).write_text('{"schema":1,"files":{"../escape.py":"A"}}')
            with self.assertRaisesRegex(ValueError, "runtime lock"):
                initializer.framework_sync_plan(source, target, validation)
            with self.assertRaisesRegex(ValueError, "Invalid managed"):
                initializer.safe_runtime_path(target, "scripts/../escape.py")

    def test_linked_script_and_artifact_paths_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            original = Path.is_junction
            with mock.patch.object(Path, "is_junction", lambda p: p == target / "scripts" or original(p)):
                with self.assertRaisesRegex(ValueError, "linked"):
                    initializer.framework_sync_plan(source, target, validation)

    def test_partial_write_is_rolled_back_with_original_lock_and_no_success_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            self.seed(source, target, validation)
            for name in ("pcbctl.py", "shared.py"):
                (source / "scripts" / name).write_text("# updated version\n")
            self.validate(source, validation)
            plan = initializer.framework_sync_plan(source, target, validation)
            before = {p: data for p, data in self.tree(target).items() if not p.startswith("tmp/")}
            replace = initializer.os.replace
            count = 0
            def fail_once(src, dst):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("simulated second write failure")
                return replace(src, dst)
            with mock.patch.object(initializer.os, "replace", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "second write"):
                    initializer.apply_framework_sync(plan, plan["plan_sha256"])
            self.assertEqual(before, {p: data for p, data in self.tree(target).items() if not p.startswith("tmp/")})

    def test_cli_never_reconfigures_and_template_checks_reject_midrun_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, validation = self.fixture(directory)
            with mock.patch.object(initializer, "ROOT", source), mock.patch.object(initializer, "configure_project") as configure:
                argv = ["init", "--sync-framework", str(target), "--plan", "--validation-receipt", str(validation)]
                with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(initializer.main(), 0)
                configure.assert_not_called()
            output = source / "build/new-validation.json"
            def check(name, command, log_dir):
                (source / "scripts/shared.py").write_text("# changed during validation\n")
                return {"name": name, "success": True}
            with mock.patch.object(template_checks, "ROOT", source), mock.patch.object(template_checks, "run_check", side_effect=check):
                with mock.patch.object(sys, "argv", ["checks", "--output", str(output)]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(template_checks.main(), 1)
            receipt = json.loads(output.read_text())
            self.assertFalse(receipt["inputs_unchanged"])
            self.assertIn("template_inputs_changed_during_validation", receipt["failed_checks"])


if __name__ == "__main__":
    unittest.main()
