#!/usr/bin/env python3
"""Compile reviewed atopile source without invoking part picking or PCB generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def wsl_process_environment() -> dict[str, str]:
    env = os.environ.copy()
    existing = [item for item in env.get("WSLENV", "").split(":") if item]
    existing_names = {item.split("/", 1)[0] for item in existing}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"):
        if env.get(name) and name not in existing_names:
            existing.append(name)
    if existing:
        env["WSLENV"] = ":".join(existing)
    return env


def windows_to_wsl(path: Path) -> str:
    match = re.fullmatch(r"([A-Za-z]):\\(.*)", str(path.resolve()))
    if not match:
        raise SystemExit(f"Cannot convert path to WSL form: {path}")
    drive, tail = match.groups()
    return f"/mnt/{drive.lower()}/{tail.replace(os.sep, '/')}"


def source_sha256(source_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source_root.rglob("*.ato")):
        digest.update(path.relative_to(source_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper()


def compile_source(entry: str) -> dict[str, object]:
    import importlib.metadata

    import faebryk.core.faebrykpy as fbrk
    import faebryk.core.graph as graph
    from atopile.compiler.build import Linker, StdlibRegistry, build_file, build_stage_2
    from atopile.config import config

    file_name, separator, module_name = entry.partition(":")
    if not separator or not file_name.endswith(".ato") or not module_name:
        raise ValueError("Entry must use path/to/file.ato:Module syntax")
    entry_path = (ROOT / "src" / file_name).resolve()
    entry_path.relative_to((ROOT / "src").resolve())
    if not entry_path.is_file():
        raise FileNotFoundError(entry_path)

    config.apply_options(None, selected_builds=("default",), working_dir=ROOT)
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)
    stdlib = StdlibRegistry(tg)
    result = build_file(g=g, tg=tg, import_path=file_name, path=entry_path)
    linker = Linker(config, stdlib, tg)
    build_stage_2(g, tg, linker, result, validate=True)
    if module_name not in result.state.type_roots:
        raise ValueError(
            f"Entrypoint module {module_name!r} is unavailable; "
            f"found {sorted(result.state.type_roots)}"
        )
    tg.instantiate_node(type_node=result.state.type_roots[module_name], attributes={})
    return {
        "schema": 1,
        "kind": "atopile_source_validation",
        "success": True,
        "entry": entry,
        "compiler_version": importlib.metadata.version("atopile"),
        "source_sha256": source_sha256(ROOT / "src"),
        "checks": ["parse", "imports", "type_graph", "deferred_operations", "entry_instantiation"],
        "limit": "This does not pick parts, run electrical ERC, or generate a PCB candidate.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", default="main.ato:App")
    parser.add_argument("--distro", default="Ubuntu-24.04")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wsl-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if os.name == "nt" and not args.wsl_child:
        command = (
            f"cd {windows_to_wsl(ROOT)} && "
            "~/.local/share/uv/tools/atopile/bin/python "
            f"scripts/validate_atopile_source.py --wsl-child --entry {args.entry}"
        )
        process = subprocess.run(
            ["wsl.exe", "-d", args.distro, "--", "bash", "-lc", command],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=wsl_process_environment(),
        )
        lines = [line for line in process.stdout.splitlines() if line.strip()]
        try:
            receipt = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError):
            receipt = {
                "schema": 1,
                "kind": "atopile_source_validation",
                "success": False,
                "entry": args.entry,
                "failure": "validator_runtime_failure",
                "evidence": lines[-8:],
            }
        if process.returncode:
            receipt["success"] = False
        if args.output:
            args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
            args.output.resolve().write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
        return 0 if receipt.get("success") is True else 1

    try:
        receipt = compile_source(args.entry)
    except BaseException as exc:
        receipt = {
            "schema": 1,
            "kind": "atopile_source_validation",
            "success": False,
            "entry": args.entry,
            "source_sha256": source_sha256(ROOT / "src"),
            "failure": type(exc).__name__,
            "evidence": str(exc).splitlines()[-8:],
        }
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0 if receipt.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
