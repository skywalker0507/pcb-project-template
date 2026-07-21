#!/usr/bin/env python3
"""Create a compact, verifiable handoff for the next Codex stage/session."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def tree_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
    for path in sorted(files):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper()


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run_pipeline(config: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_pcb_pipeline.py",
            "--config",
            str(config),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    for line in reversed(result.stdout.splitlines()):
        try:
            summary = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(summary, dict):
            if result.returncode != 0:
                summary["success"] = False
            return summary
    raise RuntimeError("PCB pipeline returned no JSON summary")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--open-item", action="append", default=[])
    parser.add_argument("--next-action", action="append", default=[])
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/pcb-flow.json",
    )
    parser.add_argument("--skip-pipeline", action="store_true")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    limit = int(config["handoff"].get("max_open_items", 8))
    if len(args.open_item) > limit or len(args.next_action) > limit:
        raise SystemExit(f"Handoff lists are limited to {limit} items")

    pipeline = None if args.skip_pipeline else run_pipeline(args.config)
    board_path = ROOT / config["project"]["board"]
    output = ROOT / config["handoff"]["output"]
    dirty_lines = git("status", "--short").splitlines()
    handoff = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": config["project"]["name"],
        "stage": args.stage,
        "status": args.status,
        "objective": args.objective,
        "source_sha256": tree_hash([ROOT / "ato.yaml", ROOT / "src"]),
        "board_sha256": (
            hashlib.sha256(board_path.read_bytes()).hexdigest().upper()
            if board_path.is_file()
            else None
        ),
        "git": {
            "commit": git("rev-parse", "HEAD"),
            "dirty": bool(dirty_lines),
            "dirty_path_count": len(dirty_lines),
        },
        "pipeline": pipeline,
        "open_items": args.open_item,
        "next_actions": args.next_action,
        "read_first": [
            str(output.resolve()),
        ],
        "authoritative_docs": [
            str((ROOT / path).resolve())
            for path in config["handoff"].get("authoritative_docs", [])
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown = output.with_suffix(".md")
    pipeline_status = (
        "not run"
        if pipeline is None
        else "PASS" if pipeline.get("success") else "FAIL"
    )
    markdown.write_text(
        "\n".join(
            [
                f"# {config['project']['name']} — Stage {args.stage}",
                "",
                f"- Status: {args.status}",
                f"- Objective: {args.objective}",
                f"- Pipeline: {pipeline_status}",
                f"- Board SHA-256: `{handoff['board_sha256']}`",
                f"- Source SHA-256: `{handoff['source_sha256']}`",
                "",
                "## Open items",
                *([f"- {item}" for item in args.open_item] or ["- None"]),
                "",
                "## Next actions",
                *([f"- {item}" for item in args.next_action] or ["- None"]),
                "",
                f"Machine-readable handoff: `{output}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "handoff": str(output.resolve()),
                "markdown": str(markdown.resolve()),
                "bytes": output.stat().st_size,
                "pipeline_success": pipeline.get("success") if pipeline else None,
            },
            separators=(",", ":"),
        )
    )
    return 0 if pipeline is None or pipeline.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
