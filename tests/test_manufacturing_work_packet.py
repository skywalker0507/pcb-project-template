from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from import_manufacturing_dfm_result import validate_review
from manufacturing_work_packet import (
    create_work_packet,
    verify_work_packet,
    write_observation_skeleton,
    write_quote_skeleton,
)


class ManufacturingWorkPacketTests(unittest.TestCase):
    def source(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content, encoding="utf-8")
        return path

    def create(self, root: Path):
        inputs = root / "inputs"
        inputs.mkdir()
        packet = root / "packet"
        packet.mkdir()
        path, value = create_work_packet(
            packet,
            board=self.source(inputs, "board.kicad_pcb", "board"),
            audit=self.source(inputs, "audit.json", "{}"),
            gerber_zip=self.source(inputs, "gerber.zip", "zip"),
            bom=self.source(inputs, "bom.csv", "Designator\nR1\n"),
            cpl=self.source(inputs, "cpl.csv", "Designator\nR1\n"),
            vendor="demo",
            quote_currency="CNY",
            order_parameters={
                "surface_finish": "ENIG", "solder_mask_color": "black",
                "assembly_service": "smt", "required_dfm_modules": ["pcb", "smt"],
            },
        )
        return path, value

    def test_packet_copies_and_verifies_exact_upload_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, value = self.create(Path(directory))
            manifest, files = verify_work_packet(path)
            self.assertEqual(manifest["content_sha256"], value["content_sha256"])
            self.assertEqual(set(files), {"board", "candidate_audit", "gerber_zip", "bom", "cpl"})
            self.assertEqual({item.name for item in (path.parent / "upload").iterdir()}, {
                "candidate-gerber.zip", "BOM_JLCPCB.csv", "CPL_JLCPCB.csv",
            })

    def test_packet_rejects_changed_upload_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.create(Path(directory))
            (path.parent / "upload/BOM_JLCPCB.csv").write_text("changed", encoding="utf-8")
            with self.assertRaises(SystemExit):
                verify_work_packet(path)

    def test_dfm_observation_requires_confirmed_resolved_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, manifest = self.create(Path(directory))
            observation_path = write_observation_skeleton(path.parent, manifest)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            self.assertIn("review_not_confirmed", validate_review(observation, manifest["content_sha256"]))
            observation["review"].update({"status": "confirmed", "reviewer": "owner"})
            observation["analysis_modules"] = {
                "pcb": {"executed": True, "pass": True},
                "smt": {"executed": True, "pass": True},
            }
            observation["findings_summary"].update({"errors": 1, "warnings": 2})
            observation["findings_summary"]["accepted_exceptions"] = [{
                "rule": "same-net mask bridge", "scope": "D1/F1", "reason": "same electrical net",
            }]
            self.assertEqual(validate_review(observation, manifest["content_sha256"]), [])

    def test_quote_skeleton_is_prebound_to_every_packet_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, manifest = self.create(Path(directory))
            quote = json.loads(write_quote_skeleton(path.parent, manifest).read_text(encoding="utf-8"))
            self.assertEqual(quote["work_packet_content_sha256"], manifest["content_sha256"])
            self.assertEqual(set(quote["artifacts"]), {"board", "gerber_zip", "bom", "cpl"})
            self.assertEqual(quote["approval"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
