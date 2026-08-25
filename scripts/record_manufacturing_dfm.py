#!/usr/bin/env python3
"""Record a compact, hash-bound DFM observation and evaluate its reviewed scope."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from engine_runtime import activate

activate()

from pcb_template_engine import API_VERSION, __version__  # noqa: E402
from pcb_template_engine.manufacturing import evaluate_dfm_observation, validate_registry  # noqa: E402
from design_policy import DEFAULT_OUTPUT, MANUFACTURING_REGISTRY_ARTIFACT, require_policy  # noqa: E402
from release_inputs import register_release_input  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def module_result(value: str) -> dict[str, object]:
    return {
        "executed": value != "not_run",
        "pass": value == "pass",
        "reported_result": value,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--gerber-zip", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--surface-finish", required=True)
    parser.add_argument("--solder-mask-color", required=True)
    parser.add_argument("--silkscreen-color")
    parser.add_argument("--pcb-dfm", choices=("pass", "fail", "not_run"), required=True)
    parser.add_argument("--smt-dfm", choices=("pass", "fail", "not_run"), required=True)
    parser.add_argument("--extraction-method", default="operator_recorded_from_report")
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--registry", type=Path, default=MANUFACTURING_REGISTRY_ARTIFACT)
    parser.add_argument("--design-policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = {name: value.resolve() for name, value in {
        "board": args.board, "gerber_zip": args.gerber_zip, "report": args.report,
    }.items()}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise SystemExit("DFM evidence is missing: " + ", ".join(missing))
    policy = require_policy("release", args.design_policy.resolve())
    registry = load(args.registry.resolve(), "Manufacturing registry")
    validate_registry(registry, policy, engine_version=__version__, engine_api_version=API_VERSION)
    observation = {
        "order_parameters": {
            "surface_finish": args.surface_finish,
            "solder_mask_color": args.solder_mask_color.lower(),
            "silkscreen_color": args.silkscreen_color.lower() if args.silkscreen_color else None,
        },
        "analysis_modules": {
            "pcb": module_result(args.pcb_dfm),
            "smt": module_result(args.smt_dfm),
        },
    }
    gate = evaluate_dfm_observation(registry, observation)
    receipt = {
        "schema": 2,
        "kind": "manufacturing_dfm",
        "success": gate["success"],
        "dfm_pass": gate["dfm_pass"],
        "backend": "jlcpcb",
        "board_sha256": sha256(paths["board"]),
        "gerber_zip_sha256": sha256(paths["gerber_zip"]),
        "report": str(paths["report"]),
        "report_sha256": sha256(paths["report"]),
        "extraction_method": args.extraction_method,
        **observation,
        "policy_sha256": gate["policy_sha256"],
        "registry_sha256": gate["registry_sha256"],
        "registry_gate": gate,
        "notes": [str(item) for item in args.note if str(item).strip()],
        "limits": "A PCB DFM geometry result cannot substitute for missing SMT DFM, order-parameter mismatch, or separately configured special-process confirmation.",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_release_input("jlc_dfm", output, receipt)
    print(json.dumps({
        "success": receipt["success"],
        "dfm_pass": receipt["dfm_pass"],
        "board_sha256": receipt["board_sha256"],
        "report_sha256": receipt["report_sha256"],
        "mismatches": gate["mismatches"],
        "output": str(output),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if receipt["dfm_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
