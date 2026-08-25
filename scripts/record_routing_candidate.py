#!/usr/bin/env python3
"""Record an audited routing candidate without promoting it to the active board.

This closes the gap between a passing before/after DRC comparison and a stage
handoff.  The receipt is evidence for a *candidate only*: it never changes
``config/pcb-flow.json`` and can never be used as release evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True, help="krt_candidate_audit JSON report")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", required=True, help="Short, reviewable description of the copper change")
    args = parser.parse_args()
    candidate, audit_path, output = args.candidate.resolve(), args.audit.resolve(), args.output.resolve()
    if not candidate.is_file():
        raise SystemExit(f"Candidate board not found: {candidate}")
    if output.exists():
        raise SystemExit(f"Receipt output already exists: {output}")
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid candidate audit: {audit_path}: {exc}") from exc
    if not isinstance(audit, dict) or audit.get("success") is not True or audit.get("baseline_pass") is not True:
        raise SystemExit("Candidate receipt requires a successful zero-new-DRC comparison")
    recorded_candidate = Path(str(audit.get("candidate", ""))).resolve()
    if recorded_candidate != candidate:
        raise SystemExit("Candidate path does not match the DRC comparison")
    candidate_hash = sha256(candidate)
    if audit.get("candidate_sha256") != candidate_hash:
        raise SystemExit("Candidate SHA does not match the DRC comparison")
    project = candidate.with_suffix(".kicad_pro")
    payload = {
        "schema": 1,
        "kind": "routing_candidate",
        "success": True,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "candidate": str(candidate),
        "candidate_sha256": candidate_hash,
        "candidate_project": str(project) if project.is_file() else None,
        "candidate_project_sha256": sha256(project) if project.is_file() else None,
        "input": audit.get("input"),
        "input_sha256": audit.get("input_sha256"),
        "audit": str(audit_path),
        "audit_sha256": sha256(audit_path),
        "scope": args.scope,
        "evidence": {"baseline_pass": True, "new_violations": []},
        "limits": [
            "This receipt preserves an isolated candidate only.",
            "It is not a promotion, physical review, manufacturing approval, or release evidence.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": True, "receipt": str(output), "candidate_sha256": candidate_hash}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
