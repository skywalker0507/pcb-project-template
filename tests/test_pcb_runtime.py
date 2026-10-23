from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pcb_runtime as runtime
import init_project
import install_linux_kicad as installer


class HostRuntimeTests(unittest.TestCase):
    def test_linux_legacy_defaults_resolve_without_windows_or_wsl(self):
        with mock.patch.object(runtime, "WINDOWS", False), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runtime.shutil, "which", return_value=None):
            self.assertEqual(runtime.kicad_cli("C:/Program Files/KiCad/10.0/bin/kicad-cli.exe").name, "kicad-cli")
            self.assertEqual(runtime.routing_python(ROOT, ".venv-routingtools/Scripts/python.exe"), ROOT / ".venv-routingtools/bin/python")
            self.assertNotIn("wsl.exe", runtime.atopile_command(["--version"]))

    def test_windows_retains_bundled_kicad_and_wsl(self):
        with mock.patch.object(runtime, "WINDOWS", True), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runtime.kicad_python().name, "python.exe")
            self.assertEqual(runtime.kicad_cli().name, "kicad-cli.exe")
            self.assertEqual(runtime.venv_python(Path("venv")), Path("venv/Scripts/python.exe"))
            argv = runtime.atopile_command(["build", "--build", "name with spaces"])
            self.assertEqual(argv[0], "wsl.exe")
            self.assertIn("'name with spaces'", argv[-1])

    def test_explicit_tool_override_is_never_silently_replaced(self):
        with mock.patch.dict(os.environ, {"PCB_KICAD_CLI": "/missing/custom-cli", "PCB_KICAD_PYTHON": "/custom/python", "PCB_KRT_PYTHON": "/custom/krt"}, clear=True):
            self.assertEqual(runtime.kicad_cli(), Path("/missing/custom-cli"))
            self.assertEqual(runtime.kicad_python(), Path("/custom/python"))
            self.assertEqual(runtime.routing_python(), Path("/custom/krt"))

    def test_native_shell_preserves_spaces_quotes_and_environment(self):
        if os.name == "nt":
            self.skipTest("native POSIX shell integration")
        with tempfile.TemporaryDirectory(prefix="pcb's project ") as folder:
            with mock.patch.object(runtime, "WINDOWS", False), mock.patch.dict(os.environ, {"HTTP_PROXY": "http://example.invalid"}, clear=True):
                result = subprocess.run(runtime.atopile_shell("printf '%s' \"$HTTP_PROXY\"", "unused", Path(folder)), cwd=folder, env=runtime.atopile_environment(), capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, "http://example.invalid")

    def test_cloud_uses_supported_spawn_mode_and_keeps_proxy(self):
        with mock.patch.object(runtime, "WINDOWS", False), mock.patch.dict(os.environ, {"HTTPS_PROXY": "https://proxy.invalid"}, clear=True):
            env = runtime.atopile_environment()
        self.assertEqual(env, {"HTTPS_PROXY": "https://proxy.invalid", "ATO_MP_START_METHOD": "spawn"})

    def test_reexec_guard_fails_instead_of_starting_an_infinite_loop(self):
        real_import = __import__
        def import_without_pcbnew(name, *args, **kwargs):
            if name == "pcbnew":
                raise ImportError("test: no bindings")
            return real_import(name, *args, **kwargs)
        script = str(Path(sys.argv[0]).resolve())
        with mock.patch("builtins.__import__", side_effect=import_without_pcbnew), mock.patch.dict(os.environ, {"PCB_KICAD_REEXEC": script}, clear=True), mock.patch.object(runtime.subprocess, "call") as child:
            with self.assertRaisesRegex(SystemExit, "pcbnew is unavailable"):
                runtime.ensure_kicad_python()
            child.assert_not_called()

    def test_wsl_translation_does_not_require_a_windows_host(self):
        self.assertEqual(runtime.windows_to_wsl(Path(r"C:\PCB Project\test")), "/mnt/c/PCB Project/test")
        with self.assertRaises(SystemExit):
            runtime.windows_to_wsl(Path("relative/project"))

    def test_linux_mcp_config_handles_quoted_paths_and_preserves_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "board's project"
            (root / "config").mkdir(parents=True)
            for name in ("pcb-flow.json", "electrical-backend.json"):
                shutil.copy2(ROOT / "config" / name, root / "config" / name)
            stage = root / "handoff/stages/CURRENT_STAGE.json"
            stage.parent.mkdir(parents=True)
            stage.write_text('{"stage":"release","status":"blocked"}')
            before = stage.read_bytes()
            with mock.patch.object(init_project, "ROOT", root), mock.patch.object(init_project, "WINDOWS", False):
                init_project.configure_project("test", "unused", False, False, 4, "atopile", preserve_existing=True)
            config = tomllib.loads((root / ".codex/config.toml").read_text())
            self.assertNotIn("wsl", config["mcp_servers"]["atopile"]["command"])
            self.assertEqual(config["mcp_servers"]["atopile"]["cwd"], str(root))
            self.assertEqual(stage.read_bytes(), before)

    def test_portable_package_hashes_use_repository_names_without_epoch(self):
        hashes = installer.package_hashes("Package: xauth\nVersion: 1:1.1\nFilename: pool/xauth_1.1_amd64.deb\nSHA256: abc\n")
        self.assertEqual(hashes, {"xauth_1.1_amd64.deb": "abc"})


if __name__ == "__main__":
    unittest.main()
