from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pcb_init_project", ROOT / "scripts/init_project.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def fixture(root: Path) -> None:
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "pcb-flow.json").write_text(json.dumps({
        "project": {"name": "template"},
        "manufacturing": {"output": "manufacturing/example"},
    }), encoding="utf-8")
    (config / "board-stack.json").write_text(json.dumps({
        "active": "two_layer",
        "profiles": {
            "two_layer": {"copper_layers": ["F.Cu", "B.Cu"], "forbidden_signal_layers": []},
            "four_layer": {"copper_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"], "forbidden_signal_layers": ["In1.Cu", "In2.Cu"]},
        },
    }), encoding="utf-8")
    (config / "kicad-routing-tools.json").write_text(json.dumps({
        "profiles": {"outer_low_risk": {"routing": {"layers": [], "layer_costs": []}}},
        "guardrails": {"forbidden_signal_layers": []},
    }), encoding="utf-8")


class InitProjectTests(unittest.TestCase):
    def test_init_creates_local_artifacts_and_stage_without_tools(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pcb template ") as directory:
            root = Path(directory)
            fixture(root)
            with mock.patch.object(MODULE, "ROOT", root), mock.patch.object(
                MODULE.shutil, "which", return_value="node"
            ):
                message = MODULE.configure_project("demo-board", "Ubuntu-24.04", False, False, 2)

            self.assertIn("Wrote", message)
            toml = (root / ".codex/config.toml").read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.atopile]", toml)
            self.assertNotIn("[mcp_servers.konnect]", toml)
            self.assertIn("Ubuntu-24.04", toml)
            # Paths with spaces must be shell quoted, but the surrounding TOML
            # argument must be double quoted so the nested shell quotes survive.
            self.assertIn("cd '", toml)
            self.assertTrue((root / "handoff/stages/CURRENT_STAGE.json").is_file())
            flow = json.loads((root / "config/pcb-flow.json").read_text(encoding="utf-8"))
            self.assertEqual(flow["project"]["name"], "demo-board")
            stack = json.loads((root / "config/board-stack.json").read_text(encoding="utf-8"))
            self.assertEqual(stack["active"], "two_layer")

    def test_existing_codex_config_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            local = root / ".codex/config.toml"
            local.parent.mkdir(parents=True)
            local.write_text("# existing personal configuration\n", encoding="utf-8")
            with mock.patch.object(MODULE, "ROOT", root), mock.patch.object(
                MODULE.shutil, "which", return_value="node"
            ):
                message = MODULE.configure_project("demo-board", "Ubuntu-24.04", False, False, 2)
            self.assertIn("preserved", message)
            self.assertEqual(local.read_text(encoding="utf-8"), "# existing personal configuration\n")
            self.assertTrue((root / ".codex/pcb-mcp.fragment.toml").is_file())

    def test_rejects_names_and_distro_names_before_modifying_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            with mock.patch.object(MODULE, "ROOT", root):
                with self.assertRaises(SystemExit):
                    MODULE.configure_project("../bad", "Ubuntu-24.04", False, False, 2)
                with self.assertRaises(SystemExit):
                    MODULE.configure_project("safe", "Ubuntu; echo bad", False, False, 2)
            self.assertFalse((root / ".codex/config.toml").exists())


if __name__ == "__main__":
    unittest.main()
