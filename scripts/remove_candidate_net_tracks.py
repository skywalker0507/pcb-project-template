#!/usr/bin/env python3
"""Remove named-net copper while preserving an isolated KiCad project context."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python

KICAD_PYTHON = kicad_python()

try:
    import pcbnew
except ImportError:
    ensure_host_kicad_python()
    import pcbnew


def copy_context(source: Path, output: Path, context_source: Path | None) -> None:
    for suffix in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
        companion = source.with_suffix(suffix)
        if not companion.is_file() and context_source is not None:
            companion = context_source.with_suffix(suffix)
        if companion.is_file():
            shutil.copy2(companion, output.with_suffix(suffix))
    context_dir = (context_source or source).parent
    for name in ("fp-lib-table", "sym-lib-table"):
        companion = source.parent / name
        if not companion.is_file():
            companion = context_dir / name
        if not companion.is_file():
            continue
        text = companion.read_text(encoding="utf-8")

        def rebase(match: re.Match[str]) -> str:
            target = (companion.parent / match.group(1)).resolve()
            return "${KIPRJMOD}/" + Path(os.path.relpath(target, output.parent)).as_posix()

        (output.parent / name).write_text(
            re.sub(r"\$\{KIPRJMOD\}/([^\")]+)", rebase, text),
            encoding="utf-8",
            newline="\n",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--net", action="append", required=True)
    parser.add_argument(
        "--context-source",
        type=Path,
        help="Known-good project whose library tables are used only when the input lacks them.",
    )
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    context_source = args.context_source.resolve() if args.context_source else None
    if not source.is_file() or output.exists() or source == output:
        raise SystemExit("Input must exist and output must be new")
    if context_source is not None and not context_source.is_file():
        raise SystemExit("Context source must be an existing KiCad board")

    board = pcbnew.LoadBoard(str(source))
    nets = set(args.net)
    removed = [item for item in board.GetTracks() if item.GetNetname() in nets]
    for item in removed:
        board.Remove(item)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    output.parent.mkdir(parents=True, exist_ok=True)
    pcbnew.SaveBoard(str(output), board)
    copy_context(source, output, context_source)
    print(json.dumps({
        "success": True,
        "nets": sorted(nets),
        "removed_items": len(removed),
        "context_source": str(context_source) if context_source else None,
        "scope": "removal-only intermediate",
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
