#!/usr/bin/env python3
"""Give every KiCad footprint instance and child item a deterministic unique UUID."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python


KICAD_PYTHON = kicad_python()
try:
    import pcbnew
except ImportError:
    ensure_host_kicad_python()
    import pcbnew


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def item_uuid(item: object) -> str:
    return item.m_Uuid.AsString()


def instance_items(board: object) -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    footprints = sorted(board.GetFootprints(), key=lambda fp: fp.GetReference())
    for footprint in footprints:
        ref = footprint.GetReference()
        items.append((f"footprint:{ref}", footprint))
        groups = (
            ("pad", list(footprint.Pads())),
            ("field", list(footprint.GetFields())),
            ("graphic", list(footprint.GraphicalItems())),
            ("zone", list(footprint.Zones())),
        )
        for kind, children in groups:
            for index, child in enumerate(children):
                items.append((f"footprint:{ref}:{kind}:{index}", child))
    return items


def duplicate_summary(items: list[tuple[str, object]]) -> tuple[int, int]:
    counts = Counter(item_uuid(item) for _, item in items)
    duplicates = [count for count in counts.values() if count > 1]
    return len(duplicates), sum(count - 1 for count in duplicates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-sha256", required=True)
    args = parser.parse_args()

    source = args.input.resolve()
    output = args.output.resolve()
    if not source.is_file() or output.exists() or source == output:
        raise SystemExit("Input must exist and output must be a new isolated candidate")
    actual_hash = sha256(source)
    if actual_hash != args.input_sha256.upper():
        raise SystemExit(f"Input SHA-256 mismatch: expected {args.input_sha256.upper()}, got {actual_hash}")

    board = pcbnew.LoadBoard(str(source))
    items = instance_items(board)
    before_groups, before_extra = duplicate_summary(items)
    seen: set[str] = set()
    changed = 0
    for _, item in items:
        current = item_uuid(item)
        if current in seen:
            while item_uuid(item) in seen:
                item.m_Uuid.Increment()
            changed += 1
        seen.add(item_uuid(item))
    after_groups, after_extra = duplicate_summary(items)
    if after_groups or after_extra:
        raise SystemExit("Deterministic UUID normalization did not produce unique instance items")

    output.parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(output), board)
    for suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
        companion = source.with_suffix(suffix)
        if companion.is_file():
            shutil.copy2(companion, output.with_suffix(suffix))
    receipt = {
        "schema": 1,
        "kind": "kicad_instance_uuid_normalization",
        "success": True,
        "input": str(source),
        "input_sha256": actual_hash,
        "output": str(output),
        "output_sha256": sha256(output),
        "instance_item_count": len(items),
        "duplicate_uuid_groups_before": before_groups,
        "duplicate_uuid_extra_items_before": before_extra,
        "changed_instance_items": changed,
        "duplicate_uuid_groups_after": after_groups,
        "duplicate_uuid_extra_items_after": after_extra,
        "scope": "UUID identity only; references, footprints, pads, nets, placement and board geometry are unchanged.",
    }
    print(json.dumps(receipt, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
