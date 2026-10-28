"""Validate optional durable project reference inputs without network or writes."""
from pathlib import Path
import json
import subprocess

from electrical_backend import sha256

MANIFEST = "config/source-references.json"


def durable_files(root: Path, paths: list[Path]) -> None:
    root = root.resolve()
    relative = []
    for path in paths:
        path = path.resolve()
        try:
            local = path.relative_to(root)
        except ValueError as exc:
            raise ValueError("Required reference inputs must stay inside the workspace") from exc
        if local.parts[0] in {"tmp", "build", ".git", "node_modules", ".ato"}:
            raise ValueError(f"Required reference input uses disposable storage: {local.as_posix()}")
        if not path.is_file():
            raise ValueError(f"Required reference input is unavailable: {local.as_posix()}")
        relative.append(local.as_posix())
    if relative and (root / ".git").exists():
        result = subprocess.run(["git", "check-ignore", "--no-index", "-z", "--stdin"], cwd=root,
                                input=("\0".join(relative)+"\0").encode("utf-8"), capture_output=True, check=False)
        if result.returncode not in {0, 1}:
            raise ValueError("Cannot check required reference inputs against Git ignore rules")
        ignored = result.stdout.decode("utf-8", errors="replace").split("\0")
        if ignored[0]:
            raise ValueError(f"Required reference input is Git-ignored: {ignored[0]}")


def inputs(root: Path) -> dict | None:
    manifest = root / MANIFEST
    if not manifest.exists():
        return None
    durable_files(root, [manifest])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != 1 or not isinstance(payload.get("files"), list) or not payload["files"]:
        raise ValueError("Reference manifest needs schema1 and a non-empty files list")
    files = []
    seen = set()
    declared = []
    for entry in payload["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]:
            raise ValueError("Reference entry needs an explicit workspace path")
        path = (root / entry["path"]).resolve()
        if path in seen:
            raise ValueError(f"Duplicate reference input: {entry['path']}")
        seen.add(path)
        if not isinstance(entry.get("scope"), str) or not entry["scope"]:
            raise ValueError(f"Reference entry needs a bounded scope: {entry['path']}")
        declared.append((path, entry))
    durable_files(root, [path for path, _ in declared])
    for path, entry in declared:
        actual = sha256(path)
        if entry.get("sha256") != actual:
            raise ValueError(f"Reference input hash mismatch: {entry['path']}")
        files.append({"path": path.relative_to(root.resolve()).as_posix(), "sha256": actual})
    return {"manifest": {"path": MANIFEST, "sha256": sha256(manifest)},
            "files": sorted(files, key=lambda item: item["path"]),
            "validator_sha256": sha256(Path(__file__))}


def require(build: dict, root: Path) -> dict | None:
    current = inputs(root)
    if current != build.get("source_references"):
        raise ValueError("Required source-reference evidence changed;revalidate the electrical candidate")
    return current
