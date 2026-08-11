from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/start_konnect_mcp.py"
SPEC = importlib.util.spec_from_file_location("start_konnect_mcp", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class StartKonnectTests(unittest.TestCase):
    def test_suppresses_implicit_claude_install_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "konnect.exe"
            executable.write_bytes(b"placeholder")
            argv = [str(SCRIPT), str(executable), "--config", "konnect.toml"]

            with mock.patch.object(Path, "home", return_value=root / "home"), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(MODULE.subprocess, "call", return_value=0) as call:
                self.assertEqual(MODULE.main(), 0)

            marker = root / "home/.konnect/.installed"
            self.assertEqual(marker.read_text(encoding="utf-8"), "codex-mcp\n")
            call.assert_called_once_with([
                str(executable.resolve()), "--config", "konnect.toml",
            ])


if __name__ == "__main__":
    unittest.main()
