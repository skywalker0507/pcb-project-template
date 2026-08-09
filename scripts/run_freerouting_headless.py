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
import re

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_tool_policy import require_tool
from artifact_store import create_run, finish_run, relative
from pcb_policy import load_acceptance
from design_policy import require_policy
from stage_policy import require_operation


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JAR = ROOT / "tools/freerouting/freerouting-latest.jar"
DEFAULT_ACCEPTANCE = ROOT / "config/pcb-acceptance.json"
DEFAULT_POLICY = ROOT / "config/freerouting-policy.json"
REQUIRED_HEADLESS_FLAG = "--gui.enabled=false"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def resolved_jar_hash() -> str | None:
    receipt = ROOT / "build/00-shared/evidence/toolchain/toolchain-resolved.json"
    if not receipt.is_file():
        return None
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
        expected = value.get("freerouting", {}).get("sha256")
        return str(expected).upper() if expected else None
    except (json.JSONDecodeError, AttributeError):
        raise SystemExit(f"Malformed toolchain receipt: {receipt}")


def dsn_net_classes(path: Path) -> set[str]:
    """Return declared DSN class names without placing the full DSN in model context."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        quoted or bare
        for quoted, bare in re.findall(r"\(class\s+(?:\"([^\"]+)\"|([^\s()]+))", text, re.IGNORECASE)
        if quoted or bare
    }


def build_freerouting_command(
    jar: Path,
    dsn: Path,
    output: Path,
    *,
    heap_gb: int,
    policy: dict[str, object],
    ignored_net_classes: list[str],
) -> list[str]:
    """Build the only authorized FreeRouting invocation for this project."""
    command = [
        "java",
        f"-Xmx{heap_gb}g",
        "-jar",
        str(jar),
        "-de",
        str(dsn),
        "-do",
        str(output),
        REQUIRED_HEADLESS_FLAG,
        f"--router.optimizer.enabled={'true' if policy.get('optimizer_enabled', False) else 'false'}",
        "-mp",
        str(int(policy.get("max_passes", 10))),
        "-mt",
        str(int(policy.get("max_threads", 4))),
    ]
    if ignored_net_classes:
        command.extend(["-inc", ",".join(ignored_net_classes)])
    return command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dsn", type=Path, nargs="?")
    parser.add_argument("ses", type=Path, nargs="?", help="Optional candidate path; defaults to the run tmp lane")
    parser.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    parser.add_argument("--source-board", type=Path)
    parser.add_argument("--check", action="store_true", help="Verify the locked JAR and wrapper policy without launching Java")
    parser.add_argument("--run-id")
    parser.add_argument("--heap-gb", type=int, default=12)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--acceptance", type=Path, default=DEFAULT_ACCEPTANCE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--ignore-net-class", action="append", default=[])
    parser.add_argument("--override-attempt-limit", action="store_true")
    args = parser.parse_args()
    jar = args.jar.resolve()
    if args.check:
        expected_jar = resolved_jar_hash()
        actual_jar = sha256(jar) if jar.is_file() else None
        success = bool(jar.is_file() and (not expected_jar or actual_jar == expected_jar))
        print(json.dumps({
            "success": success,
            "entrypoint": "scripts/run_freerouting_headless.py",
            "java_launched": False,
            "jar": relative(jar) if jar.is_file() else str(jar),
            "jar_sha256": actual_jar,
            "expected_jar_sha256": expected_jar,
            "required_flags": [REQUIRED_HEADLESS_FLAG],
        }, separators=(",", ":")))
        return 0 if success else 1
    if args.dsn is None or args.source_board is None:
        raise SystemExit("Routing requires DSN and --source-board; use --check for a non-launching diagnostic")
    require_operation("candidate_route")
    require_policy("candidate_routing")
    tool_policy = require_tool("freerouting")
    candidate_rules = load_acceptance(args.acceptance.resolve(), "routing_candidate", {
        "separate_candidate", "input_sha256_preserved", "project_rules_preserved",
        "no_forbidden_signal_layers", "candidate_audit_pass",
    })

    dsn = args.dsn.resolve()
    source_board = args.source_board.resolve()
    policy = json.loads(args.policy.resolve().read_text(encoding="utf-8"))
    if not dsn.is_file():
        raise SystemExit(f"DSN not found: {dsn}")
    if not jar.is_file():
        raise SystemExit(f"FreeRouting JAR not found: {jar}")
    if not source_board.is_file():
        raise SystemExit(f"Source board not found: {source_board}")
    if policy.get("require_ignored_net_classes", True) and not args.ignore_net_class:
        raise SystemExit("At least one --ignore-net-class is required; freeze critical/power classes before bulk routing")
    declared_classes = dsn_net_classes(dsn)
    unknown_classes = sorted(set(args.ignore_net_class) - declared_classes)
    if unknown_classes:
        raise SystemExit(f"Ignored net classes are not declared in the DSN: {unknown_classes}; declared={sorted(declared_classes)}")
    expected_jar = resolved_jar_hash()
    actual_jar = sha256(jar)
    if expected_jar and actual_jar != expected_jar:
        raise SystemExit("FreeRouting JAR SHA-256 differs from the resolved toolchain receipt")
    signature = {
        "dsn_sha256": sha256(dsn),
        "source_board_sha256": sha256(source_board),
        "ignored_net_classes": sorted(args.ignore_net_class),
        "max_passes": int(policy.get("max_passes", 10)),
        "optimizer_enabled": bool(policy.get("optimizer_enabled", False)),
    }
    prior_attempts = 0
    for receipt_path in (ROOT / "build/06-routing/evidence").glob("*/freerouting.json"):
        try:
            old = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if old.get("attempt_signature") == signature:
            prior_attempts += 1
    attempt_limit = int(tool_policy.get("max_attempts_per_profile", 2))
    if prior_attempts >= attempt_limit and not args.override_attempt_limit:
        raise SystemExit(json.dumps({
            "success": False, "error": "identical_attempt_limit",
            "attempts": prior_attempts, "limit": attempt_limit,
            "next": "Change the frozen-net profile or routing hypothesis; do not repeat the same bulk route.",
        }, separators=(",", ":")))
    run = create_run("routing", "freerouting", args.run_id)
    ses = (args.ses.resolve() if args.ses else run.tmp / "freerouting.ses")
    try:
        ses.relative_to(run.tmp.resolve())
    except ValueError as exc:
        raise SystemExit(f"SES must stay in this disposable run: {run.tmp}") from exc
    ses.parent.mkdir(parents=True, exist_ok=True)
    if ses.exists() and not args.force:
        raise SystemExit(f"SES already exists; pass --force to replace after a successful run: {ses}")
    temporary_ses = ses.with_suffix(f".freerouting-{os.getpid()}.tmp.ses")
    if temporary_ses.exists():
        temporary_ses.unlink()

    command = build_freerouting_command(
        jar, dsn, temporary_ses,
        heap_gb=args.heap_gb,
        policy=policy,
        ignored_net_classes=args.ignore_net_class,
    )
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log = (args.log or run.tmp / "freerouting.log").resolve()
    try:
        log.relative_to(run.tmp.resolve())
    except ValueError as exc:
        raise SystemExit(f"FreeRouting log must stay in this disposable run: {run.tmp}") from exc
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(process.stdout, encoding="utf-8")
    if args.verbose:
        print(process.stdout[-6000:], end="")
    if process.returncode != 0:
        temporary_ses.unlink(missing_ok=True)
        finish_run(run, False, artifacts={"log": relative(log)}, metrics={"exit_code": process.returncode})
        print(json.dumps({"success": False, "exit_code": process.returncode, "log": relative(log)}, separators=(",", ":")))
        return process.returncode
    if not temporary_ses.is_file() or temporary_ses.stat().st_size == 0:
        temporary_ses.unlink(missing_ok=True)
        finish_run(run, False, artifacts={"log": relative(log)}, metrics={"exit_code": 2})
        print(json.dumps({"success": False, "error": "missing_ses", "log": relative(log)}, separators=(",", ":")))
        return 2
    session_head = temporary_ses.read_text(encoding="utf-8", errors="replace")[:65536]
    if "(session" not in session_head:
        temporary_ses.unlink(missing_ok=True)
        finish_run(run, False, artifacts={"log": relative(log)}, metrics={"exit_code": 3})
        print(json.dumps({"success": False, "error": "invalid_ses", "log": relative(log)}, separators=(",", ":")))
        return 3
    os.replace(temporary_ses, ses)
    receipt = run.evidence / "freerouting.json"
    payload = {
        "success": True,
        "accepted": False,
        "run_id": run.run_id,
        "ses": relative(ses),
        "bytes": ses.stat().st_size,
        "log": relative(log),
        "dsn_sha256": sha256(dsn),
        "ses_sha256": sha256(ses),
        "source_board": str(source_board),
        "source_board_sha256": sha256(source_board),
        "jar_sha256": actual_jar,
        "ignored_net_classes": args.ignore_net_class,
        "attempt_signature": signature,
        "attempt_number": prior_attempts + 1,
        "limits": {"max_passes": policy.get("max_passes"), "max_threads": policy.get("max_threads")},
        "acceptance": {"path": str(args.acceptance.resolve()), "requires": candidate_rules.get("requires", []), "accepted": False},
        "next_required": (
            "Import the SES into a separate candidate board in KiCad, then run "
            "scripts/krt_candidate_audit.py against the source and candidate."
        ),
    }
    receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finish_run(
        run, True,
        artifacts={"receipt": relative(receipt), "session": relative(ses), "log": relative(log)},
        metrics={"accepted": False, "bytes": ses.stat().st_size, "ignored_net_classes": args.ignore_net_class},
    )
    print(
        json.dumps({
            "success": True, "accepted": False, "run_id": run.run_id,
            "receipt": relative(receipt), "ses": relative(ses), "log": relative(log),
            "next_required": payload["next_required"],
        }, separators=(",", ":"))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
