#!/usr/bin/env python3
"""Map a live manufacturing quote to reviewed cost drivers before ordering."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from engine_runtime import activate

activate()

from pcb_template_engine import API_VERSION, __version__  # noqa: E402
from pcb_template_engine.manufacturing import evaluate_quote_observation, validate_registry  # noqa: E402
from design_policy import DEFAULT_OUTPUT, MANUFACTURING_REGISTRY_ARTIFACT, require_policy  # noqa: E402
from manufacturing_work_packet import sha256, verify_work_packet  # noqa: E402


def load(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quote", type=Path, required=True, help="Compact JSON observation copied from the live order quote")
    parser.add_argument("--work-packet", type=Path, required=True, help="Exact DFM/manufacturing work-packet manifest used for this quote")
    parser.add_argument("--max-age-hours", type=float, default=48.0, help="Reject older live quotes (default: 48 hours)")
    parser.add_argument("--registry", type=Path, default=MANUFACTURING_REGISTRY_ARTIFACT)
    parser.add_argument("--design-policy", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    policy = require_policy("release", args.design_policy.resolve())
    registry = load(args.registry.resolve(), "Manufacturing registry")
    validate_registry(registry, policy, engine_version=__version__, engine_api_version=API_VERSION)
    observation = load(args.quote.resolve(), "Manufacturing quote observation")
    work_packet_path = args.work_packet.resolve()
    work_packet, _ = verify_work_packet(work_packet_path)
    wrapper_problems: list[str] = []
    if observation.get("work_packet_content_sha256") != work_packet.get("content_sha256"):
        wrapper_problems.append("quote_work_packet_content_hash_mismatch")
    source = observation.get("source")
    if not isinstance(source, dict) or str(source.get("provider", "")).strip().lower() != str(work_packet.get("vendor", "")).lower():
        wrapper_problems.append("quote_provider_does_not_match_work_packet")
    if observation.get("currency") != work_packet.get("quote_currency"):
        wrapper_problems.append("quote_currency_does_not_match_work_packet")
    quoted_artifacts = observation.get("artifacts")
    if isinstance(quoted_artifacts, dict):
        for role in ("board", "gerber_zip", "bom", "cpl"):
            quoted = quoted_artifacts.get(role)
            expected = work_packet["files"][role]
            if not isinstance(quoted, dict) or quoted.get("sha256") != expected.get("sha256"):
                wrapper_problems.append(f"quote_artifact_mismatch:{role}")
    if observation.get("order_parameters") != work_packet.get("order_parameters"):
        wrapper_problems.append("quote_order_parameters_do_not_match_work_packet")
    try:
        observed_at = datetime.fromisoformat(str(observation.get("observed_at", "")).replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("timezone is required")
        age_hours = (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours < -0.25:
            wrapper_problems.append("quote_observed_at_is_in_the_future")
        elif age_hours > args.max_age_hours:
            wrapper_problems.append("quote_is_stale")
    except ValueError:
        wrapper_problems.append("quote_observed_at_is_not_timezone_aware_iso8601")
        age_hours = None
    expires_at_raw = observation.get("expires_at")
    if expires_at_raw is not None:
        try:
            expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                raise ValueError("timezone is required")
            if datetime.now(timezone.utc) >= expires_at.astimezone(timezone.utc):
                wrapper_problems.append("quote_has_expired")
            if age_hours is not None and expires_at <= observed_at:
                wrapper_problems.append("quote_expiry_is_not_after_observation")
        except ValueError:
            wrapper_problems.append("quote_expires_at_is_not_timezone_aware_iso8601")
    result = evaluate_quote_observation(registry, observation)
    result["work_packet_manifest"] = str(work_packet_path)
    result["work_packet_manifest_sha256"] = sha256(work_packet_path)
    result["quote_observation_sha256"] = sha256(args.quote.resolve())
    result["quote_age_hours"] = age_hours
    result["wrapper_problems"] = sorted(set(wrapper_problems))
    if wrapper_problems:
        result["success"] = False
        result["manufacturing_quote_review_pass"] = False
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "success": result["success"],
        "kind": result["kind"],
        "currency": result["currency"],
        "total": result["total"],
        "approval_pass": result["approval_pass"],
        "binding_pass": result["binding_pass"] and not wrapper_problems,
        "binding_problems": result["binding_problems"] + result["wrapper_problems"],
        "unexpected_lines": result["unexpected_lines"],
        "output": str(output),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["success"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
