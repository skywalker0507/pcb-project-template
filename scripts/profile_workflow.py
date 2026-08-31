#!/usr/bin/env python3
"""Profile workflow churn and storage without reading bulk PCB contents."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config/workflow-optimization.json"
DEFAULT_OUTPUT = ROOT / "build/00-shared/workflow-profile.json"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"Expected a JSON object: {path}")
    return value


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def filesystem_profile(build: Path) -> dict[str, Any]:
    by_stage: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
    by_lane: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
    by_suffix: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
    tmp_run_dirs = 0
    total_files = 0
    total_bytes = 0
    if not build.is_dir():
        return {"files": 0, "bytes": 0, "tmp_run_dirs": 0, "by_stage": [], "by_lane": [], "by_suffix": []}
    for base, directories, files in os.walk(build):
        base_path = Path(base)
        rel_parts = base_path.relative_to(build).parts
        if len(rel_parts) >= 2 and rel_parts[1] == "tmp":
            tmp_run_dirs += sum(1 for name in directories if base_path == build / rel_parts[0] / "tmp")
        for name in files:
            path = base_path / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            parts = path.relative_to(build).parts
            stage = parts[0] if parts else "root"
            lane = parts[1] if len(parts) > 1 else "root"
            suffix = path.suffix.lower() or "<none>"
            total_files += 1
            total_bytes += size
            for table, key in ((by_stage, stage), (by_lane, lane), (by_suffix, suffix)):
                table[key]["files"] += 1
                table[key]["bytes"] += size

    def ranked(values: dict[str, dict[str, int]], limit: int = 12) -> list[dict[str, Any]]:
        return [
            {"name": name, **counts}
            for name, counts in sorted(values.items(), key=lambda item: item[1]["bytes"], reverse=True)[:limit]
        ]

    return {
        "files": total_files,
        "bytes": total_bytes,
        "tmp_run_dirs": tmp_run_dirs,
        "by_stage": ranked(by_stage),
        "by_lane": ranked(by_lane),
        "by_suffix": ranked(by_suffix),
    }


def artifact_run_profile(build: Path) -> dict[str, Any]:
    operations: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    stale_by_operation: Counter[str] = Counter()
    now = datetime.now(timezone.utc)
    for path in build.glob("*/evidence/*/run.json"):
        try:
            value = load_object(path)
            started = datetime.fromisoformat(str(value["created_at"]))
            finished = datetime.fromisoformat(str(value["updated_at"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        status = value.get("status")
        operation = str(value.get("operation", "unknown"))
        if status not in {"complete", "failed"}:
            if (now - finished).total_seconds() >= 24 * 3600:
                stale_by_operation[operation] += 1
            continue
        operations[operation].append((
            status == "complete" and value.get("success") is True,
            max(0.0, (finished - started).total_seconds()),
        ))
    rows = []
    for operation, values in operations.items():
        durations = [duration for _, duration in values]
        rows.append({
            "operation": operation,
            "runs": len(values),
            "failures": sum(not success for success, _ in values),
            "failure_rate": round(sum(not success for success, _ in values) / len(values), 3),
            "total_seconds": round(sum(durations), 3),
            "median_seconds": round(statistics.median(durations), 3),
            "p90_seconds": round(percentile(durations, 0.9), 3),
        })
    rows.sort(key=lambda item: item["total_seconds"], reverse=True)
    return {
        "runs": sum(len(values) for values in operations.values()),
        "operations": rows,
        "stale_in_progress_runs": sum(stale_by_operation.values()),
        "stale_in_progress_by_operation": dict(stale_by_operation.most_common()),
    }


def routing_profile(history_path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if history_path.is_file():
        for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    outcomes = Counter(str(item.get("outcome_class") or ("accepted" if item.get("success") else "rejected")) for item in rows)
    route_areas = Counter(str(item.get("route_area") or "legacy_unscoped") for item in rows)
    classified = [item for item in rows if item.get("outcome_class")]
    rejected = [item for item in classified if item.get("outcome_class") != "accepted"]
    recent = classified[-20:]
    recent_rejected = [item for item in recent if item.get("outcome_class") != "accepted"]
    return {
        "attempts": len(rows),
        "classified_attempts": len(classified),
        "rejection_rate": round(len(rejected) / len(classified), 3) if classified else 0.0,
        "recent_rejection_rate": round(len(recent_rejected) / len(recent), 3) if recent else 0.0,
        "outcomes": dict(outcomes),
        "top_route_areas": [{"route_area": name, "attempts": count} for name, count in route_areas.most_common(10)],
    }


def pipeline_profile(build: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    duplicate_keys: Counter[tuple[str, str]] = Counter()
    for path in build.glob("*/evidence/*/pipeline-summary.json"):
        try:
            value = load_object(path)
        except (OSError, json.JSONDecodeError):
            continue
        requested = value.get("requested") if isinstance(value.get("requested"), dict) else {}
        board_hash = value.get("board_sha256")
        tier = requested.get("tier")
        if isinstance(board_hash, str) and isinstance(tier, str):
            duplicate_keys[(board_hash, tier)] += 1
        rows.append(value)
    repeats = sorted((count for count in duplicate_keys.values()), reverse=True)
    return {
        "runs": len(rows),
        "same_board_tier_max_repeats": repeats[0] if repeats else 0,
        "same_board_tier_repeated_groups": sum(count > 1 for count in repeats),
        "recorded_log_bytes": sum(int((item.get("context_metrics") or {}).get("pipeline_log_bytes", 0)) for item in rows),
    }


def findings(profile: dict[str, Any], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    result: list[dict[str, Any]] = []
    mitigated: list[dict[str, Any]] = []
    implemented = policy.get("implemented_mitigations")
    implemented = implemented if isinstance(implemented, dict) else {}

    def record(item: dict[str, Any]) -> None:
        mitigation = implemented.get(item["code"])
        if isinstance(mitigation, dict) and mitigation.get("status") == "implemented":
            mitigated.append({**item, "status": "mitigated", "implemented_mitigation": mitigation})
        else:
            result.append(item)

    storage = profile["storage"]
    tmp = next((item for item in storage["by_lane"] if item["name"] == "tmp"), {"bytes": 0})
    if int(tmp["bytes"]) > int(thresholds.get("tmp_bytes_warning", 134_217_728)):
        record({
            "code": "expired_tmp_pressure", "priority": 20,
            "evidence": {"tmp_bytes": tmp["bytes"], "tmp_run_dirs": storage["tmp_run_dirs"]},
            "action": "preview_reference_safe_tmp_archive",
            "command": "py -3.14 scripts/artifact_store.py prune",
        })
    routing = profile["routing"]
    if routing["recent_rejection_rate"] > float(thresholds.get("recent_route_rejection_rate_warning", 0.35)):
        record({
            "code": "routing_candidate_churn", "priority": 10,
            "evidence": {"recent_rejection_rate": routing["recent_rejection_rate"]},
            "action": "diagnose_route_area_before_more_numeric_tuning",
        })
    pipeline = profile["pipeline"]
    if pipeline["same_board_tier_max_repeats"] > int(thresholds.get("same_board_tier_repeat_warning", 3)):
        record({
            "code": "repeated_full_board_verification", "priority": 15,
            "evidence": {"max_repeats": pipeline["same_board_tier_max_repeats"]},
            "action": "reuse_hash_bound_checkpoint_receipt_unless_inputs_changed",
            "mitigation": "pcbctl verify --reuse-checkpoint <passing-pipeline-summary.json>",
        })
    minimum_runs = int(thresholds.get("operation_failure_rate_minimum_runs", 5))
    failure_limit = float(thresholds.get("operation_failure_rate_warning", 0.5))
    failure_overrides = thresholds.get("operation_failure_rate_overrides", {})
    high_failure = [
        {"operation": item["operation"], "runs": item["runs"], "failure_rate": item["failure_rate"]}
        for item in profile["artifact_runs"]["operations"]
        if item["runs"] >= minimum_runs and item["failure_rate"] >= float(failure_overrides.get(item["operation"], failure_limit))
    ][:6]
    if high_failure:
        record({
            "code": "high_operation_failure_rate", "priority": 12,
            "evidence": high_failure,
            "action": "read_compact_failure_family_before_another_attempt",
        })
    stale_by_operation = profile["artifact_runs"].get("stale_in_progress_by_operation", {})
    stale_allowance = int(thresholds.get("stale_in_progress_sample_allowance_per_operation", 1))
    excess_stale = sum(max(0, int(count) - stale_allowance) for count in stale_by_operation.values())
    if excess_stale:
        record({
            "code": "stale_in_progress_artifacts", "priority": 18,
            "evidence": {
                "excess_runs": excess_stale,
                "by_operation": stale_by_operation,
            },
            "action": "archive_abandoned_runs_and_inspect_one_sample_per_operation",
            "command": "py -3.14 scripts/artifact_store.py checkpoint-prune",
        })
    result.sort(key=lambda item: (item["priority"], item["code"]))
    mitigated.sort(key=lambda item: (item["priority"], item["code"]))
    return result, mitigated


def build_profile(policy_path: Path) -> dict[str, Any]:
    policy = load_object(policy_path)
    build = ROOT / "build"
    profile: dict[str, Any] = {
        "schema": 1,
        "kind": "workflow_profile",
        "policy": relative(policy_path),
        "storage": filesystem_profile(build),
        "artifact_runs": artifact_run_profile(build),
        "routing": routing_profile(build / "06-routing/evidence/krt-history.jsonl"),
        "pipeline": pipeline_profile(build),
    }
    profile["recommendations"], profile["mitigated_findings"] = findings(profile, policy)
    profile["success"] = True
    profile["limits"] = "Bulk PCB, image and tool-log contents are never read; this profile uses metadata and compact receipts only."
    return profile


def stdout_index(profile: dict[str, Any], output: Path) -> dict[str, Any]:
    storage = profile["storage"]
    tmp = next((item for item in storage["by_lane"] if item["name"] == "tmp"), {"bytes": 0})
    operations = profile["artifact_runs"]["operations"]
    return {
        "success": True,
        "build_mb": round(storage["bytes"] / 1_048_576, 2),
        "tmp_mb": round(int(tmp["bytes"]) / 1_048_576, 2),
        "tmp_run_dirs": storage["tmp_run_dirs"],
        "route_recent_rejection_rate": profile["routing"]["recent_rejection_rate"],
        "stale_in_progress_runs": profile["artifact_runs"].get("stale_in_progress_runs", 0),
        "hotspots": [{
            "operation": item["operation"],
            "total_seconds": item["total_seconds"],
            "failure_rate": item["failure_rate"],
        } for item in operations[:3]],
        "recommendations": [item["code"] for item in profile["recommendations"]],
        "output": relative(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    profile = build_profile(args.policy.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stdout_index(profile, output), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
