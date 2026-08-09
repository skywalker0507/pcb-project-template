#!/usr/bin/env python3
"""Phase 1 helper: remove routed tracks/vias for named nets and save an isolated board."""
from __future__ import annotations
import argparse,json,shutil,subprocess,sys
from pathlib import Path
KICAD_PYTHON=Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
try:import pcbnew
except ImportError:raise SystemExit(subprocess.call([str(KICAD_PYTHON),str(Path(__file__).resolve()),*sys.argv[1:]]))
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);ap.add_argument("--net",action="append",required=True);a=ap.parse_args();src,out=a.input.resolve(),a.output.resolve()
 if not src.is_file() or out.exists() or src==out:raise SystemExit("Input must exist and output must be new")
 board=pcbnew.LoadBoard(str(src));nets=set(a.net);removed=[item for item in board.GetTracks() if item.GetNetname() in nets]
 for item in removed:board.Remove(item)
 pcbnew.ZONE_FILLER(board).Fill(board.Zones());out.parent.mkdir(parents=True,exist_ok=True);pcbnew.SaveBoard(str(out),board)
 project=src.with_suffix(".kicad_pro")
 if project.is_file():shutil.copy2(project,out.with_suffix(".kicad_pro"))
 print(json.dumps({"success":True,"nets":sorted(nets),"removed_items":len(removed),"scope":"removal-only intermediate"},separators=(",",":")));return 0
if __name__=="__main__":raise SystemExit(main())
