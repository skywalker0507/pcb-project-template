#!/usr/bin/env python3
"""Create a Houtini task only when deterministic size and safety rules allow it.

This script never calls a model. Its small JSON result lets Codex either make one
path-based `delegate` call or continue with the compact deterministic receipt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from ai_tool_policy import require_tool
from artifact_store import config as artifact_config, create_run


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config/houtini-delegation-policy.json"
DEFAULT_OUTPUT_DIR = None


def read_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Houtini delegation policy is invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SystemExit("Unsupported Houtini delegation policy")
    return value


def under_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def create_job(
    paths: list[Path], task_type: str, reason: str, policy_path: Path = DEFAULT_POLICY,
    output_dir: Path | None = DEFAULT_OUTPUT_DIR, job_name: str | None = None,
) -> dict[str, Any]:
    require_tool("houtini", allow_optional=True)
    policy = read_policy(policy_path.resolve())
    if policy.get("enabled") is not True:
        return {"eligible": False, "reason": "delegation_disabled"}
    max_paths = int(policy.get("max_paths", 0))
    allowed = {str(item).lower() for item in policy.get("allowed_suffixes", [])}
    forbidden = {str(item).lower() for item in policy.get("forbidden_suffixes", [])}
    resolved = [path.resolve() for path in paths]
    if not resolved or len(resolved) > max_paths:
        return {"eligible": False, "reason": "path_count_out_of_policy", "path_count": len(resolved)}
    invalid = [str(path) for path in resolved if not path.is_file() or path.is_symlink() or not under_root(path)]
    if invalid:
        return {"eligible": False, "reason": "unsafe_or_missing_path", "paths": invalid}
    suffixes = {path.suffix.lower() for path in resolved}
    if suffixes & forbidden or not suffixes.issubset(allowed):
        return {"eligible": False, "reason": "file_type_not_delegable", "suffixes": sorted(suffixes)}
    total_bytes = sum(path.stat().st_size for path in resolved)
    minimum = int(policy.get("minimum_total_input_bytes", 0))
    if total_bytes < minimum:
        return {"eligible": False, "reason": "input_below_delegation_threshold", "input_bytes": total_bytes, "minimum_bytes": minimum}
    delegate = policy.get("delegate")
    if not isinstance(delegate, dict):
        raise SystemExit("Houtini delegation policy has no delegate settings")
    safe_name = "".join(char if char.isalnum() or char in "-_" else "-" for char in (job_name or task_type))[:80]
    if output_dir is None:
        from stage_policy import current_stage
        run = create_run(str(current_stage().get("stage")), "houtini", allow_existing=False)
        output_dir = run.evidence / "delegation"
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    job_path = output_dir / f"{safe_name}-houtini-job.json"
    task = f"Classify the supplied {task_type} artifacts. {reason}\n{delegate.get('output_contract', 'Return concise evidence and explicit unknowns.')}"
    job = {
        "schema": 1,
        "eligible": True,
        "task_type": task_type,
        "input_bytes": total_bytes,
        "instruction": "Use one Houtini delegate call only if this job is eligible; do not read the source artifacts into Codex first.",
        "delegate": {
            "task": task,
            "paths": [str(path) for path in resolved],
            "kind": delegate.get("kind", "summarize"),
            "thinking": delegate.get("thinking", "disabled"),
            "max_tokens": min(
                int(delegate.get("max_tokens", 800)),
                int((artifact_config().get("context_budget") or {}).get("max_delegated_output_tokens", 800)),
            ),
            "max_chars": min(
                int(delegate.get("max_chars", 3200)),
                int((artifact_config().get("context_budget") or {}).get("max_delegated_output_chars", 3200)),
            ),
        },
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"eligible": True, "job": str(job_path), "input_bytes": total_bytes, "task_type": task_type}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--path", type=Path, action="append", required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--job-name")
    args = parser.parse_args()
    result = create_job(args.path, args.task_type, args.reason, args.policy, args.output_dir, args.job_name)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
