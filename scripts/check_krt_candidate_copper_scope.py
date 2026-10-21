#!/usr/bin/env python3
"""Prove that a KRT candidate changed copper only on explicitly selected nets."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re


def sexpr_blocks(text: str, head: str):
    marker = f"({head}"
    start = 0
    while True:
        index = text.find(marker, start)
        if index < 0:
            return
        boundary = index + len(marker)
        if boundary < len(text) and not text[boundary].isspace():
            start = boundary
            continue
        depth = 0
        quoted = False
        escaped = False
        for end in range(index, len(text)):
            char = text[end]
            if escaped:
                escaped = False
                continue
            if char == "\\" and quoted:
                escaped = True
                continue
            if char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        yield text[index:end + 1]
                        start = end + 1
                        break
        else:
            return


def net_map(text: str) -> dict[str, str]:
    return {
        code: bytes(name, "utf-8").decode("unicode_escape")
        for code, name in re.findall(r'\(net\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\)', text)
    }


def field(block: str, name: str) -> str:
    match = re.search(rf'\({re.escape(name)}\s+([^()]+?)\)', block)
    return " ".join(match.group(1).split()) if match else ""


def net_name(value: str, names: dict[str, str]) -> str:
    if value.startswith('"') and value.endswith('"'):
        return bytes(value[1:-1], "utf-8").decode("unicode_escape")
    return names.get(value, value)


def signatures(path: Path) -> Counter[tuple[str, ...]]:
    text = path.read_text(encoding="utf-8")
    names = net_map(text)
    result: Counter[tuple[str, ...]] = Counter()
    for block in sexpr_blocks(text, "segment"):
        code = field(block, "net")
        endpoints = tuple(sorted((field(block, "start"), field(block, "end"))))
        result[("track", net_name(code, names), field(block, "layer"), *endpoints, field(block, "width"))] += 1
    for block in sexpr_blocks(text, "arc"):
        code = field(block, "net")
        result[(
            "arc", net_name(code, names), field(block, "layer"), field(block, "start"),
            field(block, "mid"), field(block, "end"), field(block, "width"),
        )] += 1
    for block in sexpr_blocks(text, "via"):
        code = field(block, "net")
        result[(
            "via", net_name(code, names), field(block, "at"), field(block, "size"),
            field(block, "drill"), field(block, "layers"),
        )] += 1
    return result


def net_counts(delta: Counter[tuple[str, ...]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for signature, count in delta.items():
        counts[signature[1]] += count
    return dict(sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--nets", nargs="+", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    before = signatures(args.input.resolve())
    after = signatures(args.candidate.resolve())
    additions = after - before
    removals = before - after
    selected = set(args.nets)
    added_by_net = net_counts(additions)
    removed_by_net = net_counts(removals)
    outside_additions = {net: count for net, count in added_by_net.items() if net not in selected}
    outside_removals = {net: count for net, count in removed_by_net.items() if net not in selected}
    receipt = {
        "success": not outside_additions and not outside_removals,
        "selected_nets": sorted(selected),
        "added_copper_items_by_net": added_by_net,
        "removed_copper_items_by_net": removed_by_net,
        "outside_scope_additions": outside_additions,
        "outside_scope_removals": outside_removals,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        receipt["output"] = str(args.output.resolve())
    print(json.dumps(receipt, separators=(",", ":")))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
