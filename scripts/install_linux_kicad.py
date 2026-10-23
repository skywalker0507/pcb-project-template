#!/usr/bin/env python3
"""Extract authenticated APT packages into an isolated, untracked KiCad prefix.

Configure the official KiCad 10 Ubuntu PPA and run apt update first. This
installer only reads APT metadata; it never changes the system package database.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ["kicad", "kicad-footprints", "kicad-symbols", "python3-pil", "xvfb", "xauth"]


def package_hashes(metadata: str) -> dict[str, str]:
    result = {}
    for paragraph in metadata.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines()
                      if ": " in line and not line.startswith(" "))
        if "Filename" in fields and "SHA256" in fields:
            result[urllib.parse.unquote(Path(fields["Filename"]).name)] = fields["SHA256"]
    return result


def write_launchers(prefix: Path) -> None:
    binary = prefix / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    if not (prefix / "share").exists():
        (prefix / "share").symlink_to("usr/share", target_is_directory=True)
    lib = prefix / "usr/lib" / str(sysconfig.get_config_var("MULTIARCH"))
    common = ("#!/bin/sh\nset -eu\n"
              f"export APPDIR={shlex.quote(str(prefix))}\n"
              f"export KICAD_STOCK_DATA_HOME={shlex.quote(str(prefix / 'usr/share/kicad'))}\n"
              f"export LD_LIBRARY_PATH={shlex.quote(str(lib))}:${{LD_LIBRARY_PATH:-}}\n"
              f"export PYTHONPATH={shlex.quote(str(prefix / 'usr/lib/python3/dist-packages'))}:${{PYTHONPATH:-}}\n"
              f"export KICAD10_FOOTPRINT_DIR={shlex.quote(str(prefix / 'usr/share/kicad/footprints'))}\n"
              f"export KICAD10_SYMBOL_DIR={shlex.quote(str(prefix / 'usr/share/kicad/symbols'))}\n")
    for name, target in (("kicad-cli", str(prefix / "usr/bin/kicad-cli")),
                         ("pcb-kicad-python", "/usr/bin/python3")):
        path = binary / name
        path.write_text(common + f'exec {shlex.quote(target)} "$@"\n', encoding="utf-8")
        path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=ROOT / "tools/kicad-linux")
    parser.add_argument("--cache", type=Path, default=ROOT / "build/00-shared/tmp/linux-packages")
    args = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("This installer requires Linux with APT and a system Python.")
    prefix = args.prefix.resolve()
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    candidate = subprocess.check_output(["apt-cache", "policy", "kicad"], text=True)
    if not any(line.strip().startswith("Candidate: 10.") for line in candidate.splitlines()):
        raise SystemExit("Configure the official KiCad 10 PPA and run apt update first; see docs/LINUX.md.")
    # Print-only resolution uses no dpkg lock and makes no system changes.
    plan = subprocess.check_output([
        "apt-get", "-o", "Debug::NoLocking=1", "--print-uris", "--yes",
        "--download-only", "--no-install-recommends", "install", *PACKAGES,
    ], text=True)
    rows = [shlex.split(line) for line in plan.splitlines() if line.startswith("'")]
    names = [row[1].split("_")[0] for row in rows]
    metadata = subprocess.check_output(["apt-cache", "show", *names], text=True) if names else ""
    hashes = package_hashes(metadata)

    def download(row: list[str]) -> Path:
        url, name, size, _ = row
        repository_name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
        expected = hashes.get(repository_name)
        if not expected:
            raise RuntimeError(f"No authenticated SHA-256 metadata for {name}")
        path = cache / name
        data = path.read_bytes() if path.is_file() else urllib.request.urlopen(url, timeout=120).read()
        if len(data) != int(size) or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f"Package size/hash mismatch: {name}")
        path.write_bytes(data)
        return path

    prefix.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for index, path in enumerate(pool.map(download, rows), 1):
            subprocess.run(["dpkg-deb", "-x", str(path), str(prefix)], check=True)
            print(f"Verified/extracted {index}/{len(rows)}: {path.name}", flush=True)
    # An APT-installed KiCad is already usable without extraction.
    if not (prefix / "usr/bin/kicad-cli").is_file():
        raise SystemExit("No KiCad package was extracted; use the system-installed kicad-cli instead.")
    write_launchers(prefix)
    subprocess.run([str(prefix / "bin/kicad-cli"), "version"], check=True)
    subprocess.run([str(prefix / "bin/pcb-kicad-python"), "-c", "import pcbnew; print(pcbnew.Version())"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
