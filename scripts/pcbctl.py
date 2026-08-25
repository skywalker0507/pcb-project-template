#!/usr/bin/env python3
"""Thin, stable command surface over the existing PCB workflow scripts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# This table only dispatches. Validation, artifacts, guards and release policy
# remain implemented by the mature scripts below.
COMMANDS: dict[str, tuple[str, ...]] = {
    "status": ("scripts/session_boot.py",),
    "build": ("scripts/build_electrical.py",),
    "freeze": ("scripts/create_electrical_freeze.py",),
    "physical-start": ("scripts/prepare_kicad_physical_candidate.py", "--activate"),
    "place": ("scripts/apply_kicad_placement_plan.py",),
    "route": ("scripts/krt_flow.py",),
    "verify": ("scripts/run_pcb_pipeline.py",),
    "release": ("scripts/run_pcb_pipeline.py", "--export"),
}

# Subcommands keep the top-level surface small while still reaching reviewed
# deterministic scripts. They only dispatch; policy remains in the target.
SUBCOMMANDS: dict[str, dict[str, tuple[str, ...]]] = {
    "physical-start": {
        "create": ("scripts/prepare_kicad_physical_candidate.py", "--activate"),
        "rebase": ("scripts/rebase_kicad_physical_candidate.py",),
        "outline": ("scripts/apply_kicad_candidate_outline.py",),
        "stack": ("scripts/apply_kicad_stack_plan.py",),
        "plane": ("scripts/apply_kicad_plane_plan.py",),
        "silkscreen": ("scripts/apply_kicad_silkscreen_plan.py",),
    },
    "place": {
        "apply": ("scripts/apply_kicad_placement_plan.py",),
        "query": ("scripts/query_pcb_geometry.py",),
        "score": ("scripts/check_kicad_placement_routability.py",),
        "semantics": ("scripts/check_kicad_placement_semantics.py",),
        "immutability": ("scripts/check_kicad_placement_immutability.py",),
        "integrity": ("scripts/check_kicad_physical_integrity.py", "--phase", "placement"),
        "render": ("scripts/render_kicad_visual_bundle.py",),
        "review": ("scripts/record_pcb_visual_review.py",),
        "promote": ("scripts/promote_kicad_physical_candidate.py",),
    },
    "verify": {
        "readiness": ("scripts/check_release_readiness.py",),
        "parity": ("scripts/refresh_final_electrical_parity.py",),
        "prepare-review": ("scripts/prepare_release_review.py",),
        "finalize-review": ("scripts/finalize_release_review.py",),
        "delta": ("scripts/krt_candidate_audit.py",),
        "remaining": ("scripts/summarize_kicad_drc.py", "--mode", "remaining"),
        "pre-route": ("scripts/summarize_kicad_drc.py", "--mode", "pre-route"),
        "footprint-equivalence": ("scripts/check_kicad_footprint_equivalence.py",),
        "drc-review": ("scripts/record_kicad_drc_review.py",),
    },
    "route": {
        "run": ("scripts/krt_flow.py", "route"),
        "start": ("scripts/krt_checkpoint.py", "start"),
        "status": ("scripts/krt_checkpoint.py", "status"),
        "finalize": ("scripts/krt_checkpoint.py", "finalize"),
        "health": ("scripts/krt_flow.py", "health"),
        "escalate": ("scripts/assess_routing_escalation.py",),
        "trace": ("scripts/apply_kicad_trace_plan.py",),
        "return-vias": ("scripts/summarize_kicad_return_vias.py",),
        "checkpoint-finalize": ("scripts/finalize_routing_checkpoint.py",),
    },
    "release": {
        "prepare-dfm": ("scripts/prepare_jlcpcb_dfm_candidate.py",),
        "record-dfm": ("scripts/record_manufacturing_dfm.py",),
        "check-manufacturing": ("scripts/check_manufacturing_registry.py",),
        "check-gerber": ("scripts/check_manufacturing_registry.py", "--phase", "gerber"),
        "confirm-process": ("scripts/confirm_special_process.py",),
    },
}

COMMAND_HELP = {
    "status": "current stage and next action",
    "build": "isolated electrical candidate",
    "freeze": "immutable electrical handoff",
    "physical-start": "writable KiCad candidate from immutable freeze",
    "place": "bounded SHA-guarded KiCad placement plan",
    "route": "guarded KRT candidate flow",
    "verify": "checkpoint DRC/connectivity (use --tier release only at a checkpoint)",
    "release": "guarded manufacturing export",
}


def usage() -> str:
    lines = ["usage: pcbctl.py <command> [arguments forwarded unchanged]", "commands:"]
    lines.extend(f"  {name:<14} {COMMAND_HELP[name]}" for name in COMMANDS)
    lines.extend([
        "subcommands:",
        "  physical-start  create | rebase | outline | stack | plane | silkscreen",
        "  place           apply | query | score | semantics | immutability | integrity | render | review | promote",
        "  route           run | start | status | finalize | check | report | history | health | escalate | trace | return-vias | checkpoint-finalize",
        "  verify          readiness | parity | prepare-review | finalize-review | delta | remaining | pre-route | footprint-equivalence | drc-review",
        "  release         prepare-dfm (Gerber+BOM+CPL) | record-dfm | confirm-process | check-manufacturing | check-gerber",
    ])
    return "\n".join(lines)


def command_argv(command: str, arguments: list[str]) -> list[str]:
    target = COMMANDS.get(command)
    if target is None:
        raise ValueError(f"unknown command: {command}")
    forwarded = list(arguments)
    if forwarded:
        nested = SUBCOMMANDS.get(command, {}).get(forwarded[0])
        if nested is not None:
            target = nested
            forwarded.pop(0)
        elif command in {"physical-start", "place", "verify", "release"} and not forwarded[0].startswith("-"):
            raise ValueError(f"unknown {command} subcommand: {forwarded[0]}")
    return [sys.executable, *target, *forwarded]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0
    command = args.pop(0)
    try:
        dispatched = command_argv(command, args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2
    return subprocess.run(dispatched, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
