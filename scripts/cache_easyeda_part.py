#!/usr/bin/env python3
"""Fetch one reviewed EasyEDA part into atopile's local cache without proxies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LCSC_RE = re.compile(r"C[1-9][0-9]*")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch reviewed EasyEDA CAD JSON using an explicit direct connection and "
            "store it in atopile's build/cache/parts/easyeda layout."
        )
    )
    parser.add_argument("--lcsc", required=True)
    parser.add_argument("--manufacturer", required=True)
    parser.add_argument("--datasheet-url", required=True)
    parser.add_argument(
        "--cache-root", type=Path, default=ROOT / "build/cache/parts/easyeda"
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not LCSC_RE.fullmatch(args.lcsc):
        raise SystemExit(f"Invalid LCSC identifier: {args.lcsc!r}")
    datasheet = urllib.parse.urlparse(args.datasheet_url)
    if datasheet.scheme != "https" or not datasheet.netloc:
        raise SystemExit("--datasheet-url must be an absolute HTTPS URL")

    output = args.cache_root.resolve() / args.lcsc / f"{args.lcsc}.json"
    if output.exists() and not args.force:
        raise SystemExit(f"Cache already exists; use --force to refresh: {output}")

    url = (
        f"https://easyeda.com/api/products/{args.lcsc}/components"
        "?version=6.4.19.5"
    )
    # ProxyHandler({}) deliberately ignores HTTP(S)_PROXY inherited from the shell.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"User-Agent": "pcb-template/1"})
    with opener.open(request, timeout=45) as response:
        payload = json.load(response)

    if payload.get("success") is not True or not isinstance(payload.get("result"), dict):
        raise SystemExit("EasyEDA returned an unsuccessful or malformed response")
    result = payload["result"]
    returned_id = str(result.get("lcsc", {}).get("number", ""))
    if returned_id != args.lcsc:
        raise SystemExit(
            f"EasyEDA identity mismatch: requested {args.lcsc}, returned {returned_id!r}"
        )

    result["atopile_queried_at"] = time.time()
    result["atopile_manufacturer"] = args.manufacturer
    result["atopile_datasheet_url"] = args.datasheet_url
    encoded = (json.dumps(result, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)

    print(
        json.dumps(
            {
                "success": True,
                "lcsc": args.lcsc,
                "network_mode": "direct_no_proxy",
                "output": str(output),
                "sha256": hashlib.sha256(encoded).hexdigest().upper(),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
