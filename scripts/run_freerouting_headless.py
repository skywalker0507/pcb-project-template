#!/usr/bin/env python3
"""Run FreeRouting headlessly with deterministic paths and result checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JAR = ROOT / "tools/freerouting/freerouting-2.2.4.jar"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dsn", type=Path)
    parser.add_argument("ses", type=Path)
    parser.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    parser.add_argument("--heap-gb", type=int, default=12)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    dsn = args.dsn.resolve()
    ses = args.ses.resolve()
    jar = args.jar.resolve()
    if not dsn.is_file():
        raise SystemExit(f"DSN not found: {dsn}")
    if not jar.is_file():
        raise SystemExit(f"FreeRouting JAR not found: {jar}")
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
                "ses": str(ses),
                "bytes": ses.stat().st_size,
                "log": str(log),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
