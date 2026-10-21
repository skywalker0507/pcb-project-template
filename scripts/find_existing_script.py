#!/usr/bin/env python3
"""Find existing project scripts before creating another helper."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def normalized_terms(query: list[str]) -> list[str]:
    return [term.lower() for value in query for term in re.findall(r"[A-Za-z0-9_+-]+", value) if term]


def description(path: Path) -> str:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        value = ast.get_docstring(tree, clean=True) or ""
    except (OSError, SyntaxError):
        value = ""
    return " ".join(value.split())[:240]


def search(query: list[str], *, root: Path = ROOT, limit: int = 12) -> dict[str, Any]:
    terms = normalized_terms(query)
    if not terms:
        raise SystemExit("Provide at least one script-search term")
    matches: list[tuple[int, str, str]] = []
    for path in (root / "scripts").glob("*.py"):
        relative = path.relative_to(root).as_posix()
        summary = description(path)
        filename = path.stem.lower()
        haystack = f"{filename} {summary.lower()}"
        score = sum(5 if term in filename else 1 if term in haystack else 0 for term in terms)
        if score:
            matches.append((score, relative, summary))
    matches.sort(key=lambda item: (-item[0], item[1]))
    selected = matches[: max(1, min(limit, 30))]
    return {
        "schema": 1,
        "query": terms,
        "match_count": len(matches),
        "results": [{"path": path, "description": summary} for _, path, summary in selected],
        "truncated": len(matches) > len(selected),
        "instruction": "Inspect the best matching script before creating a new helper.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="+")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(search(args.query, limit=args.limit), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
