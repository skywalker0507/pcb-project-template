#!/usr/bin/env python3
"""Record explicitly reviewed routing values; never infer them from a source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pcb_policy import read_object, selected_manufacturing_profile, valid_source


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"via_diameter", "via_drill", "clearance", "track_width", "hole_to_hole"}


def parse_value(value: str) -> tuple[str, float]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("values must use name=value")
    name, raw = value.split("=", 1)
    if name not in ALLOWED:
        raise argparse.ArgumentTypeError(f"unsupported value {name!r}")
    try:
        number = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid numeric value: {raw!r}") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("values must be positive")
    return name, number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--value", action="append", required=True, type=parse_value, help="Reviewed millimetre value, e.g. clearance=0.2")
    parser.add_argument("--evidence", action="append", required=True, help="Source page/section or local-file locator for the reviewed values")
    parser.add_argument("--source", help="Must match the selected source unless intentionally changed")
    parser.add_argument("--profile", type=Path, default=ROOT / "config/manufacturing-profile.json")
    parser.add_argument("--overrides", type=Path, default=ROOT / "config/krt-fab-overrides.txt")
    args = parser.parse_args()
    profile_path = args.profile.resolve()
    profile = selected_manufacturing_profile(profile_path)
    source = args.source or str(profile["source"])
    if source != profile["source"] or not valid_source(source, Path.cwd()):
        raise SystemExit("--source must match the selected valid manufacturing source")
    values = dict(args.value)
    evidence = [item.strip() for item in args.evidence if item.strip()]
    if not evidence:
        raise SystemExit("At least one non-empty --evidence is required")
    profile["routing_rules"] = {"reviewed": True, "source": source, "values": values, "evidence": evidence}
    profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    overrides = args.overrides.resolve()
    overrides.write_text("\n".join(f"{key} = {value:g}" for key, value in sorted(values.items())) + "\n", encoding="utf-8")
    krt_path = ROOT / "config/kicad-routing-tools.json"
    krt = read_object(krt_path, "KRT policy")
    krt["profiles"]["outer_low_risk"]["routing"]["fab_overrides"] = str(overrides.relative_to(ROOT).as_posix())
    krt_path.write_text(json.dumps(krt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": True, "profile": str(profile_path), "overrides": str(overrides), "values": values, "evidence": evidence}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
