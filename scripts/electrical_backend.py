"""Shared configuration and receipts for replaceable electrical backends."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/electrical-backend.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def source_sha256(path: Path) -> str:
    """Hash source names and contents without including generated dependencies."""
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
        return digest.hexdigest().upper()
    if not path.is_dir():
        raise SystemExit(f"Electrical source root is unavailable: {path}")
    for item in sorted((candidate for candidate in path.rglob("*") if candidate.is_file()), key=lambda item: item.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix()
        if relative.startswith(("node_modules/", "dist/", ".git/")):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper()


def read_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Electrical backend configuration is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise SystemExit("Electrical backend configuration must use schema 1")
    backends = config.get("backends")
    if not isinstance(backends, dict) or not backends:
        raise SystemExit("Electrical backend configuration needs a non-empty backends object")
    return config


def selected_backend(config: dict[str, Any], requested: str | None = None) -> tuple[str, dict[str, Any]]:
    name = requested or config.get("active")
    if not isinstance(name, str) or not name:
        raise SystemExit("Electrical backend configuration needs an active backend")
    backend = (config.get("backends") or {}).get(name)
    if not isinstance(backend, dict):
        raise SystemExit(f"Unknown electrical backend: {name}")
    for key in ("driver", "source_root", "entry", "runtime", "platform", "status"):
        if not isinstance(backend.get(key), str) or not backend[key]:
            raise SystemExit(f"Electrical backend {name!r} needs a non-empty {key!r}")
    return name, backend


def workspace_path(value: str, label: str) -> Path:
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit(f"{label} must stay inside the workspace: {value}") from exc
    return path


def source_entry(backend: dict[str, Any], entry: str | None = None, *, root: Path | None = None) -> tuple[Path, Path, str]:
    """Resolve the backend-relative entry once, including on the WSL side."""
    root = (root or ROOT).resolve()
    source = (root / backend["source_root"]).resolve()
    source.relative_to(root)
    filename, separator, symbol = (entry or backend["entry"]).partition(":")
    path = (source / filename).resolve()
    try:
        path.relative_to(source)
    except ValueError as exc:
        raise ValueError("Electrical entry must stay inside the configured source_root") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Electrical entry is unavailable: {path}")
    if separator and not symbol:
        raise ValueError("Electrical entry module is empty")
    return source, path, symbol


def footprint_libraries(source_root: Path) -> dict[str, Path]:
    """Discover local libraries recursively; ambiguous nicknames fail closed."""
    result: dict[str, Path] = {}
    for directory in sorted({path.parent.resolve() for path in source_root.rglob("*.kicad_mod")}):
        directory.relative_to(source_root.resolve())
        nickname = directory.name.removesuffix(".pretty")
        if nickname in result and result[nickname] != directory:
            raise ValueError(f"Ambiguous footprint library nickname {nickname!r}: {result[nickname]}, {directory}")
        result[nickname] = directory
    return result


def footprint_table(project_dir: Path, source_root: Path) -> str:
    libraries = footprint_libraries(source_root)
    if not libraries:
        raise ValueError(f"No local footprint libraries found under configured source_root: {source_root}")
    rows = ["(fp_lib_table", "  (version 7)"]
    for name, directory in sorted(libraries.items()):
        uri = "${KIPRJMOD}/" + Path(os.path.relpath(directory, project_dir)).as_posix()
        rows.append(f'  (lib (name {json.dumps(name, ensure_ascii=False)})(type "KiCad")(uri {json.dumps(uri, ensure_ascii=False)})(options "")(descr "Reviewed project footprint"))')
    return "\n".join([*rows, ")", ""])


def validate_backend(config: dict[str, Any], name: str, backend: dict[str, Any]) -> dict[str, Any]:
    driver = workspace_path(str(backend["driver"]), "Backend driver")
    source_root = workspace_path(str(backend["source_root"]), "Backend source_root")
    if not driver.is_file():
        raise SystemExit(f"Electrical backend {name!r} driver is unavailable: {driver}")
    if not source_root.exists():
        raise SystemExit(f"Electrical backend {name!r} source root is unavailable: {source_root}")
    entry = str(backend["entry"])
    _, separator, symbol = entry.partition(":")
    try:
        _, resolved_entry, _ = source_entry(backend)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    canonical_entry = f"{resolved_entry.relative_to(ROOT).as_posix()}{separator}{symbol}"
    if name == "atopile":
        ato_yaml = ROOT / "ato.yaml"
        try:
            text = ato_yaml.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"atopile project configuration is unavailable: {ato_yaml}: {exc}") from exc
        configured_entries = []
        for scalar in re.findall(r"^\s+entry:\s*(.+)$", text, re.MULTILINE):
            scalar = scalar.strip()
            if scalar.startswith('"'):
                try:
                    value, _ = json.JSONDecoder().raw_decode(scalar)
                except ValueError as exc:
                    raise SystemExit(f"Invalid quoted atopile entry: {scalar}") from exc
            elif scalar.startswith("'"):
                match = re.match(r"'((?:[^']|'')*)'", scalar)
                if not match:
                    raise SystemExit(f"Invalid quoted atopile entry: {scalar}")
                value = match.group(1).replace("''", "'")
            else:
                value = scalar.split(" #", 1)[0].strip()
            configured_entries.append(value)
        if canonical_entry not in configured_entries:
            raise SystemExit(
                f"atopile backend entry {canonical_entry!r} does not match an ato.yaml build entry: {configured_entries}"
            )
    return {
        "backend": name,
        "status": backend["status"],
        "platform": backend["platform"],
        "driver": str(driver.relative_to(ROOT).as_posix()),
        "source_root": str(source_root.relative_to(ROOT).as_posix()),
        "entry": canonical_entry,
        "source_sha256": source_sha256(source_root),
        "config_sha256": sha256(DEFAULT_CONFIG),
    }
