from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("jlcpcb_assembly", ROOT / "scripts/generate-jlcpcb-assembly.py")
assembly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assembly)


class AssemblyConsignmentTests(unittest.TestCase):
    def test_validate_only_checks_full_board_without_positions_or_output_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            board, source, _, parts, native = self.fixture(root)
            source.write_text(json.dumps(parts), encoding="utf-8")
            argv = ["assembly", "--parts-source", str(source), "--board-source", str(board), "--validate-only"]
            with patch.dict(sys.modules, {"pcbnew": native}), patch.object(sys, "argv", argv), patch("builtins.print") as output:
                assembly.main()
                value = json.loads(output.call_args.args[0])
            self.assertEqual(value["assembly_designators"], 2)
            self.assertEqual(value["customer_supplied_designators"], ["J1"])
            self.assertFalse((root / "assembly").exists())
            parts["parts"].pop(0)
            source.write_text(json.dumps(parts), encoding="utf-8")
            with patch.dict(sys.modules, {"pcbnew": native}), patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(SystemExit, "missing from assembly"):
                    assembly.main()

    def fixture(self, root: Path):
        board = root / "board.kicad_pcb"
        board.write_text("exact fixture board", encoding="utf-8")
        parts = {
            "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
            "parts": [
                {"refs": ["J1"], "mpn": "CONNECTOR-EXACT-A", "manufacturer": "Connector Maker",
                 "lcsc": None, "sourcing": "customer_supplied"},
                {"refs": ["R1"], "mpn": "RESISTOR-EXACT", "lcsc": "C123"},
            ],
            "dnp_refs": [],
        }
        source = root / "parts.json"
        position = root / "position.csv"
        position.write_text("Ref,PosX,PosY,Rot,Side\nJ1,10,-20,180,bottom\nR1,11,-21,0,top\n", encoding="utf-8")

        def footprint(ref):
            fpid = SimpleNamespace(GetLibNickname=lambda: "lib", GetLibItemName=lambda: ref)
            return SimpleNamespace(GetReference=lambda: ref, GetValue=lambda: "",
                                   GetFPID=lambda: fpid,
                                   Pads=lambda: [SimpleNamespace(GetAttribute=lambda: 1)])

        native = SimpleNamespace(PAD_ATTRIB_SMD=1,
                                 LoadBoard=lambda _: SimpleNamespace(GetFootprints=lambda: [footprint("J1"), footprint("R1")]))
        return board, source, position, parts, native

    def run_generator(self, root, board, source, position, parts, native):
        source.write_text(json.dumps(parts), encoding="utf-8")
        argv = ["assembly", "--parts-source", str(source), "--board-source", str(board),
                "--pos-source", str(position), "--output-dir", str(root / "assembly")]
        with patch.dict(sys.modules, {"pcbnew": native}), patch.object(sys, "argv", argv), patch("builtins.print"):
            assembly.main()

    def test_consigned_part_keeps_exact_identity_and_bottom_placement_in_complete_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = self.fixture(root)
            self.run_generator(root, *fixture)
            with (root / "assembly/BOM_JLCPCB.csv").open(encoding="utf-8-sig", newline="") as handle:
                bom = {row["Designator"]: row for row in csv.DictReader(handle)}
            with (root / "assembly/CPL_JLCPCB.csv").open(encoding="utf-8-sig", newline="") as handle:
                cpl = {row["Designator"]: row for row in csv.DictReader(handle)}
            self.assertEqual(set(bom), {"J1", "R1"})
            self.assertEqual(set(cpl), set(bom))
            self.assertEqual(bom["J1"]["LCSC Part #"], "")
            self.assertEqual(bom["J1"]["Manufacturer Part Number"], "CONNECTOR-EXACT-A")
            self.assertEqual(bom["J1"]["Manufacturer"], "Connector Maker")
            self.assertEqual(bom["J1"]["Sourcing"], "customer_supplied")
            self.assertEqual(cpl["J1"], {"Designator": "J1", "Mid X": "10.0000mm", "Mid Y": "20.0000mm",
                                         "Layer": "Bottom", "Rotation": "180.00"})
            validation = json.loads((root / "assembly/assembly-validation.json").read_text())
            self.assertEqual(validation["assembly_designators"], 2)
            self.assertEqual(validation["customer_supplied_designators"], ["J1"])
            self.assertTrue(validation["factory_matching_required"])
            self.assertEqual(validation["position_rows_excluded_not_in_bom"], [])

    def test_consignment_cannot_mask_missing_mapping_identity_or_board_mismatch(self):
        mutations = {
            "implicit blank catalog ID": lambda p: p["parts"][0].pop("sourcing"),
            "unknown mode": lambda p: p["parts"][0].update(sourcing="manual_skip"),
            "missing manufacturer": lambda p: p["parts"][0].pop("manufacturer"),
            "missing MPN": lambda p: p["parts"][0].update(mpn=None),
            "fabricated ID": lambda p: p["parts"][0].update(lcsc="CUSTOM-J1"),
            "dropped placement": lambda p: p["parts"].pop(0),
            "stale board": lambda p: p.update(board_sha256="STALE"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                board, source, position, parts, native = self.fixture(root)
                mutate(parts)
                with self.assertRaises(SystemExit):
                    self.run_generator(root, board, source, position, parts, native)
                self.assertFalse((root / "assembly/BOM_JLCPCB.csv").exists())


if __name__ == "__main__":
    unittest.main()
