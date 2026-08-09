#!/usr/bin/env python3
"""Stage-scoped build artifact store with disposable, evidence and release lanes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/build-artifacts.json"
CURRENT_INDEX = ROOT / "build/CURRENT.json"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Artifact configuration is unavailable or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise SystemExit(f"Unsupported artifact configuration: {path}")
    return value


def config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    return read_json(path.resolve())


def safe_name(value: str, label: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise SystemExit(f"{label} must contain only letters, numbers, dot, dash, or underscore")
    return value


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def require_under(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise SystemExit(f"{label} must stay under {parent.resolve()}: {resolved}") from exc
    return resolved


def stage_directory(stage: str, settings: dict[str, Any] | None = None) -> Path:
    settings = settings or config()
    label = (settings.get("stages") or {}).get(stage)
    if not isinstance(label, str):
        raise SystemExit(f"Unknown artifact stage: {stage}")
    return ROOT / str(settings.get("root", "build")) / label


def default_run_id(operation: str) -> str:
    safe_operation = re.sub(r"[^A-Za-z0-9._-]+", "-", operation).strip("-") or "run"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{safe_operation}"


@dataclass(frozen=True)
class ArtifactRun:
    stage: str
    stage_label: str
    run_id: str
    operation: str
    tmp: Path
    evidence: Path
    release: Path
    manifest: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "stage_label": self.stage_label,
            "run_id": self.run_id,
            "operation": self.operation,
            "tmp": str(self.tmp),
            "evidence": str(self.evidence),
            "release": str(self.release),
            "manifest": str(self.manifest),
        }


def create_run(
    stage: str,
    operation: str,
    run_id: str | None = None,
    *,
    settings: dict[str, Any] | None = None,
    allow_existing: bool = False,
) -> ArtifactRun:
    settings = settings or config()
    stage_root = stage_directory(stage, settings)
    stage_label = stage_root.name
    run_id = safe_name(run_id or default_run_id(operation), "Run id")
    lanes = settings.get("lanes") or {}
    tmp = stage_root / str(lanes.get("tmp", "tmp")) / run_id
    evidence = stage_root / str(lanes.get("evidence", "evidence")) / run_id
    release = stage_root / str(lanes.get("release", "release")) / run_id
    existing = [path for path in (tmp, evidence, release) if path.exists()]
    if existing and not allow_existing:
        raise SystemExit("Artifact run already exists: " + ", ".join(str(path) for path in existing))
    tmp.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    manifest = evidence / "run.json"
    run = ArtifactRun(stage, stage_label, run_id, operation, tmp, evidence, release, manifest)
    if not manifest.exists():
        write_manifest(run, "in_progress", success=None)
    CURRENT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_INDEX.write_text(
        json.dumps({"schema": 1, **run.as_dict()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return run


def write_manifest(
    run: ArtifactRun,
    status: str,
    *,
    success: bool | None,
    artifacts: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    previous: dict[str, Any] = {}
    if run.manifest.is_file():
        try:
            previous = json.loads(run.manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    payload = {
        "schema": 1,
        "kind": "artifact_run",
        "stage": run.stage,
        "stage_label": run.stage_label,
        "run_id": run.run_id,
        "operation": run.operation,
        "status": status,
        "success": success,
        "created_at": previous.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "lanes": {
            "tmp": relative(run.tmp),
            "evidence": relative(run.evidence),
            "release": relative(run.release),
        },
        "artifacts": artifacts or previous.get("artifacts", {}),
        "metrics": metrics or previous.get("metrics", {}),
        "invariants": [
            "tmp is disposable and never release evidence",
            "evidence is compact and independently verifiable",
            "release is populated only after the relevant acceptance gate passes",
        ],
    }
    run.manifest.parent.mkdir(parents=True, exist_ok=True)
    run.manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finish_run(
    run: ArtifactRun,
    success: bool,
    *,
    artifacts: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    write_manifest(run, "complete" if success else "failed", success=success, artifacts=artifacts, metrics=metrics)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def inventory(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or config()
    root = ROOT / str(settings.get("root", "build"))
    stages: dict[str, Any] = {}
    for stage, label in (settings.get("stages") or {}).items():
        stage_root = root / str(label)
        lane_counts = {}
        for lane_name, lane_dir in (settings.get("lanes") or {}).items():
            path = stage_root / str(lane_dir)
            files = [item for item in path.rglob("*") if item.is_file()] if path.is_dir() else []
            lane_counts[lane_name] = {
                "files": len(files),
                "bytes": sum(item.stat().st_size for item in files),
            }
        stages[stage] = {"label": label, "lanes": lane_counts}
    legacy_names = {"generated", "candidates", "logs", "reports", "reviews", "delegated", "builds", "renders"}
    legacy = []
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.name not in legacy_names:
                continue
            files = [item for item in path.rglob("*") if item.is_file()] if path.is_dir() else [path]
            legacy.append({"path": relative(path), "files": len(files), "bytes": sum(item.stat().st_size for item in files)})
    shared_root = root / str(settings.get("shared", "00-shared"))
    shared_files = [item for item in shared_root.rglob("*") if item.is_file()] if shared_root.is_dir() else []
    return {
        "schema": 1,
        "shared": {"path": relative(shared_root), "files": len(shared_files), "bytes": sum(item.stat().st_size for item in shared_files)},
        "stages": stages,
        "legacy": legacy,
    }


def _preserved(path: Path, preserved: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == item or item in resolved.parents or resolved in item.parents for item in preserved)


def archive_legacy(settings: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    legacy = settings.get("legacy") or {}
    archive_root = require_under(ROOT / str(legacy.get("archive_root", "build/00-legacy")), ROOT / "build", "Legacy archive")
    guard_value = legacy.get("release_guard")
    snapshot = (ROOT / str(guard_value)).resolve() if guard_value else None
    archive_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = archive_root / archive_id
    preserved = [require_under(ROOT / value, ROOT / "build", "Preserved artifact") for value in legacy.get("preserve", [])]
    moves: list[tuple[Path, Path]] = []
    for value in legacy.get("whole_directories", []):
        source = require_under(ROOT / value, ROOT / "build", "Legacy source")
        if source.exists():
            moves.append((source, destination / source.relative_to(ROOT / "build")))
    for value in legacy.get("whole_files", []):
        source = require_under(ROOT / value, ROOT / "build", "Legacy source")
        if source.is_file():
            moves.append((source, destination / source.relative_to(ROOT / "build")))
    for value in legacy.get("partial_directories", []):
        source_root = require_under(ROOT / value, ROOT / "build", "Legacy source")
        if not source_root.is_dir():
            continue
        for source in sorted((item for item in source_root.rglob("*") if item.is_file()), key=lambda item: len(item.parts), reverse=True):
            if not _preserved(source, preserved):
                moves.append((source, destination / source.relative_to(ROOT / "build")))
    preview = [{
        "from": relative(source),
        "to": relative(target),
        "bytes": source.stat().st_size if source.is_file() else sum(item.stat().st_size for item in source.rglob("*") if item.is_file()),
    } for source, target in moves]
    if apply and not moves:
        for value in legacy.get("partial_directories", []):
            root = ROOT / value
            if root.is_dir():
                try:
                    root.rmdir()
                except OSError:
                    pass
        return {
            "success": True, "applied": True, "destination": None,
            "move_count": 0, "bytes": 0, "preview": [], "truncated": False,
        }
    archive_file: Path | None = None
    if apply:
        destination.mkdir(parents=True, exist_ok=False)
        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        for value in legacy.get("partial_directories", []):
            root = ROOT / value
            if root.is_dir():
                for directory in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        manifest = {
            "schema": 1,
            "kind": "legacy_artifact_archive",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "recoverable": True,
            "immutable_release_guard": relative(snapshot) if snapshot and snapshot.is_file() else None,
            "moves": preview,
        }
        (destination / "archive-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if legacy.get("compress", True):
            archive_file = destination.with_suffix(".zip")
            if archive_file.exists():
                raise SystemExit(f"Refusing to overwrite legacy archive: {archive_file}")
            with zipfile.ZipFile(archive_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for item in sorted(path for path in destination.rglob("*") if path.is_file()):
                    bundle.write(item, item.relative_to(destination))
            with zipfile.ZipFile(archive_file, "r") as bundle:
                corrupt = bundle.testzip()
                if corrupt is not None or not bundle.namelist():
                    archive_file.unlink(missing_ok=True)
                    raise SystemExit(f"Legacy archive verification failed: {corrupt or 'empty archive'}")
            sidecar = archive_file.with_suffix(".manifest.json")
            sidecar.write_text(json.dumps({
                **manifest,
                "archive": relative(archive_file),
                "archive_sha256": sha256(archive_file),
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            shutil.rmtree(destination)
    return {
        "success": True,
        "applied": apply,
        "destination": relative(archive_file or destination),
        "move_count": len(preview),
        "bytes": sum(item.get("bytes") or 0 for item in preview),
        "preview": preview[:20],
        "truncated": len(preview) > 20,
    }


def register_release(
    release_id: str,
    snapshot_manifest: Path,
    audit_path: Path,
    review_path: Path,
    package_audit_path: Path,
) -> dict[str, Any]:
    """Create a compact release index only from mutually SHA-bound passing evidence."""
    snapshot_manifest = snapshot_manifest.resolve()
    audit_path = audit_path.resolve()
    review_path = review_path.resolve()
    package_audit_path = package_audit_path.resolve()
    snapshot = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    package_audit = json.loads(package_audit_path.read_text(encoding="utf-8"))
    board_sha = str(snapshot.get("board_sha256", "")).upper()
    failures: list[str] = []
    if snapshot.get("kind") != "release_snapshot" or snapshot.get("release_pass") is not True:
        failures.append("snapshot_not_release_pass")
    if audit.get("release_pass") is not True or str(audit.get("board_sha256", "")).upper() != board_sha:
        failures.append("audit_not_passing_or_board_mismatch")
    if review.get("review_pass") is not True or str(review.get("board_sha256", "")).upper() != board_sha:
        failures.append("physical_review_not_passing_or_board_mismatch")
    if package_audit.get("success") is not True or package_audit.get("failures"):
        failures.append("package_audit_not_passing")
    snapshot_root = snapshot_manifest.parent
    for name, expected in (snapshot.get("sha256") or {}).items():
        artifact = snapshot_root / str(name)
        if not artifact.is_file() or sha256(artifact) != str(expected).upper():
            failures.append(f"snapshot_hash_mismatch:{name}")
            break
    if failures:
        raise SystemExit(json.dumps({"success": False, "failures": failures}, separators=(",", ":")))

    run = create_run("release", "register-release", release_id)
    run.release.mkdir(parents=True, exist_ok=False)
    copies = {
        "snapshot-manifest.json": snapshot_manifest,
        "final-routing-audit.json": audit_path,
        "physical-review.json": review_path,
        "jlcpcb-package-audit.json": package_audit_path,
    }
    artifacts: dict[str, str] = {}
    for name, source in copies.items():
        target = run.release / name
        shutil.copy2(source, target)
        artifacts[name] = relative(target)
    index = run.release / "release-index.json"
    index.write_text(json.dumps({
        "schema": 1,
        "kind": "compact_release_index",
        "release_id": release_id,
        "board_sha256": board_sha,
        "release_pass": True,
        "immutable_snapshot": relative(snapshot_manifest.parent),
        "immutable_snapshot_manifest_sha256": sha256(snapshot_manifest),
        "files": {name: {"path": path, "sha256": sha256(run.release / name)} for name, path in artifacts.items()},
        "note": "Manufacturing files remain in the immutable snapshot; this build lane stores compact verified references only.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts["release-index.json"] = relative(index)
    finish_run(run, True, artifacts=artifacts, metrics={"board_sha256": board_sha, "release_pass": True})
    return {"success": True, "release_id": release_id, "board_sha256": board_sha, "release": relative(run.release), "index": relative(index)}


def prune_tmp(settings: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    """Archive expired disposable runs; never touch evidence or release lanes."""
    max_age = float((settings.get("retention") or {}).get("tmp_max_age_hours", 24))
    cutoff = datetime.now(timezone.utc).timestamp() - max_age * 3600
    current_tmp: Path | None = None
    if CURRENT_INDEX.is_file():
        try:
            current_tmp = Path(json.loads(CURRENT_INDEX.read_text(encoding="utf-8")).get("tmp", "")).resolve()
        except (json.JSONDecodeError, OSError):
            current_tmp = None
    candidates: list[Path] = []
    for stage in (settings.get("stages") or {}):
        tmp_root = stage_directory(stage, settings) / str((settings.get("lanes") or {}).get("tmp", "tmp"))
        if not tmp_root.is_dir():
            continue
        for run_dir in tmp_root.iterdir():
            if run_dir.is_dir() and run_dir.resolve() != current_tmp and run_dir.stat().st_mtime < cutoff:
                candidates.append(run_dir.resolve())
    preview = [{
        "path": relative(path),
        "bytes": sum(item.stat().st_size for item in path.rglob("*") if item.is_file()),
    } for path in sorted(candidates)]
    if not apply or not candidates:
        return {"success": True, "applied": apply, "run_count": len(candidates), "bytes": sum(item["bytes"] for item in preview), "preview": preview[:20], "truncated": len(preview) > 20}
    archive_root = ROOT / str((settings.get("legacy") or {}).get("archive_root", "build/00-legacy"))
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = archive_root / f"retention-{stamp}.zip"
    manifest = {
        "schema": 1, "kind": "expired_tmp_archive", "created_at": datetime.now(timezone.utc).isoformat(),
        "max_age_hours": max_age, "runs": preview,
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.writestr("retention-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for run_dir in candidates:
            require_under(run_dir, ROOT / "build", "Expired tmp run")
            for item in sorted(path for path in run_dir.rglob("*") if path.is_file()):
                bundle.write(item, item.relative_to(ROOT / "build"))
    with zipfile.ZipFile(archive, "r") as bundle:
        corrupt = bundle.testzip()
        if corrupt is not None:
            archive.unlink(missing_ok=True)
            raise SystemExit(f"Retention archive verification failed: {corrupt}")
    for run_dir in candidates:
        shutil.rmtree(run_dir)
    sidecar = archive.with_suffix(".manifest.json")
    sidecar.write_text(json.dumps({**manifest, "archive": relative(archive), "archive_sha256": sha256(archive)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"success": True, "applied": True, "archive": relative(archive), "run_count": len(candidates), "bytes": sum(item["bytes"] for item in preview)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--stage", required=True)
    create.add_argument("--operation", required=True)
    create.add_argument("--run-id")
    commands.add_parser("inventory")
    archive = commands.add_parser("archive-legacy")
    archive.add_argument("--apply", action="store_true")
    release = commands.add_parser("register-release")
    release.add_argument("--release-id", required=True)
    release.add_argument("--snapshot-manifest", type=Path, required=True)
    release.add_argument("--audit", type=Path, required=True)
    release.add_argument("--physical-review", type=Path, required=True)
    release.add_argument("--package-audit", type=Path, required=True)
    prune = commands.add_parser("prune")
    prune.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = config(args.config)
    if args.command == "create":
        result = create_run(args.stage, args.operation, args.run_id, settings=settings)
        payload: dict[str, Any] = {"success": True, **result.as_dict()}
    elif args.command == "archive-legacy":
        payload = archive_legacy(settings, apply=args.apply)
    elif args.command == "register-release":
        payload = register_release(args.release_id, args.snapshot_manifest, args.audit, args.physical_review, args.package_audit)
    elif args.command == "prune":
        payload = prune_tmp(settings, apply=args.apply)
    else:
        payload = inventory(settings)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
