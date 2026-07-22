#!/usr/bin/env python3
"""Fetch one package directory from the official atopile/packages repository.

This is a registry-outage fallback.  It downloads only a selected package at
an immutable Git commit, so Windows never has to check out unrelated paths in
the packages monorepo.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = "atopile/packages"
API = f"https://api.github.com/repos/{REPOSITORY}/contents"
RAW = f"https://raw.githubusercontent.com/{REPOSITORY}"


def fetch(url: str, timeout: int, accept: str) -> bytes:
    last_error: OSError | None = None
    for attempt in range(4):
        request = Request(url, headers={"Accept": accept, "User-Agent": "pcb-project-template"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except OSError as error:
            last_error = error
            if attempt == 3:
                break
            time.sleep(1 << attempt)
    raise RuntimeError(f"download failed after retries: {url}: {last_error}")


def get_json(url: str) -> object:
    return json.loads(fetch(url, timeout=30, accept="application/vnd.github+json"))


def get_bytes(url: str) -> bytes:
    return fetch(url, timeout=60, accept="application/octet-stream")


def package_files(package_path: str, revision: str) -> list[dict[str, str]]:
    pending = [package_path]
    files: list[dict[str, str]] = []
    while pending:
        directory = pending.pop()
        listing = get_json(f"{API}/{quote(directory)}?ref={quote(revision)}")
        if not isinstance(listing, list):
            raise RuntimeError(f"GitHub did not return a directory listing for {directory}")
        for item in listing:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            path = item.get("path")
            if not isinstance(path, str):
                continue
            if item_type == "dir":
                pending.append(path)
            elif item_type == "file":
                download_url = item.get("download_url")
                if not isinstance(download_url, str):
                    raise RuntimeError(f"No download URL for {path}")
                blob_sha = item.get("sha")
                if not isinstance(blob_sha, str):
                    raise RuntimeError(f"No Git blob SHA for {path}")
                files.append({"path": path, "download_url": download_url, "blob_sha": blob_sha})
    return sorted(files, key=lambda item: item["path"])


def download_file(destination: Path, url: str) -> str:
    content = get_bytes(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, destination)
    return hashlib.sha256(content).hexdigest()


def git_blob_sha256(path: Path) -> str:
    content = path.read_bytes()
    header = f"blob {len(content)}\\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="Package directory name, e.g. raspberry-rp2040")
    parser.add_argument("--revision", required=True, help="Immutable upstream Git commit SHA")
    parser.add_argument("--destination", default="tools/atopile-package-cache")
    parser.add_argument("--jobs", type=int, default=8, help="Concurrent downloads (default: 8)")
    args = parser.parse_args()

    if not args.package or any(part in {"", ".", ".."} for part in args.package.split("/")):
        raise SystemExit("package must be a single safe package directory name")
    if len(args.revision) != 40 or any(char not in "0123456789abcdef" for char in args.revision.lower()):
        raise SystemExit("revision must be a 40-character Git commit SHA")

    source_prefix = f"packages/{args.package}"
    if args.jobs < 1 or args.jobs > 16:
        raise SystemExit("jobs must be between 1 and 16")
    destination_root = Path(args.destination).resolve()
    package_root = destination_root / args.package
    files = package_files(source_prefix, args.revision)
    if not files:
        raise SystemExit(f"No files found at {source_prefix}@{args.revision}")

    def fetch_item(item: dict[str, str]) -> tuple[str, str, bool]:
        relative = Path(item["path"]).relative_to(source_prefix)
        destination = package_root / relative
        if destination.is_file() and git_blob_sha256(destination) == item["blob_sha"]:
            return relative.as_posix(), hashlib.sha256(destination.read_bytes()).hexdigest(), True
        return relative.as_posix(), download_file(destination, item["download_url"]), False

    hashes: dict[str, str] = {}
    downloaded = 0
    reused = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for relative, digest, was_reused in executor.map(fetch_item, files):
            hashes[relative] = digest
            reused += was_reused
            downloaded += not was_reused

    receipt = {
        "schema": 1,
        "repository": REPOSITORY,
        "revision": args.revision,
        "package": args.package,
        "files": hashes,
    }
    receipt_path = package_root / ".atopile-github-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"package_root": str(package_root), "files": len(files), "downloaded": downloaded, "reused": reused, "receipt": str(receipt_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
