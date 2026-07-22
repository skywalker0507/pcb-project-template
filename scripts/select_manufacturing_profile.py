#!/usr/bin/env python3
"""Select a manufacturer profile without inventing fabrication values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pcb_policy import valid_source


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manufacturer", required=True)
    parser.add_argument("--source", required=True, help="Manufacturer URL or reviewed local process-card path")
    parser.add_argument("--profile", type=Path, default=ROOT / "config/manufacturing-profile.json")
    args = parser.parse_args()
    if not args.manufacturer.strip() or not args.source.strip():
        raise SystemExit("--manufacturer and --source must not be empty")
    if not valid_source(args.source.strip(), Path.cwd()):
        raise SystemExit("--source must be an http(s) URL or an existing local process-card path")
    profile = {
        "schema": 1, "selected": True, "manufacturer": args.manufacturer.strip(), "source": args.source.strip(),
        "routing_rules": {"reviewed": False, "values": {}, "evidence": []},
        "notes": ["Selection records provenance only. Run apply_reviewed_fab_values.py after independent review before real routing."],
    }
    path = args.profile.resolve()
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": True, "profile": str(path), "manufacturer": profile["manufacturer"], "source": profile["source"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
