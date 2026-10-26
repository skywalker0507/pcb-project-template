#!/usr/bin/env python3
"""Build atopile in WSL without allowing it to overwrite the routed KiCad PCB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from electrical_backend import footprint_table, read_config, selected_backend, source_entry


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def windows_to_wsl(path: Path) -> str:
    match = re.fullmatch(r"([A-Za-z]):\\(.*)", str(path.resolve()))
    if not match:
        raise SystemExit(f"Cannot convert path to WSL form: {path}")
    drive, tail = match.groups()
    return f"/mnt/{drive.lower()}/{tail.replace(os.sep, '/')}"


def source_requires_part_picker(source_root: Path) -> bool:
    """Return true when reviewed source contains explicit part-picking constraints."""
    for path in source_root.rglob("*.ato"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\b(?:lcsc_id|manufacturer|mpn|package)\s*=", text):
            return True
    return False


def wsl_process_environment(network_mode: str = "inherit") -> dict[str, str]:
    """Apply the reviewed direct/inherited network policy to WSL processes."""
    env = os.environ.copy()
    proxy_names = (
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    )
    if network_mode == "direct":
        for name in proxy_names:
            env.pop(name, None)
        retained = [
            item for item in env.get("WSLENV", "").split(":") if item
            and item.split("/", 1)[0] not in proxy_names
        ]
        if retained:
            env["WSLENV"] = ":".join(retained)
        else:
            env.pop("WSLENV", None)
        return env
    existing = [item for item in env.get("WSLENV", "").split(":") if item]
    existing_names = {item.split("/", 1)[0] for item in existing}
    for name in proxy_names:
        if env.get(name) and name not in existing_names:
            existing.append(name)
    if existing:
        env["WSLENV"] = ":".join(existing)
    return env


def classify_failure(log: str) -> dict[str, object] | None:
    log = re.sub(r"\x1b\[[0-9;]*m", "", log)
    rules = [
        ("atopile_auth_required", r"Part picking on atopile 0\.15\.8 requires sign-in|Sign-in required", "Run `ato auth login` interactively in the configured WSL distro, then rerun the isolated electrical build. Never add credentials to the repository."),
        ("component_data_unreachable", r"Fetching component data failed|Name or service not known|EasyEDA API is unavailable|Failed to fetch footprint data.*handshake operation timed out", "Restore component-data access or provide a reviewed offline component cache; do not change electrical sources merely to bypass picking."),
        ("electrical_net_name_conflict", r"Multiple conflicting required net names|conflicting.*net names", "Resolve the conflicting net names in reviewed .ato connectivity. Retry only after the source changes."),
        ("footprint_resource_mismatch", r"Footprint `[^`]+` doesn't exist in library `[^`]+`", "Check the configured source_root, resolved fp-lib-table URI, filename and internal footprint name. A stale library URI after moving resources is distinct from a wrong package; do not substitute a similar footprint."),
        ("package_compiler_incompatible", r"trait not found|Incompatible interface types", "Review the exact upstream package/compiler compatibility issue; keep any local migration patch separately reviewed."),
        ("dependency_resolution", r"Failed to resolve.*dependency|ConnectError", "Check the local dependency cache and immutable package revision before retrying."),
        ("source_validation", r"SyntaxError|TypeError|Ercfault", "Inspect the cited .ato source location and resolve the electrical validation error."),
    ]
    for name, pattern, next_action in rules:
        if re.search(pattern, log, re.IGNORECASE):
            lines = log.splitlines()
            indices = [i for i, line in enumerate(lines) if re.search(pattern, line, re.IGNORECASE)]
            evidence = list(dict.fromkeys(line.strip().strip("│ ") for i in indices[:2] for line in lines[max(0, i-1):i+3] if line.strip()))
            return {"class": name, "evidence": evidence[:6], "next": next_action}
    if log.strip():
        lines = [line.strip().strip("│ ") for line in log.splitlines() if line.strip()]
        errors = [line for line in lines if re.search(r"error|exception|failed|\.ato:\d|\.py:\d", line, re.IGNORECASE) and not re.search(r"^Build failed|^Build took|^Targets?\s", line, re.IGNORECASE)]
        return {"class": "unclassified_build_failure", "evidence": (errors[:6] or lines[-6:]), "next": "Inspect the cited root error in the on-disk build log; do not retry an unchanged failure."}
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", default="default")
    parser.add_argument("--distro", default="Ubuntu-24.04")
    parser.add_argument("--entry", help="Entry relative to configured source_root")
    parser.add_argument("--network-mode", choices=("direct", "inherit"), default="inherit")
    parser.add_argument("--board", type=Path)
    parser.add_argument(
        "--output", type=Path,
        help="Optional isolated candidate directory for a successful generated board.",
    )
    parser.add_argument(
        "--receipt", type=Path,
        help="Optional common electrical-backend receipt path.",
    )
    parser.add_argument("--log", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.build):
        raise SystemExit("Invalid build name")
    _, backend = selected_backend(read_config(ROOT / "config/electrical-backend.json"), "atopile")
    source_root, _, _ = source_entry(backend, args.entry, root=ROOT)
    args.entry = args.entry or backend["entry"]

    board = (
        args.board if args.board and args.board.is_absolute()
        else ROOT / args.board if args.board
        else ROOT / f"elec/layout/{args.build}/{args.build}.kicad_pcb"
    ).resolve()
    board.parent.mkdir(parents=True, exist_ok=True)
    guard = board.with_suffix(f".codex-build-guard-{os.getpid()}.kicad_pcb.bak")
    stale_guards = sorted(board.parent.glob(f"{board.stem}.codex-build-guard-*.kicad_pcb.bak"))
    if not board.exists() and len(stale_guards) == 1:
        os.replace(stale_guards[0], board)
        print(f"RECOVERED_STALE_GUARD={stale_guards[0]}")
        stale_guards = []
    if stale_guards:
        raise SystemExit(
            "Stale build guards require manual inspection: "
            + ", ".join(str(path) for path in stale_guards)
        )
    if guard.exists():
        raise SystemExit(f"Guard path already exists: {guard}")
    candidate_board = None
    if args.output:
        output = args.output.resolve()
        if output.exists() and any(output.iterdir()):
            raise SystemExit(f"Candidate output already exists and is not empty: {output}")
        output.mkdir(parents=True, exist_ok=True)
        candidate_board = output / "candidate.kicad_pcb"

    protected_board_existed = board.is_file()
    before = sha256(board) if protected_board_existed else None
    log_path = args.log.resolve() if args.log else (
        args.output.resolve() / "atopile-build.log" if args.output
        else ROOT / "build/03-electrical/tmp/atopile/ato-build-isolated.log"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    source_validation_process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/validate_atopile_source.py"),
            "--distro",
            args.distro,
            "--entry",
            args.entry,
            "--build", args.build,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    source_validation_lines = [
        line for line in source_validation_process.stdout.splitlines() if line.strip()
    ]
    try:
        source_validation = json.loads(source_validation_lines[-1])
    except (IndexError, json.JSONDecodeError):
        source_validation = {
            "kind": "atopile_source_validation",
            "success": False,
            "failure": "validator_runtime_failure",
            "evidence": source_validation_lines[-8:],
        }
    if source_validation_process.returncode or source_validation.get("success") is not True:
        log_path.write_text(source_validation_process.stdout, encoding="utf-8")
        payload = {
            "schema": 1,
            "kind": "electrical_build",
            "backend": "atopile",
            "success": False,
            "exit_code": 1,
            "build": args.build,
            "build_id": None,
            "compiler_version": source_validation.get("compiler_version"),
            "log": str(log_path),
            "candidate_board": None,
            "protected_board_existed": board.is_file(),
            "restored_board_sha256": sha256(board).upper() if board.is_file() else None,
            "failure": {
                "class": "source_validation",
                "evidence": source_validation.get("evidence", [])[:8],
                "next": "Fix the cited reviewed atopile source; authentication and part picking were not attempted.",
            },
            "source_validation": source_validation,
            "preflight": "atopile_source_validation",
            "isolation": "Source compilation failed before any board or source-tree artifact was changed.",
        }
        if args.receipt:
            args.receipt.resolve().parent.mkdir(parents=True, exist_ok=True)
            args.receipt.resolve().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(payload, separators=(",", ":")))
        return 1
    preflight_log = ""
    if source_requires_part_picker(source_root):
        preflight_command = (
            "printf 'CODEX_ATO_VERSION=' && ato --version | tail -n 1 && "
            "ato auth status"
        )
        preflight = subprocess.run(
            ["wsl.exe", "-d", args.distro, "--", "bash", "-lc", preflight_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=wsl_process_environment(args.network_mode),
        )
        preflight_log = preflight.stdout
        if re.search(r"^Signed out\.$", preflight_log, re.MULTILINE):
            log_path.write_text(
                preflight_log
                + "\nBuild failed: Part picking on atopile 0.15.8 requires sign-in. "
                + "Run `ato auth login`.\n",
                encoding="utf-8",
            )
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            version_match = re.search(r"^CODEX_ATO_VERSION=(.+)$", log_text, re.MULTILINE)
            payload = {
                "schema": 1,
                "kind": "electrical_build",
                "backend": "atopile",
                "success": False,
                "exit_code": 1,
                "build": args.build,
                "build_id": None,
                "compiler_version": version_match.group(1).strip() if version_match else None,
                "log": str(log_path),
                "candidate_board": None,
                "protected_board_existed": board.is_file(),
                "restored_board_sha256": sha256(board).upper() if board.is_file() else None,
                "failure": classify_failure(log_text),
                "source_validation": source_validation,
                "preflight": "atopile_auth_status",
                "isolation": "Authentication was checked before any board or source-tree artifact was changed.",
            }
            if args.receipt:
                args.receipt.resolve().parent.mkdir(parents=True, exist_ok=True)
                args.receipt.resolve().write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            print(json.dumps(payload, separators=(",", ":")))
            return 1
    table = board.parent / "fp-lib-table"
    table_before = table.read_bytes() if table.exists() else None
    compiler_table = footprint_table(board.parent, source_root)
    if protected_board_existed:
        os.replace(board, guard)
    exit_code = 1
    candidate_captured = False
    try:
        # Compile against current resource paths, then restore the physical
        # project's table byte-for-byte, just like its reviewed board.
        table.write_text(compiler_table, encoding="utf-8")
        command = (
            f"cd {shlex.quote(windows_to_wsl(ROOT))} && "
            "printf 'CODEX_ATO_VERSION=' && ato --version | tail -n 1 && "
            f"ato build --build {args.build}"
        )
        process = subprocess.Popen(
            ["wsl.exe", "-d", args.distro, "--", "bash", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=wsl_process_environment(args.network_mode),
        )
        assert process.stdout is not None
        with log_path.open("w", encoding="utf-8", newline="\n") as log:
            for line in process.stdout:
                if args.verbose:
                    print(line, end="")
                log.write(line)
        exit_code = process.wait()
    finally:
        # The protected board restoration runs even if candidate capture fails.
        # A failed copy must never leave the manual KiCad board behind a guard.
        try:
            if board.exists():
                if exit_code == 0 and candidate_board:
                    if candidate_board.exists():
                        raise RuntimeError(f"Refusing to overwrite existing candidate board: {candidate_board}")
                    shutil.copy2(board, candidate_board)
                    (candidate_board.parent / "fp-lib-table").write_text(footprint_table(candidate_board.parent, source_root), encoding="utf-8")
                    candidate_captured = True
                if args.verbose:
                    print(f"DISCARDING_GENERATED_BOARD={board}")
                board.unlink()
        finally:
            try:
                if protected_board_existed:
                    if not guard.exists():
                        raise RuntimeError("Protected-board guard disappeared during build")
                    os.replace(guard, board)
            finally:
                if table_before is not None:
                    table.write_bytes(table_before)
                elif table.exists():
                    table.unlink()

    after = sha256(board) if protected_board_existed else None
    if protected_board_existed and after != before:
        raise RuntimeError(f"Routed board changed: {before} -> {after}")
    if exit_code == 0 and candidate_board and not candidate_captured:
        exit_code = 1
        with log_path.open("a", encoding="utf-8") as log:
            log.write("\nBuild reported success but produced no KiCad PCB candidate.\n")
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    build_ids = re.findall(r"\bBuild ([0-9a-f]{8,})\b|\[([0-9a-f]{8,})\]", log_text)
    build_id = next((left or right for left, right in reversed(build_ids)), None)
    version_match = re.search(r"^CODEX_ATO_VERSION=(.+)$", log_text, re.MULTILINE)
    payload = {
        "schema": 1,
        "kind": "electrical_build",
        "backend": "atopile",
        "success": exit_code == 0,
        "exit_code": exit_code,
        "build": args.build,
        "build_id": build_id,
        "compiler_version": version_match.group(1).strip() if version_match else None,
        "log": str(log_path),
        "candidate_board": str(candidate_board.resolve()) if exit_code == 0 and candidate_board else None,
        "protected_board_existed": protected_board_existed,
        "restored_board_sha256": after.upper() if after else None,
        "failure": classify_failure(log_text) if exit_code else None,
        "source_validation": source_validation,
        "isolation": "Generated candidate copied to build/; any pre-existing KiCad board is restored byte-for-byte and a newly generated source-tree board is removed.",
    }
    if args.receipt:
        args.receipt.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.receipt.resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
