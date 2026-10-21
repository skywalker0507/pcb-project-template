#!/usr/bin/env python3
"""Normalize one candidate net to the reviewed default width, then refill zones."""
from __future__ import annotations
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];KICAD_PYTHON=Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
try:import pcbnew
except ImportError:raise SystemExit(subprocess.call([str(KICAD_PYTHON),str(Path(__file__).resolve()),*sys.argv[1:]]))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest().upper()
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);ap.add_argument("--net",action="append",required=True);a=ap.parse_args();src,out=a.input.resolve(),a.output.resolve()
 if not src.is_file() or out.exists() or src==out:raise SystemExit("Input must exist and output must be new")
 width=json.loads((ROOT/"config/manufacturing-profile.json").read_text(encoding="utf-8"))["routing_rules"]["values"]["track_width"];board=pcbnew.LoadBoard(str(src));changed=0
 nets=set(a.net)
 for item in board.GetTracks():
  if not isinstance(item,pcbnew.PCB_VIA) and item.GetNetname() in nets and item.GetWidth()!=pcbnew.FromMM(width):item.SetWidth(pcbnew.FromMM(width));changed+=1
 board.BuildConnectivity();pcbnew.ZONE_FILLER(board).Fill(board.Zones());out.parent.mkdir(parents=True,exist_ok=True);pcbnew.SaveBoard(str(out),board)
 project=src.with_suffix(".kicad_pro")
 if project.is_file():shutil.copy2(project,out.with_suffix(".kicad_pro"))
 print(json.dumps({"success":True,"input_sha256":digest(src),"output_sha256":digest(out),"nets":sorted(nets),"width_mm":width,"changed_segments":changed,"zones_refilled":True},separators=(",",":")));return 0
if __name__=="__main__":raise SystemExit(main())
