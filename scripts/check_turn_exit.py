#!/usr/bin/env python3
"""Refuse a premature final response during continuous PCB work."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGE = ROOT / "handoff/stages/CURRENT_STAGE.json"
DEFAULT_POLICY = ROOT / "config/execution-policy.json"


def load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Invalid {label}: {path} must contain a JSON object")
    return value


def nonempty_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def completion_evidence_issues(stage: dict[str, object]) -> list[str]:
    if stage.get("completion_verified") is not True:
        return ["stage_completion_is_not_evidence_verified"]
    board_sha = stage.get("board_sha256")
    if not isinstance(board_sha, str) or not board_sha:
        return ["stage_completion_board_sha256_is_missing"]
    board_value = stage.get("board")
    if not isinstance(board_value, str) or not board_value:
        return ["stage_completion_board_is_missing"]
    board = Path(board_value)
    board = board.resolve() if board.is_absolute() else (ROOT / board).resolve()
    if not board.is_file() or hashlib.sha256(board.read_bytes()).hexdigest().upper() != board_sha:
        return ["stage_completion_board_is_missing_or_stale"]
    basis = stage.get("completion_basis")
    if basis == "pipeline":
        pipeline = stage.get("pipeline")
        if not isinstance(pipeline, dict) or pipeline.get("success") is not True or pipeline.get("board_sha256") != board_sha:
            return ["stage_completion_pipeline_is_invalid"]
        if stage.get("stage") == "release" and pipeline.get("release_pass") is not True:
            return ["stage_release_pipeline_did_not_pass_all_gates"]
        return []
    if basis == "receipts":
        receipts = stage.get("completion_receipts")
        if not isinstance(receipts, list) or not receipts:
            return ["stage_completion_receipts_are_missing"]
        for item in receipts:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                return ["stage_completion_receipt_record_is_invalid"]
            path = Path(item["path"])
            path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest().upper() != item.get("sha256"):
                return ["stage_completion_receipt_is_missing_or_stale"]
        return []
    return ["stage_completion_basis_is_missing"]


def evaluate(
    stage: dict[str, object],
    policy: dict[str, object],
    reason: str,
    detail: str = "",
    material_decision: bool = False,
) -> dict[str, object]:
    status = str(stage.get("status") or "")
    next_actions = nonempty_strings(stage.get("next_actions"))
    final_reasons = policy.get("final_reasons")
    if not isinstance(final_reasons, dict) or not isinstance(final_reasons.get(reason), dict):
        return {
            "final_allowed": False,
            "continue_required": True,
            "reason": reason,
            "issues": ["unknown_final_reason"],
            "next_actions": next_actions[:8],
        }

    rule = final_reasons[reason]
    issues: list[str] = []
    if reason == "stage_complete":
        statuses = nonempty_strings(rule.get("statuses"))
        if status not in statuses:
            issues.append(f"stage_status_is_{status or 'missing'}")
        if rule.get("requires_empty_next_actions") is True and next_actions:
            issues.append("remaining_next_actions")
        if rule.get("requires_verified_handoff") is True:
            issues.extend(completion_evidence_issues(stage))
    elif reason == "blocked":
        statuses = nonempty_strings(rule.get("statuses"))
        if status not in statuses:
            issues.append(f"stage_status_is_{status or 'missing'}")
        evidence = stage.get("blocking_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
            issues.append("missing_blocking_evidence")
        minimum = int(rule.get("minimum_observations", 3))
        observations = evidence.get("observations", 0)
        if not isinstance(observations, int) or observations < minimum:
            issues.append(f"blocking_observations_below_{minimum}")
        if rule.get("requires_safe_alternatives_exhausted") is True and evidence.get("safe_alternatives_exhausted") is not True:
            issues.append("safe_alternatives_not_exhausted")
        for field in nonempty_strings(rule.get("required_evidence_fields")):
            value = evidence.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(f"missing_blocking_evidence_{field}")
    elif reason == "user_decision":
        if rule.get("requires_material_decision_flag") is True and not material_decision:
            issues.append("material_decision_not_confirmed")
        if rule.get("requires_detail") is True and not detail.strip():
            issues.append("missing_decision_detail")
    elif reason == "user_stopped":
        if rule.get("requires_detail") is True and not detail.strip():
            issues.append("missing_user_stop_detail")

    allowed = not issues
    instruction = (
        "Final response is allowed for the recorded terminal reason."
        if allowed
        else "Do not send a final response; continue with the next safe in-scope action."
    )
    return {
        "final_allowed": allowed,
        "continue_required": not allowed,
        "reason": reason,
        "stage": stage.get("stage"),
        "status": status,
        "objective": stage.get("objective"),
        "issues": issues,
        "next_actions": next_actions[:8],
        "instruction": instruction,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--continuous-scope",
        action="store_true",
        help="Activate the whole-stage completion gate after an explicit user request to finish the current stage.",
    )
    parser.add_argument(
        "--reason",
        choices=("stage_complete", "blocked", "user_decision", "user_stopped"),
        default="stage_complete",
    )
    parser.add_argument("--detail", default="")
    parser.add_argument("--material-decision", action="store_true")
    parser.add_argument("--stage-file", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()

    if not args.continuous_scope:
        print(json.dumps({
            "gate_active": False,
            "final_allowed": True,
            "continue_required": False,
            "instruction": "Whole-stage completion scope was not explicitly activated; judge completion against the bounded user request.",
        }, ensure_ascii=False, separators=(",", ":")))
        return 0

    result = evaluate(
        load_object(args.stage_file.resolve(), "stage handoff"),
        load_object(args.policy.resolve(), "execution policy"),
        args.reason,
        args.detail,
        args.material_decision,
    )
    result["gate_active"] = True
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["final_allowed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
