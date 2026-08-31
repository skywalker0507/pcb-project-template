#!/usr/bin/env python3
"""Import a structured review of a vendor DFM report and bind it to exact inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine_runtime import activate

activate()

from pcb_template_engine import API_VERSION, __version__  # noqa: E402
from pcb_template_engine.manufacturing import evaluate_dfm_observation, validate_registry  # noqa: E402
from design_policy import DEFAULT_OUTPUT, MANUFACTURING_REGISTRY_ARTIFACT, require_policy  # noqa: E402
from manufacturing_work_packet import load_object, sha256, verify_work_packet  # noqa: E402
from release_inputs import register_release_input  # noqa: E402


def validate_review(observation: dict[str, object], expected_packet_hash: str) -> list[str]:
    problems: list[str] = []
    if observation.get("schema") != 2 or observation.get("kind") != "manufacturing_dfm_observation":
        raise SystemExit("DFM observation must use schema 2 and kind manufacturing_dfm_observation")
    if observation.get("work_packet_content_sha256") != expected_packet_hash:
        problems.append("work_packet_content_sha256_mismatch")
    review = observation.get("review")
    if not isinstance(review, dict) or review.get("status") != "confirmed":
        problems.append("review_not_confirmed")
    elif not isinstance(review.get("reviewer"), str) or not str(review.get("reviewer")).strip():
        problems.append("reviewer_missing")
    summary = observation.get("findings_summary")
    if not isinstance(summary, dict):
        problems.append("findings_summary_missing")
        return problems
    for field in ("blocking_findings", "unresolved_items", "accepted_exceptions"):
        if not isinstance(summary.get(field), list):
            problems.append(f"{field}_invalid")
    for field in ("blocking_findings", "unresolved_items"):
        if isinstance(summary.get(field), list) and summary[field]:
            problems.append(f"{field}_remain")
    exceptions = summary.get("accepted_exceptions")
    if isinstance(exceptions, list):
        for index, item in enumerate(exceptions):
            if not isinstance(item, dict) or not all(
                isinstance(item.get(field), str) and str(item[field]).strip()
                for field in ("rule", "scope", "reason")
            ):
                problems.append(f"accepted_exception_{index}_lacks_rule_scope_or_reason")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-packet", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=MANUFACTURING_REGISTRY_ARTIFACT)
    parser.add_argument("--design-policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = args.report.resolve()
    if not report.is_file():
        raise SystemExit(f"Vendor DFM report is unavailable: {report}")
    work_packet_path = args.work_packet.resolve()
    work_packet, files = verify_work_packet(work_packet_path)
    observation_path = args.observation.resolve()
    observation = load_object(observation_path, "Structured DFM observation")
    review_problems = validate_review(observation, str(work_packet["content_sha256"]))
    if observation.get("order_parameters") != work_packet.get("order_parameters"):
        review_problems.append("observation_order_parameters_do_not_match_work_packet")

    policy = require_policy("release", args.design_policy.resolve())
    registry = load_object(args.registry.resolve(), "Manufacturing registry")
    validate_registry(registry, policy, engine_version=__version__, engine_api_version=API_VERSION)
    registry_gate = evaluate_dfm_observation(registry, observation)
    success = registry_gate["success"] is True and not review_problems
    summary = observation.get("findings_summary")
    receipt = {
        "schema": 3,
        "kind": "manufacturing_dfm",
        "success": success,
        "dfm_pass": success,
        "backend": work_packet["vendor"],
        "board_sha256": work_packet["files"]["board"]["sha256"],
        "gerber_zip_sha256": work_packet["files"]["gerber_zip"]["sha256"],
        "bom_sha256": work_packet["files"]["bom"]["sha256"],
        "cpl_sha256": work_packet["files"]["cpl"]["sha256"],
        "work_packet_manifest": str(work_packet_path),
        "work_packet_manifest_sha256": sha256(work_packet_path),
        "work_packet_content_sha256": work_packet["content_sha256"],
        "report": str(report),
        "report_sha256": sha256(report),
        "observation": str(observation_path),
        "observation_sha256": sha256(observation_path),
        "extraction_method": observation.get("review", {}).get("method") if isinstance(observation.get("review"), dict) else None,
        "order_parameters": observation.get("order_parameters"),
        "analysis_modules": observation.get("analysis_modules"),
        "findings_summary": summary,
        "review": observation.get("review"),
        "review_problems": sorted(set(review_problems)),
        "policy_sha256": registry_gate["policy_sha256"],
        "registry_sha256": registry_gate["registry_sha256"],
        "registry_gate": registry_gate,
        "limits": "The original report remains opaque evidence. The structured review must disclose every accepted exception and leave no blocking or unresolved finding.",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if success:
        register_release_input("jlc_dfm", output, receipt)
    print(json.dumps({
        "success": success,
        "dfm_pass": success,
        "board_sha256": receipt["board_sha256"],
        "report_sha256": receipt["report_sha256"],
        "accepted_exception_count": len(summary.get("accepted_exceptions", [])) if isinstance(summary, dict) else None,
        "review_problems": receipt["review_problems"],
        "mismatches": registry_gate["mismatches"],
        "output": str(output),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
