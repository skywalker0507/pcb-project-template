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


class ProjectInitializationTests(unittest.TestCase):
    def fixture(self, root):
        (root / "config").mkdir()
        for name in ("pcb-flow.json", "board-stack.json", "kicad-routing-tools.json", "electrical-backend.json",
                     "requirements.json", "product-decisions.json", "selected-parts.json", "footprint-provenance.json",
                     "design-data.json", "jlcpcb-parts.json"):
            shutil.copy2(ROOT / "config" / name, root / "config" / name)
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
            with mock.patch.object(freeze, "ROOT", root):
                release.write_text(json.dumps({"schema": 2, "kind": "electrical_freeze", "success": True, "freeze_pass": True}))
                with self.assertRaisesRegex(SystemExit, "different project initialization"):
                    freeze.load_release_freeze(release, release.parent.parent)

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
            args = ["physical-start", "--electrical-freeze", str(freeze_path), "--output", str(output)]
            with mock.patch.object(physical, "ROOT", root), mock.patch.object(sys, "argv", args), mock.patch.object(physical, "current_stage", return_value={"stage": "physical"}), mock.patch.object(physical, "read_config", return_value={}), mock.patch.object(physical, "selected_backend", return_value=("kicad", {"role": "primary"})), mock.patch.object(physical, "load_release_freeze", return_value=({"source_sha256": backend.source_sha256(source)}, {})), mock.patch.object(physical, "create_run") as create_run:
                with self.assertRaisesRegex(SystemExit, "stale"):
                    physical.main()
                create_run.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".kicad_pro").exists())
            self.assertEqual(before, table.read_bytes())


if __name__ == "__main__":
    unittest.main()
