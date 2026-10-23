#!/usr/bin/env python3
"""Emit compact remaining-connectivity or pre-route release-debt summaries."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from krt_candidate_audit import DEFAULT_KICAD_CLI, drc_context_key, run_drc


from pcb_runtime import kicad_python, ensure_kicad_python as ensure_host_kicad_python

ROOT = Path(__file__).resolve().parents[1]
KICAD_PYTHON = kicad_python()
CONNECTIVITY = {"unconnected_items"}
PRESENTATION = {"silk_edge_clearance", "silk_over_copper", "silk_overlap"}
IDENTITY = {"lib_footprint_mismatch", "lib_footprint_issues", "missing_footprint"}
COPPER_FATAL = {
    "shorting_items", "clearance", "tracks_crossing", "track_dangling",
    "via_dangling", "hole_clearance", "copper_edge_clearance",
}


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_pcbnew() -> object:
    try:
        import pcbnew  # type: ignore
    except ImportError:
        ensure_host_kicad_python()
        import pcbnew
    return pcbnew


def board_regions(board: Path) -> tuple[list[str], dict[str, str]]:
    pcbnew = load_pcbnew()
    value = pcbnew.LoadBoard(str(board))
    refs: list[str] = []
    regions: dict[str, str] = {}
    for footprint in value.GetFootprints():
        ref = str(footprint.GetReference())
        refs.append(ref)
        fields = {str(field.GetName()): str(field.GetText()) for field in footprint.GetFields()}
        address = fields.get("atopile_address", "")
        regions[ref] = address.split(".", 1)[0] if "." in address else address or "unclassified"
    return sorted(refs, key=len, reverse=True), regions


def referenced_refs(item: dict[str, object], known_refs: list[str]) -> list[str]:
    text = " ".join(str(value.get("description", "")) for value in item.get("items", []) if isinstance(value, dict))
    return sorted({ref for ref in known_refs if re.search(rf"(?<![A-Za-z0-9]){re.escape(ref)}(?![A-Za-z0-9])", text)})


def referenced_nets(item: dict[str, object]) -> list[str]:
    text = " ".join(str(value.get("description", "")) for value in item.get("items", []) if isinstance(value, dict))
    return sorted({name for name in re.findall(r"\[([^\]]+)\]", text) if name})


def remaining_summary(report: dict[str, object], refs: list[str], regions: dict[str, str]) -> dict[str, object]:
    groups: dict[tuple[str, ...], dict[str, object]] = {}
    net_counts: Counter[str] = Counter()
    items = list(report.get("unconnected_items", []))
    for item in items:
        if not isinstance(item, dict):
            continue
        item_refs = referenced_refs(item, refs)
        item_nets = referenced_nets(item)
        net_counts.update(item_nets or ["unknown"])
        region_key = tuple(sorted({regions.get(ref, "unclassified") for ref in item_refs}) or ["unclassified"])
        group = groups.setdefault(region_key, {"regions": list(region_key), "refs": set(), "nets": set(), "count": 0})
        group["refs"].update(item_refs)
        group["nets"].update(item_nets)
        group["count"] += 1
    normalized = [
        {"regions": value["regions"], "refs": sorted(value["refs"]), "nets": sorted(value["nets"]), "count": value["count"]}
        for value in groups.values()
    ]
    normalized.sort(key=lambda value: (-int(value["count"]), value["regions"]))
    return {"count": len(items), "by_net": dict(sorted(net_counts.items())), "groups": normalized[:12]}


def pre_route_summary(
    report: dict[str, object], ownership: dict[str, object] | None = None,
    refs: list[str] | None = None, regions: dict[str, str] | None = None,
) -> dict[str, object]:
    counts = Counter(str(item.get("type", "unknown")) for item in report.get("violations", []) if isinstance(item, dict))
    buckets: dict[str, dict[str, int]] = defaultdict(dict)
    for category, count in sorted(counts.items()):
        bucket = "copper" if category in COPPER_FATAL else "identity" if category in IDENTITY else "presentation" if category in PRESENTATION else "other"
        buckets[bucket][category] = count
    debt = sum(counts.values())
    category_policy = ownership.get("categories", {}) if isinstance(ownership, dict) else {}
    if not isinstance(category_policy, dict):
        category_policy = {}
    dispositions: dict[str, str] = {}
    unowned: list[str] = []
    invalid: list[str] = []
    for category in sorted(counts):
        entry = category_policy.get(category)
        disposition = entry.get("disposition") if isinstance(entry, dict) else None
        if disposition not in {"fix_before_routing", "fix_before_release"}:
            unowned.append(category)
            if disposition is not None:
                invalid.append(category)
        else:
            dispositions[category] = str(disposition)
    routing_blockers = sorted(name for name, value in dispositions.items() if value == "fix_before_routing")
    ready = not unowned and not routing_blockers
    affected: dict[str, Counter[str]] = defaultdict(Counter)
    unattributed: Counter[str] = Counter()
    if refs is not None:
        for item in report.get("violations", []):
            if not isinstance(item, dict):
                continue
            category = str(item.get("type", "unknown"))
            item_refs = referenced_refs(item, refs)
            if item_refs:
                affected[category].update(item_refs)
            else:
                unattributed[category] += 1
    return {
        "release_debt_count": debt,
        "categories": dict(sorted(counts.items())),
        "buckets": {name: value for name, value in sorted(buckets.items())},
        "ownership": dispositions,
        "unowned_categories": unowned,
        "invalid_ownership_categories": invalid,
        "routing_blocker_categories": routing_blockers,
        "affected_refs": {
            category: dict(sorted(values.items(), key=lambda pair: (-pair[1], pair[0]))[:20])
            for category, values in sorted(affected.items())
        },
        "unattributed_counts": dict(sorted(unattributed.items())),
        "ready_for_routing": ready,
        "release_cleanup_required": debt > 0,
        "next_action": (
            "route" if ready and debt == 0
            else "route_with_release_debt_owned" if ready
            else "classify_or_resolve_release_debt_before_broad_routing"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--drc-json", type=Path)
    parser.add_argument("--mode", choices=("remaining", "pre-route"), default="remaining")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "build/06-routing/tmp/drc-cache")
    parser.add_argument("--kicad-cli", type=Path, default=DEFAULT_KICAD_CLI)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ownership", type=Path, default=ROOT / "config/release-debt-policy.json")
    args = parser.parse_args()
    board = args.board.resolve()
    if not board.is_file():
        raise SystemExit(f"Board not found: {board}")
    if args.drc_json:
        drc_path = args.drc_json.resolve()
        report = json.loads(drc_path.read_text(encoding="utf-8"))
        cache_hit = True
    else:
        key, _, _ = drc_context_key(args.kicad_cli.resolve(), board)
        drc_path = args.cache_dir.resolve() / f"board-{key}.drc.json"
        report, cache_hit = run_drc(args.kicad_cli.resolve(), board, drc_path)
    refs, regions = board_regions(board)
    result = {
        "schema": 1, "kind": "kicad_drc_summary", "success": True,
        "mode": args.mode, "board": str(board),
        "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest().upper(),
        "drc_json": str(drc_path), "drc_sha256": hashlib.sha256(drc_path.read_bytes()).hexdigest().upper(),
        "drc_cache_hit": cache_hit,
        "unconnected": remaining_summary(report, refs, regions),
    }
    if args.mode == "pre-route":
        ownership = (
            json.loads(args.ownership.resolve().read_text(encoding="utf-8"))
            if args.ownership and args.ownership.resolve().is_file() else None
        )
        result["pre_route"] = pre_route_summary(report, ownership, refs, regions)
        result["ownership_policy"] = str(args.ownership.resolve()) if ownership is not None else None
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["output"] = str(output)
    print(compact(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
