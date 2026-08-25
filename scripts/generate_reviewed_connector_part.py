#!/usr/bin/env python3
"""Generate a project-local atomic connector from reviewed KiCad assets.

Run with the atopile Python environment because this uses atopile's pinned
KiCad symbol parser. The caller remains responsible for primary-source land
pattern and pin-number review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from faebryk.libs.kicad.fileformats import kicad


def ato_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit("--identifier must be a valid atopile identifier")
    return value


def quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def add_identity_properties(source: str, manufacturer: str, mpn: str) -> str:
    """Bind reviewed identity into the footprint without marking it as SMT-picked."""
    if '(property "Manufacturer"' in source or '(property "Partnumber"' in source:
        raise SystemExit("Reviewed footprint already contains identity properties")
    marker = re.search(r"(?m)^\t\(attr ", source)
    if marker is None:
        raise SystemExit("Reviewed footprint has no attr section for identity insertion")
    blocks = ""
    for name, value in (("Manufacturer", manufacturer), ("Partnumber", mpn)):
        blocks += (
            f'\t(property {quoted(name)} {quoted(value)}\n'
            "\t\t(at 0 0 0)\n"
            "\t\t(layer \"F.Fab\")\n"
            "\t\t(hide yes)\n"
            "\t\t(effects\n"
            "\t\t\t(font\n"
            "\t\t\t\t(size 1 1)\n"
            "\t\t\t\t(thickness 0.15)\n"
            "\t\t\t)\n"
            "\t\t)\n"
            "\t)\n"
        )
    return source[: marker.start()] + blocks + source[marker.start() :]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identifier", required=True, type=ato_identifier)
    parser.add_argument("--manufacturer", required=True)
    parser.add_argument("--mpn", required=True)
    parser.add_argument("--footprint", type=Path, required=True)
    parser.add_argument("--symbol-library", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--pin-count", type=int, required=True)
    parser.add_argument("--designator-prefix", default="J")
    parser.add_argument("--output-root", type=Path, default=Path("src/parts"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.pin_count <= 100:
        raise SystemExit("--pin-count must be between 1 and 100")
    if not re.fullmatch(r"[A-Z]+", args.designator_prefix):
        raise SystemExit("--designator-prefix must contain uppercase letters only")

    footprint = args.footprint.resolve()
    symbol_library = args.symbol_library.resolve()
    if not footprint.is_file() or footprint.suffix != ".kicad_mod":
        raise SystemExit(f"Reviewed footprint is unavailable: {footprint}")
    if not symbol_library.is_file() or symbol_library.suffix != ".kicad_sym":
        raise SystemExit(f"KiCad symbol library is unavailable: {symbol_library}")

    output = (args.output_root.resolve() / args.identifier)
    if output.exists() and any(output.iterdir()) and not args.force:
        raise SystemExit(f"Refusing to overwrite non-empty atomic part: {output}")
    output.mkdir(parents=True, exist_ok=True)

    symbol_file = kicad.loads(kicad.symbol.SymbolFile, symbol_library)
    matches = [item for item in symbol_file.kicad_sym.symbols if item.name == args.symbol]
    if len(matches) != 1:
        raise SystemExit(f"Expected one symbol {args.symbol!r}, found {len(matches)}")
    symbol_file.kicad_sym.symbols = matches

    footprint_output = output / footprint.name
    symbol_output = output / f"{args.symbol}.kicad_sym"
    ato_output = output / f"{args.identifier}.ato"
    footprint_output.write_text(
        add_identity_properties(
            footprint.read_text(encoding="utf-8"), args.manufacturer, args.mpn
        ),
        encoding="utf-8",
    )
    kicad.dumps(symbol_file, symbol_output)

    pins = "\n".join(f"    pin {number}" for number in range(1, args.pin_count + 1))
    ato_output.write_text(
        "import has_designator_prefix\n"
        "import is_atomic_part\n\n"
        f"component {args.identifier}_package:\n"
        "    \"\"\"Manually reviewed connector atomic part.\"\"\"\n\n"
        "    trait is_atomic_part<"
        f"manufacturer={quoted(args.manufacturer)}, "
        f"partnumber={quoted(args.mpn)}, "
        f"footprint={quoted(footprint_output.name)}, "
        f"symbol={quoted(symbol_output.name)}>\n"
        f"    trait has_designator_prefix<prefix={quoted(args.designator_prefix)}>\n\n"
        "    # Physical pin numbers are the electrical contract.\n"
        f"{pins}\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "success": True,
        "kind": "reviewed_connector_atomic_part",
        "identifier": args.identifier,
        "manufacturer": args.manufacturer,
        "mpn": args.mpn,
        "pin_count": args.pin_count,
        "footprint_source": str(footprint),
        "symbol_source": str(symbol_library),
        "symbol": args.symbol,
        "output": str(output),
        "limit": "Generation does not prove footprint geometry or connector pin assignment.",
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
