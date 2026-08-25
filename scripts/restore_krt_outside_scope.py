#!/usr/bin/env python3
"""Restore all unselected copper blocks from a KRT input into its candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from check_krt_candidate_copper_scope import field, net_map, net_name


HEADS = ("segment", "arc", "via")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def block_spans(text: str, head: str):
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
                        yield index, end + 1, text[index:end + 1]
                        start = end + 1
                        break
        else:
            return


def copper_blocks(text: str):
    names = net_map(text)
    blocks = []
    for head in HEADS:
        for start, end, block in block_spans(text, head):
            blocks.append((start, end, net_name(field(block, "net"), names), block))
    return sorted(blocks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--nets", nargs="+", required=True)
    args = parser.parse_args()

    candidate_path = args.candidate.resolve()
    before_hash = sha256(candidate_path)
    if before_hash != args.expected_sha256.upper():
        raise SystemExit("Candidate SHA-256 changed before outside-scope restoration")
    selected = set(args.nets)
    source_text = args.input.resolve().read_text(encoding="utf-8")
    candidate_text = candidate_path.read_text(encoding="utf-8")
    source_blocks = copper_blocks(source_text)
    candidate_selected = [block for _, _, net, block in copper_blocks(candidate_text) if net in selected]
    remove_spans = [(start, end) for start, end, net, _ in source_blocks if net in selected]
    insertion_at = min((start for start, _, _, _ in source_blocks), default=-1)
    if insertion_at < 0:
        zone_match = re.search(r"(?m)^\t\(zone\b", source_text)
        insertion_at = zone_match.start() + 1 if zone_match else -1
    if insertion_at < 0:
        insertion_at = source_text.rfind(")")

    pieces = []
    cursor = 0
    adjusted_insertion = insertion_at
    removed_before_insertion = 0
    for start, end in remove_spans:
        pieces.append(source_text[cursor:start])
        if end <= insertion_at:
            removed_before_insertion += end - start
        cursor = end
    pieces.append(source_text[cursor:])
    stripped = "".join(pieces)
    adjusted_insertion -= removed_before_insertion
    restored_text = (
        stripped[:adjusted_insertion]
        + "\n\t" + "\n\t".join(candidate_selected) + "\n\t"
        + stripped[adjusted_insertion:]
    )
    candidate_path.write_text(restored_text, encoding="utf-8", newline="\n")
    print(json.dumps({
        "success": True,
        "candidate_sha256_before": before_hash,
        "candidate_sha256_after": sha256(candidate_path),
        "removed_selected_input_blocks": len(remove_spans),
        "inserted_selected_candidate_blocks": len(candidate_selected),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
