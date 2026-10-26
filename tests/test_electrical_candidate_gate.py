from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_atopile_isolated as isolated
import build_electrical as electrical
from check_kicad_footprint_equivalence import KICAD_PYTHON


class ElectricalCandidateGateTests(unittest.TestCase):
    @unittest.skipUnless(KICAD_PYTHON.is_file(), "Native KiCad Python unavailable")
    def test_native_resource_gate_rejects_export_regressions(self):
        process = subprocess.run([str(KICAD_PYTHON), str(ROOT / "tests/fixtures/electrical_integrity_fixture.py")], capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, (process.stdout + process.stderr)[-3000:])
        result = json.loads(process.stdout)
        positive = result.pop("translated_rotated")
        self.assertTrue(positive["success"], positive)
        flipped = result.pop("flipped")
        self.assertTrue(flipped["success"], flipped)
        self.assertTrue(result)
        self.assertTrue(all(value["success"] is False for value in result.values()), result)

    def test_isolated_compile_restores_board_and_stale_library_table(self):
        for outcome in (0, 1, "capture_failure"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory(prefix="pcb project ") as directory:
                root = Path(directory).resolve()
                source = root / "hardware/control"
                library = source / "resources/deep/Part.pretty"
                library.mkdir(parents=True)
                (source / "main.ato").write_text("module App:\n    pass\n")
                (library / "One.kicad_mod").write_text("fixture resource")
                config = root / "config/electrical-backend.json"
                config.parent.mkdir()
                config.write_text(json.dumps({"schema": 1, "active": "atopile", "backends": {"atopile": {"driver": "unused", "source_root": "hardware/control", "entry": "main.ato:App", "runtime": "wsl-ato", "platform": "wsl-native", "status": "primary"}}}))
                board = root / "elec/layout/default/default.kicad_pcb"
                board.parent.mkdir(parents=True)
                board.write_bytes(b"original reviewed board")
                table = board.parent / "fp-lib-table"
                table.write_bytes(b"original stale table retained for physical review")
                output, receipt = root / "build/candidate", root / "build/receipt.json"
                observed = []
                def launch(command, **kwargs):
                    observed.append(table.read_text())
                    board.write_bytes(b"compiler generated board")
                    return mock.Mock(stdout=iter(["CODEX_ATO_VERSION=test\n", "build log\n"]), wait=lambda: 1 if outcome == 1 else 0)
                validation = subprocess.CompletedProcess([], 0, json.dumps({"success": True}))
                args = ["driver", "--output", str(output), "--receipt", str(receipt)]
                with mock.patch.object(isolated, "ROOT", root), mock.patch.object(sys, "argv", args), mock.patch.object(isolated, "windows_to_wsl", return_value="/mnt/c/pcb owner's project"), mock.patch.object(isolated.subprocess, "run", return_value=validation), mock.patch.object(isolated.subprocess, "Popen", side_effect=launch), contextlib.redirect_stdout(io.StringIO()):
                    if outcome == "capture_failure":
                        with mock.patch.object(isolated.shutil, "copy2", side_effect=OSError("capture failed")), self.assertRaises(OSError):
                            isolated.main()
                    else:
                        self.assertEqual(isolated.main(), outcome)
                self.assertEqual(board.read_bytes(), b"original reviewed board")
                self.assertEqual(table.read_bytes(), b"original stale table retained for physical review")
                self.assertIn("hardware/control/resources/deep/Part.pretty", observed[0])
                self.assertFalse(list(board.parent.glob("*.bak")))
                if outcome == 0:
                    self.assertEqual((output / "candidate.kicad_pcb").read_bytes(), b"compiler generated board")
                    self.assertTrue((output / "fp-lib-table").is_file())

    def test_compiler_success_cannot_bypass_failed_candidate_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output, receipt = root / "build/generated", root / "build/evidence/build.json"
            output.mkdir(parents=True)
            receipt.parent.mkdir(parents=True)
            candidate = output / "candidate.kicad_pcb"
            candidate.write_bytes(b"candidate fixture")
            selected = {"driver": "scripts/driver.py", "source_root": "hardware", "entry": "main.ato:App"}
            status = {"source_sha256": "SOURCE", "config_sha256": "CONFIG"}
            def run(command, **kwargs):
                if "--source-root" in command:
                    audit = Path(command[command.index("--output")+1])
                    audit.write_text(json.dumps({"kind": "electrical_candidate_integrity", "success": False, "failed_refs": ["J2"]}))
                    return subprocess.CompletedProcess(command, 1, "audit rejected", "")
                receipt.write_text(json.dumps({"success": True, "backend": "atopile", "candidate_board": str(candidate)}))
                return subprocess.CompletedProcess(command, 0, "compile succeeded", "")
            args = ["build", "--output", str(output), "--receipt", str(receipt)]
            with mock.patch.object(electrical, "ROOT", root), mock.patch.object(sys, "argv", args), mock.patch.object(electrical, "read_config", return_value={}), mock.patch.object(electrical, "selected_backend", return_value=("atopile", selected)), mock.patch.object(electrical, "validate_backend", return_value=status), mock.patch.object(electrical, "production_toolchain", return_value=None), mock.patch.object(electrical, "build_input_manifest", return_value={"fingerprint": "F"}), mock.patch.object(electrical, "find_matching_build", return_value=None), mock.patch.object(electrical, "workspace_path", side_effect=lambda value, label: root / value), mock.patch.object(electrical, "relative", side_effect=lambda path: path.relative_to(root).as_posix()), mock.patch.object(electrical.subprocess, "run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(electrical.main(), 1)
            payload = json.loads(receipt.read_text())
            self.assertFalse(payload["success"])
            self.assertEqual(payload["failure"]["class"], "electrical_candidate_integrity_failed")

    def test_root_net_conflict_survives_rich_summary(self):
        failure = isolated.classify_failure("Multiple conflicting required net names: {'GND', 'PRESENT_N'}\nlocation main.ato:42\n" + "padding\n"*25 + "Build failed\nBuild took 2s")
        self.assertEqual(failure["class"], "electrical_net_name_conflict")
        self.assertIn("GND", " ".join(failure["evidence"]))
        self.assertIn("main.ato:42", " ".join(failure["evidence"]))


if __name__ == "__main__":
    unittest.main()
