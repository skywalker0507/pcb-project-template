from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_open_source_ready.py"
SPEC = importlib.util.spec_from_file_location("check_open_source_ready", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class OpenSourceReadyTests(unittest.TestCase):
    def test_regular_source_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            self.assertEqual(MODULE.scan_paths(root, ["README.md"]), [])
            (root / ".env.example").write_text("API_URL=https://example.com\\n", encoding="utf-8")
            self.assertEqual(MODULE.scan_paths(root, [".env.example"]), [])

    def test_secret_filenames_and_generated_state_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(MODULE.scan_paths(root, [".env", "build/private.log", ".codex/config.toml"]))
            self.assertTrue(MODULE.scan_paths(root, ["handoff/stages/CURRENT_STAGE.json"]))

    def test_json_validation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.json").write_text("{invalid", encoding="utf-8")
            self.assertTrue(any("invalid JSON" in issue for issue in MODULE.scan_paths(root, ["bad.json"])))

    def test_private_key_header_is_detected_without_echoing_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs.md").write_text("-----BEGIN " + "PRIVATE KEY-----\ncontents\n", encoding="utf-8")
            failures = MODULE.scan_paths(root, ["docs.md"])
            self.assertTrue(failures)
            self.assertNotIn("contents", str(failures))


if __name__ == "__main__":
    unittest.main()
