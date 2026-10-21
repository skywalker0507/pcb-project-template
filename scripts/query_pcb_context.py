#!/usr/bin/env python3
"""Return a small PCB-context slice for the nets/groups currently under review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_store import config as artifact_config

ROOT = Path(__file__).resolve().parents[1]


def latest_context_pair() -> tuple[Path, Path]:
    """Find the newest run that has both compact context and matching audit evidence."""
    candidates: list[tuple[float, Path, Path]] = []
    for stage in (ROOT / "build").glob("[0-9][0-9]-*"):
        evidence_root = stage / "evidence"
        tmp_root = stage / "tmp"
        if not evidence_root.is_dir() or not tmp_root.is_dir():
            continue
        for evidence in evidence_root.iterdir():
            audit = evidence / "final-routing-audit.json"
            context = tmp_root / evidence.name / "context/pcb-context.json"
            if audit.is_file() and context.is_file():
                candidates.append((max(audit.stat().st_mtime, context.stat().st_mtime), context, audit))
    if candidates:
        _, context, audit = max(candidates, key=lambda item: item[0])
        return context, audit
    return ROOT / "build/07-release/tmp/manual/context/pcb-context.json", ROOT / "build/07-release/evidence/manual/final-routing-audit.json"


def main() -> int:
    budget = int(artifact_config().get("context_budget", {}).get("max_context_slice_bytes", 6000))
    default_context, default_audit = latest_context_pair()
    parser = argparse.ArgumentParser()
    parser.add_argument("--net", action="append", default=[])
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--top-routes", type=int, default=5)
    parser.add_argument("--top-hpwl", type=int, default=5)
    parser.add_argument(
        "--max-output-bytes", type=int, default=budget,
        help="Hard cap for a context slice that may be shown to an LLM.",
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=default_context,
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=default_audit,
    )
    args = parser.parse_args()

    context = json.loads(args.context.read_text(encoding="utf-8"))
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    if context.get("board_sha256") != audit.get("board_sha256"):
        raise SystemExit(
            "Context and routing audit are from different board revisions; rerun the PCB pipeline"
        )
    requested_nets = set(args.net)
    requested_groups = set(args.group)
    requested_refs = set(args.ref)

    nets = {
        item["name"]: item
        for item in context["nets"]
        if item["name"] in requested_nets
    }
    routes = {
        item["net"]: item
        for item in audit["routed_net_metrics"]
        if item["net"] in requested_nets
    }
    groups = [
        item
        for item in context["functional_groups"]
        if item["name"] in requested_groups
    ]
    group_refs = {ref for item in groups for ref in item["refs"]}
    net_refs = {
        endpoint["ref"]
        for item in nets.values()
        for endpoint in item["endpoints"]
    }
    component_summary = {
        item["ref"]: {
            "ref": item["ref"],
            "x_mm": item["x_mm"],
            "y_mm": item["y_mm"],
            "rotation_deg": item["rotation_deg"],
            "side": item["side"],
            "design_source_address": item["design_source_address"],
        }
        for item in context["components"]
    }
    components = [
        {
            "ref": item["ref"],
            "x_mm": item["x_mm"],
            "y_mm": item["y_mm"],
            "rotation_deg": item["rotation_deg"],
            "side": item["side"],
            "design_source_address": item["design_source_address"],
        }
        for item in context["components"]
        if item["ref"] in group_refs
    ]

    result = {
        "board_sha256": context["board_sha256"],
        "selected_nets": [
            {**item, "route": routes.get(name)}
            for name, item in sorted(nets.items())
        ],
        "selected_net_components": [
            component_summary[ref]
            for ref in sorted(net_refs)
            if ref in component_summary
        ],
        "selected_components": [
            item
            for item in context["components"]
            if item["ref"] in requested_refs
        ],
        "selected_groups": groups,
        "selected_group_components": components,
        "top_routes": audit["longest_routed_nets"][: max(args.top_routes, 0)],
        "top_signal_hpwl": context["highest_hpwl_signal_nets"][: max(args.top_hpwl, 0)],
        "limits": [
            "HPWL is placement triage, not route or signal-integrity approval.",
            "No numeric clearance, width, impedance or length rule is inferred by this report.",
        ],
    }
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > args.max_output_bytes:
        raise SystemExit(
            "Context slice exceeds the output cap; request fewer nets/groups/refs or lower top-* values"
        )
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
