#!/usr/bin/env python3
"""Check tracked-source hygiene before publishing; this is not a history audit."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "docs/QUICKSTART.md", "docs/THIRD_PARTY.md")
FORBIDDEN_PATHS = {
    ".codex/config.toml", ".codex/pcb-mcp.fragment.toml",
    "handoff/stages/CURRENT_STAGE.json",
}
PRIVATE_NAMES = {"id_rsa", "id_ed25519", ".env", "credentials.json", "secrets.json"}
PRIVATE_SUFFIXES = {".p12", ".pfx", ".pem", ".key", ".keystore"}
FORBIDDEN_DIRS = {"build", "manufacturing", ".ato", "__pycache__", ".venv-routingtools"}
TEXT_SUFFIXES = {".py", ".json", ".toml", ".md", ".yaml", ".yml", ".txt", ".sh", ".mjs", ".js", ".tsx", ".ato", ".gitignore"}
SECRET_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GitHub access token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}")),
    ("AWS access key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("possible OpenAI secret", re.compile(r"sk-[A-Za-z0-9]{32,}")),
)


def tracked_paths(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True,
    )
    return [path.decode("utf-8", errors="surrogateescape") for path in proc.stdout.split(b"\0") if path]


def scan_paths(root: Path, paths: list[str]) -> list[str]:
    problems: list[str] = []
    for raw in paths:
        path = Path(raw)
        normalized = path.as_posix()
        pieces = path.parts
        if normalized in FORBIDDEN_PATHS or any(part in FORBIDDEN_DIRS for part in pieces):
            problems.append(f"{normalized}: generated/private path tracked")
            continue
        if path.name in PRIVATE_NAMES or path.suffix.lower() in PRIVATE_SUFFIXES or (path.name.startswith(".env.") and path.name != ".env.example"):
            problems.append(f"{normalized}: credential-like filename tracked")
            continue
        file = root / path
        if not file.is_file() or (file.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".gitignore", "AGENTS.md"}):
            continue
        try:
            content = file.read_text(encoding="utf-8")
        except UnicodeError:
            problems.append(f"{normalized}: text file is not valid UTF-8")
            continue
        if path.suffix.lower() == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                problems.append(f"{normalized}: invalid JSON at line {exc.lineno}")
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                problems.append(f"{normalized}:{line}: {label} pattern")
    return problems


def main() -> int:
    tracked = tracked_paths(ROOT)
    failures = scan_paths(ROOT, tracked)
    failures.extend(f"missing required file: {path}" for path in REQUIRED if not (ROOT / path).is_file())
    print(json.dumps({
        "ok": not failures,
        "tracked_files": len(tracked),
        "issues": failures,
        "limits": "Current tracked checkout only: no history scan, license audit or physical PCB verification.",
    }, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
