#!/usr/bin/env python3
"""Generate a local atopile atomic part from a pre-fetched EasyEDA cache entry."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from faebryk.libs.part_lifecycle import PartLifecycle
from faebryk.libs.picker.lcsc import download_easyeda_info


ROOT = Path(__file__).resolve().parents[1]
LCSC_RE = re.compile(r"C[1-9][0-9]*")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a reviewed local EasyEDA cache entry into an atomic part."
    )
    parser.add_argument("--lcsc", required=True)
    args = parser.parse_args()

    if not LCSC_RE.fullmatch(args.lcsc):
        raise SystemExit(f"Invalid LCSC identifier: {args.lcsc!r}")
    cache_json = ROOT / "build/cache/parts/easyeda" / args.lcsc / f"{args.lcsc}.json"
    if not cache_json.is_file():
        raise SystemExit(
            f"Missing reviewed cache entry: {cache_json}. "
            "Run scripts/cache_easyeda_part.py first."
        )

    part = download_easyeda_info(args.lcsc, get_model=False)
    atomic = PartLifecycle.singleton().library.ingest_part_from_easyeda(part)
    if not atomic.ato_path.is_file() or not atomic.fp_path.is_file() or not atomic.sym_path.is_file():
        raise SystemExit("Atomic-part generation did not produce all required resources")

    print(
        json.dumps(
            {
                "success": True,
                "lcsc": args.lcsc,
                "part": str(atomic.ato_path),
                "footprint": str(atomic.fp_path),
                "symbol": str(atomic.sym_path),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
