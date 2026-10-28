from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import electrical_backend
import electrical_source_preflight as preflight
import build_electrical as builder


class SourcePreflightTests(unittest.TestCase):
    def fixture(self, root, body=None):
        (root / "src").mkdir()
        (root / "src/main.ato").write_text("original")
        (root / "config").mkdir()
        (root / "rules.json").write_text('{"approved":false}')
        script = root / "check.py"
        script.write_text(body or (
            'import json,sys\nfrom pathlib import Path\n'
            'Path(sys.argv[2]).write_text(json.dumps({"schema":1,"success":True,"issues":[]}))\n'
        ))
        backend = {"source_root": "src", "source_preflight": {
            "script": "check.py", "inputs": ["rules.json"], "timeout_seconds": 10,
        }}
        config = root / "config/electrical-backend.json"
        config.write_text(json.dumps(backend))
        status = {"source_sha256": electrical_backend.source_sha256(root / "src"),
                  "config_sha256": electrical_backend.sha256(config)}
        return backend, status

    def test_absent_declaration_is_process_and_artifact_free(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(preflight.subprocess, "run") as process:
            self.assertIsNone(preflight.run({}, Path(tmp), {}))
            self.assertIsNone(preflight.inputs({}, Path(tmp)))
            process.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_real_check_binds_inputs_preserves_adoption_and_freeze_rejects_changed_rule(self):
        with tempfile.TemporaryDirectory(prefix="pcb owner's project ") as tmp:
            root = Path(tmp).resolve()
            backend, status = self.fixture(root)
            pointer = root / "build/03-electrical/CURRENT.json"
            pointer.parent.mkdir(parents=True)
            pointer.write_text("original-adoption")
            result = preflight.run(backend, root, status)
            self.assertTrue(result["success"])
            build = {"source_preflight": result, "contract": status}
            path = preflight.require(build, backend, root)
            self.assertEqual(pointer.read_text(), "original-adoption")
            self.assertFalse((root / "build/CURRENT.json").exists())
            self.assertTrue(path.is_relative_to(root / "build/03-electrical/evidence"))
            self.assertEqual(json.loads(path.read_text())["inputs"], preflight.inputs(backend, root))
            (root / "rules.json").write_text("changed rule")
            with self.assertRaisesRegex(ValueError, "stale"):
                preflight.require(build, backend, root)
            with self.assertRaisesRegex(ValueError, "no passing"):
                preflight.require({"contract": status}, backend, root)

    def test_declared_input_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            backend, _ = self.fixture(root)
            for patch in [{"inputs": []}, {"inputs": ["../outside.json"]},
                          {"inputs": ["missing.json"]}, {"inputs": ["rules.json", "./rules.json"]},
                          {"script": "rules.json"}, {"timeout_seconds": 61},
                          {"timeout_seconds": True}, {"unexpected": 1}]:
                with self.subTest(patch=patch):
                    changed = {**backend, "source_preflight": {**backend["source_preflight"], **patch}}
                    with self.assertRaises(ValueError):
                        preflight.inputs(changed, root)

    def test_mutating_source_or_declared_inputs_is_rejected(self):
        for target in ["src/main.ato", "rules.json", "check.py", "config/electrical-backend.json"]:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                body = ('import json,sys\nfrom pathlib import Path\n'
                        f'Path({target!r}).write_text("mutated")\n'
                        'Path(sys.argv[2]).write_text(json.dumps({"schema":1,"success":True,"issues":[]}))\n')
                backend, status = self.fixture(root, body)
                result = preflight.run(backend, root, status)
                self.assertFalse(result["success"])
                self.assertIn("project_check_inputs_mutated", result["issues"])

    def test_nonzero_malformed_missing_and_oversized_results_are_rejected(self):
        bodies = [
            'import sys\nsys.exit(2)\n',
            'import sys\nfrom pathlib import Path\nPath(sys.argv[2]).write_text("[]")\n',
            'import sys\nfrom pathlib import Path\nPath(sys.argv[2]).write_text("x"*262145)\n',
            'import sys,json\nfrom pathlib import Path\nPath(sys.argv[2]).write_text(json.dumps({"schema":1,"success":1,"issues":[]}))\n',
        ]
        for body in bodies:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                backend, status = self.fixture(root, body)
                self.assertFalse(preflight.run(backend, root, status)["success"])

    def test_timeout_is_bounded_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            backend, status = self.fixture(root)
            with mock.patch.object(preflight.subprocess, "run", side_effect=preflight.subprocess.TimeoutExpired("check", 10)):
                result = preflight.run(backend, root, status)
            self.assertFalse(result["success"])
            self.assertIn("project_check_timeout", result["issues"])

    def test_plan_rejects_before_reuse_toolchain_or_compiler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            backend, status = self.fixture(root, 'raise SystemExit(3)\n')
            output = io.StringIO()
            with mock.patch.object(builder, "ROOT", root), \
                 mock.patch.object(builder, "read_config", return_value={}), \
                 mock.patch.object(builder, "selected_backend", return_value=("test", backend)), \
                 mock.patch.object(builder, "validate_backend", return_value=status), \
                 mock.patch.object(builder, "production_toolchain") as toolchain, \
                 mock.patch.object(builder, "find_matching_build") as reuse, \
                 mock.patch.object(sys, "argv", ["build", "--plan"]), contextlib.redirect_stdout(output):
                self.assertEqual(builder.main(), 1)
            toolchain.assert_not_called()
            reuse.assert_not_called()
            self.assertFalse(json.loads(output.getvalue())["compiler_invoked"])

    def test_rule_helper_and_checker_changes_invalidate_build_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            backend, status = self.fixture(root)
            backend["driver"] = "driver.py"
            (root / "driver.py").write_text("driver")
            (root / "config/pcb-flow.json").write_text('{"project":{"name":"fixture"}}')
            (root / "scripts").mkdir()
            for name in ["electrical_backend.py", "electrical_source_preflight.py", "electrical_build_identity.py", "source_references.py", "check_electrical_candidate_integrity.py",
                         "check_kicad_footprint_equivalence.py", "electrical_candidate_contract.py"]:
                (root / "scripts" / name).write_text("producer")
            relative = lambda p: p.name if not p.is_relative_to(root) else p.relative_to(root).as_posix()
            with mock.patch.object(builder, "ROOT", root), mock.patch.object(builder, "relative", side_effect=relative), \
                 mock.patch.object(builder, "workspace_path", side_effect=lambda value, _: root / value):
                previous = builder.build_input_manifest("test", backend, status, "default", None)["fingerprint"]
                for filename in ["rules.json", "check.py", "scripts/electrical_source_preflight.py"]:
                    path = root / filename
                    path.write_text(path.read_text() + " ")
                    current = builder.build_input_manifest("test", backend, status, "default", None)["fingerprint"]
                    self.assertNotEqual(previous, current)
                    previous = current


if __name__ == "__main__":
    unittest.main()
