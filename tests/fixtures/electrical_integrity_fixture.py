"""Native KiCad regression fixture; no generated CAD is printed or adopted."""

import json
import faulthandler
from pathlib import Path
import sys
import tempfile

import pcbnew

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from check_electrical_candidate_integrity import audit

faulthandler.enable()


def run():
    with tempfile.TemporaryDirectory(prefix="pcb-integrity-") as directory:
        root = Path(directory).resolve()
        source = root / "source"
        library = source / "nested/Fixture.pretty"
        library.mkdir(parents=True)
        fp = pcbnew.FOOTPRINT(None)
        fp.SetFPID(pcbnew.LIB_ID("Fixture", "One"))
        fp.SetReference("REF**")
        fp.SetField("Manufacturer", "Fixture Manufacturer")
        fp.SetField("Partnumber", "EXACT-ONE")
        pad = pcbnew.PAD(fp)
        pad.SetNumber("1")
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetShape(pcbnew.PAD_SHAPE_ROUNDRECT)
        pad.SetRoundRectRadiusRatio(0.2)
        pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(1), pcbnew.FromMM(2)))
        layers = pcbnew.LSET()
        layers.AddLayer(pcbnew.F_Cu)
        pad.SetLayerSet(layers)
        fp.Add(pad)
        def primitive(x=0):
            item = pcbnew.PCB_SHAPE()
            item.SetShape(pcbnew.SHAPE_T_CIRCLE)
            item.SetCenter(pcbnew.VECTOR2I(pcbnew.FromMM(x), 0))
            item.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x+0.6), 0))
            item.SetWidth(pcbnew.FromMM(0.1))
            item.SetFilled(True)
            return item
        custom = pcbnew.PAD(fp)
        custom.SetNumber("C")
        custom.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        custom.SetShape(pcbnew.PAD_SHAPE_CUSTOM)
        custom.SetAnchorPadShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_RECT)
        custom.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.2), pcbnew.FromMM(0.2)))
        custom.SetLayerSet(layers)
        first_primitive = primitive()
        custom.AddPrimitiveShape(pcbnew.F_Cu, first_primitive)
        first_primitive.thisown = False  # PAD now owns the C++ primitive.
        fp.Add(custom)
        zone = pcbnew.ZONE(fp)
        zone.SetIsRuleArea(True)
        zone.SetLayerSet(pcbnew.LSET.AllCuMask())
        zone.SetDoNotAllowTracks(True)
        zone.SetDoNotAllowVias(True)
        zone.SetDoNotAllowPads(True)
        zone.SetDoNotAllowZoneFills(True)
        zone.SetDoNotAllowFootprints(True)
        shape = zone.Outline()
        shape.NewOutline()
        for x, y in ((-3, -3), (3, -3), (3, 3), (-3, 3)):
            shape.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        shape.NewHole()
        for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            shape.Append(pcbnew.FromMM(x), pcbnew.FromMM(y), 0, 0)
        fp.Add(zone)
        plugin = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
        plugin.FootprintSave(str(library), fp)
        board = pcbnew.BOARD()
        embedded = pcbnew.FootprintLoad(str(library), "One")
        embedded.SetFPID(pcbnew.LIB_ID("Fixture", "One"))
        embedded.SetReference("U1")
        embedded.SetOrientationDegrees(90)
        embedded.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(31), pcbnew.FromMM(47)))
        board.Add(embedded)
        original = root / "base.kicad_pcb"
        pcbnew.SaveBoard(str(original), board)
        results = {"translated_rotated": audit(pcbnew, original, source)}

        def changed(name, mutate):
            candidate = pcbnew.LoadBoard(str(original))
            item = list(candidate.GetFootprints())[0]
            mutate(candidate, item)
            path = root / f"{name}.kicad_pcb"
            pcbnew.SaveBoard(str(path), candidate)
            report = audit(pcbnew, path, source)
            results[name] = report

        changed("flipped", lambda b, f: f.Flip(f.GetPosition(), False))
        changed("missing_rule_area", lambda b, f: f.Remove(list(f.Zones())[0]))
        changed("changed_rule_geometry", lambda b, f: list(f.Zones())[0].Move(pcbnew.VECTOR2I(pcbnew.FromMM(1), 0)))
        changed("changed_restriction", lambda b, f: list(f.Zones())[0].SetDoNotAllowTracks(False))
        changed("changed_pad_number", lambda b, f: list(f.Pads())[0].SetNumber("2"))
        changed("changed_pad_radius", lambda b, f: list(f.Pads())[0].SetRoundRectRadiusRatio(0.4))
        changed("changed_mpn", lambda b, f: f.SetField("Partnumber", "SUBSTITUTE"))
        def change_custom(b, f, flip=False):
            if flip:
                f.Flip(f.GetPosition(), False)
            pad = next(pad for pad in f.Pads() if pad.GetNumber() == "C")
            extra = primitive(2)
            pad.AddPrimitiveShape(pcbnew.B_Cu if flip else pcbnew.F_Cu, extra)
            extra.thisown = False
        changed("changed_custom_pad", change_custom)
        changed("changed_flipped_custom_pad", lambda b, f: change_custom(b, f, True))
        def duplicate(b, f):
            clone = pcbnew.FOOTPRINT(f)
            clone.SetReference(f.GetReference())
            b.Add(clone)
        changed("duplicate_ref", duplicate)
        return results


print(json.dumps(run(), separators=(",", ":")))
