#!/usr/bin/env python3
"""Run FreeRouting headlessly with deterministic paths and result checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcb_policy import load_acceptance
from design_policy import require_policy
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JAR = ROOT / "tools/freerouting/freerouting-latest.jar"
DEFAULT_ARTIFACT_ROOT = ROOT / "build/candidates/freerouting"
DEFAULT_ACCEPTANCE = ROOT / "config/pcb-acceptance.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def resolved_jar_hash() -> str | None:
    receipt = ROOT / "build/toolchain-resolved.json"
    if not receipt.is_file():
        return None
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
        expected = value.get("freerouting", {}).get("sha256")
        return str(expected).upper() if expected else None
    except (json.JSONDecodeError, AttributeError):
        raise SystemExit(f"Malformed toolchain receipt: {receipt}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dsn", type=Path)
    parser.add_argument("ses", type=Path)
    parser.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    parser.add_argument("--source-board", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--heap-gb", type=int, default=12)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_ACCEPTANCE)
    args = parser.parse_args()
    require_operation("candidate_route")
    require_policy("candidate_routing")
    candidate_rules = load_acceptance(args.acceptance.resolve(), "routing_candidate", {
        "separate_candidate", "input_sha256_preserved", "project_rules_preserved",
        "no_forbidden_signal_layers", "candidate_audit_pass",
    })

    dsn = args.dsn.resolve()
    ses = args.ses.resolve()
    jar = args.jar.resolve()
    source_board = args.source_board.resolve()
    artifact_root = args.artifact_root.resolve()
    if not dsn.is_file():
        raise SystemExit(f"DSN not found: {dsn}")
    if not jar.is_file():
        raise SystemExit(f"FreeRouting JAR not found: {jar}")
    if not source_board.is_file():
        raise SystemExit(f"Source board not found: {source_board}")
    if artifact_root not in ses.parents:
        raise SystemExit(f"SES must be a disposable candidate under {artifact_root}: {ses}")
    expected_jar = resolved_jar_hash()
    actual_jar = sha256(jar)
    if expected_jar and actual_jar != expected_jar:
        raise SystemExit("FreeRouting JAR SHA-256 differs from the resolved toolchain receipt")
    ses.parent.mkdir(parents=True, exist_ok=True)
    if ses.exists() and not args.force:
        raise SystemExit(f"SES already exists; pass --force to replace after a successful run: {ses}")
    temporary_ses = ses.with_suffix(f".freerouting-{os.getpid()}.tmp.ses")
    if temporary_ses.exists():
        temporary_ses.unlink()

    command = [
        "java",
        f"-Xmx{args.heap_gb}g",
        "-jar",
        str(jar),
        "-de",
        str(dsn),
        "-do",
        str(temporary_ses),
        "--gui.enabled=false",
        "--router.optimizer.enabled=false",
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log = (args.log or ses.with_suffix(".freerouting.log")).resolve()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if args.verbose:
        print(process.stdout, end="")
    if process.returncode != 0:
        temporary_ses.unlink(missing_ok=True)
        return process.returncode
    if not temporary_ses.is_file() or temporary_ses.stat().st_size == 0:
        temporary_ses.unlink(missing_ok=True)
        print("FreeRouting exited without producing a non-empty SES", file=sys.stderr)
        return 2
    session_head = temporary_ses.read_text(encoding="utf-8", errors="replace")[:65536]
    if "(session" not in session_head:
        temporary_ses.unlink(missing_ok=True)
        print(f"Invalid SES header; inspect {log}", file=sys.stderr)
        return 3
    os.replace(temporary_ses, ses)
    print(
        json.dumps(
            {
                "success": True,
                "accepted": False,
                "ses": str(ses),
                "bytes": ses.stat().st_size,
                "log": str(log),
                "dsn_sha256": sha256(dsn),
                "ses_sha256": sha256(ses),
                "source_board": str(source_board),
                "source_board_sha256": sha256(source_board),
                "jar_sha256": actual_jar,
                "acceptance": {"path": str(args.acceptance.resolve()), "requires": candidate_rules.get("requires", []), "accepted": False},
                "next_required": (
                    "Import the SES into a separate candidate board in KiCad, then run "
                    "scripts/krt_candidate_audit.py against the source and candidate."
                ),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
