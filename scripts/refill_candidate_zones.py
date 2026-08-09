#!/usr/bin/env python3
"""Refill copper zones in an isolated candidate without changing its routed items."""
from __future__ import annotations
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path
KICAD_PYTHON=Path(r"C:\Program Files\KiCad\10.0\bin\python.exe")
try:import pcbnew
except ImportError:raise SystemExit(subprocess.call([str(KICAD_PYTHON),str(Path(__file__).resolve()),*sys.argv[1:]]))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest().upper()
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);a=ap.parse_args();src,out=a.input.resolve(),a.output.resolve()
 if not src.is_file() or out.exists() or src==out:raise SystemExit("Input must exist and output must be new")
 before=digest(src);board=pcbnew.LoadBoard(str(src));board.BuildConnectivity();pcbnew.ZONE_FILLER(board).Fill(board.Zones());out.parent.mkdir(parents=True,exist_ok=True);pcbnew.SaveBoard(str(out),board)
 project=src.with_suffix(".kicad_pro")
 if project.is_file():shutil.copy2(project,out.with_suffix(".kicad_pro"))
 print(json.dumps({"success":True,"input_sha256":before,"output_sha256":digest(out),"scope":"zone refill only"},separators=(",",":")));return 0
if __name__=="__main__":raise SystemExit(main())
