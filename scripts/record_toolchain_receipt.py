#!/usr/bin/env python3
"""Record already-installed PCB tool versions and hashes; never downloads or builds."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import shutil


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "build/00-shared/evidence/toolchain/toolchain-resolved.json"


def output(command: list[str], cwd: Path = ROOT) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        raise SystemExit(f"Command failed: {command!r}\n{result.stdout[-1000:]}{result.stderr[-1000:]}")
    return result.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_existing(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Existing toolchain receipt is invalid and will not be overwritten: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Existing toolchain receipt must contain a JSON object: {path}")
    return value


def merge_receipt(
    existing: dict[str, object],
    update: dict[str, object],
    *,
    electrical_only: bool,
    recorded_at: str,
) -> dict[str, object]:
    if electrical_only:
        receipt = dict(existing)
        # Only one electrical compiler identity may be current. Preserve all
        # non-electrical tool records from a previous full receipt.
        receipt.pop("atopile", None)
        receipt.pop("tscircuit", None)
        receipt.update(update)
        receipt["mode"] = str(existing.get("mode") or "electrical_only")
    else:
        receipt = dict(update)
        receipt["mode"] = "manual"
    receipt["schema"] = 2
    refreshed = ["electrical_backend", str((update.get("electrical_backend") or {}).get("active", ""))]
    if not electrical_only:
        refreshed.extend(name for name in ("houtini_lm", "kicad_routing_tools", "freerouting", "konnect") if name in update)
    refreshed = [name for name in refreshed if name]
    prior_section_times = existing.get("section_recorded_at")
    section_times = dict(prior_section_times) if electrical_only and isinstance(prior_section_times, dict) else {}
    for name in refreshed:
        section_times[name] = recorded_at
    receipt["section_recorded_at"] = section_times
    receipt["recording"] = {
        "last_operation": "electrical_only" if electrical_only else "full",
        "refreshed_sections": refreshed,
        "recorded_at": recorded_at,
        "preserved_unchecked_sections": sorted(
            name for name in ("houtini_lm", "kicad_routing_tools", "freerouting", "konnect")
            if electrical_only and name in receipt
        ),
    }
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distro", default="Ubuntu-24.04")
    parser.add_argument("--output", type=Path, default=RECEIPT)
    parser.add_argument("--without-konnect", action="store_true")
    parser.add_argument(
        "--electrical-only", action="store_true",
        help="Refresh the selected electrical compiler/runtime while preserving existing non-electrical tool records",
    )
    args = parser.parse_args()

    houtini = ROOT / "tools/houtini-lm"
    krt = ROOT / "tools/KiCadRoutingTools"
    jar = ROOT / "tools/freerouting/freerouting-latest.jar"
    krt_python = ROOT / ".venv-routingtools/Scripts/python.exe"
    missing = [str(path) for path in (houtini / ".git", krt / ".git", jar, krt_python) if not path.exists()]
    if missing and not args.electrical_only:
        raise SystemExit("Cannot record an incomplete local toolchain:\n" + "\n".join(missing))

    backend_config = json.loads((ROOT / "config/electrical-backend.json").read_text(encoding="utf-8"))
    active_backend = backend_config.get("active")
    backends = backend_config.get("backends", {})
    if not isinstance(active_backend, str) or active_backend not in backends:
        raise SystemExit("config/electrical-backend.json has no valid active backend")
    update: dict[str, object] = {
        "electrical_backend": {"active": active_backend},
    }
    if active_backend == "tscircuit":
        root = ROOT / "tscircuit"
        bun = shutil.which("bun")
        lock = root / "bun.lock"
        package = root / "package.json"
        if not bun or not lock.is_file() or not package.is_file():
            raise SystemExit("tscircuit receipt needs native Bun plus tscircuit/package.json and tscircuit/bun.lock")
        package_data = json.loads(package.read_text(encoding="utf-8"))
        update["tscircuit"] = {
            "bun_version": output([bun, "--version"]),
            "package_version": package_data.get("dependencies", {}).get("tscircuit"),
            "package_sha256": sha256(package),
            "lock_sha256": sha256(lock),
        }
    elif active_backend == "atopile":
        update["atopile"] = {"version": output(["wsl.exe", "-d", args.distro, "--", "bash", "-lc", "export PATH=\"$HOME/.local/bin:$PATH\"; ato --version | tail -n 1"])}
    else:
        raise SystemExit(f"No receipt strategy is configured for electrical backend: {active_backend}")
    if not args.electrical_only:
        update.update({
            "houtini_lm": {"commit": output(["git", "-C", str(houtini), "rev-parse", "HEAD"]), "version": json.loads((houtini / "package.json").read_text(encoding="utf-8"))["version"]},
            "kicad_routing_tools": {"commit": output(["git", "-C", str(krt), "rev-parse", "HEAD"])},
            "freerouting": {"sha256": sha256(jar)},
        })
        konnect = ROOT / "tools/konnect/konnect.exe"
        if args.without_konnect:
            update["konnect"] = {"disabled": True}
        else:
            if not konnect.is_file():
                raise SystemExit(f"Konnect executable not found: {konnect}; pass --without-konnect only when intentionally disabled")
            update["konnect"] = {"binary_version": output([str(konnect), "--version"]), "binary_sha256": sha256(konnect)}
    recorded_at = datetime.now(timezone.utc).isoformat()
    receipt = merge_receipt(
        load_existing(args.output) if args.electrical_only else {},
        update,
        electrical_only=args.electrical_only,
        recorded_at=recorded_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "receipt": str(args.output.resolve()),
        "operation": receipt["recording"]["last_operation"],
        "refreshed_sections": receipt["recording"]["refreshed_sections"],
        "preserved_unchecked_sections": receipt["recording"]["preserved_unchecked_sections"],
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
