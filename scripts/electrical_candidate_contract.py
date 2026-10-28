"""Validate exact-candidate integrity evidence for build reuse and freezing."""

import json
from pathlib import Path

from electrical_backend import ROOT, sha256
from electrical_build_identity import require_compiler_reuse


def require_candidate_normalization(build: dict, root: Path | None = None) -> Path | None:
    """An optional export phase, once recorded, must remain valid for reuse."""
    root = (root or ROOT).resolve()
    binding = build.get("normalization")
    if binding is None:
        return None
    if not isinstance(binding, dict):
        raise ValueError("Candidate normalization binding must be an object")
    path = (root / str(binding.get("receipt", ""))).resolve()
    path.relative_to(root / "build")
    if not path.is_file() or sha256(path) != binding.get("sha256"):
        raise ValueError("Candidate normalization receipt is unavailable or changed")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or payload.get("kind") != "atopile_candidate_normalization"
            or payload.get("success") is not True or payload.get("exact_electrical_geometry_parity") is not True
            or payload.get("candidate_board_sha256") != build.get("candidate_board_sha256")
            or payload.get("compiler_candidate_sha256") != build.get("compiler_candidate_board_sha256")
            or payload.get("source_sha256") != (build.get("contract") or {}).get("source_sha256")):
        raise ValueError("Candidate normalization evidence is not valid for this exact build")
    return path


def require_candidate_integrity(build: dict, root: Path | None = None) -> Path:
    root = (root or ROOT).resolve()
    binding = build.get("candidate_integrity")
    if not isinstance(binding, dict):
        raise ValueError("Electrical build has no candidate integrity evidence; rebuild with the current gate")
    path = (root / str(binding.get("receipt", ""))).resolve()
    path.relative_to(root / "build")
    if not path.is_file() or sha256(path) != binding.get("sha256"):
        raise ValueError("Candidate integrity receipt is unavailable or changed")
    audit = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(audit, dict):
        raise ValueError("Candidate integrity receipt must be a JSON object")
    board_hash = build.get("candidate_board_sha256")
    source_hash = (build.get("contract") or {}).get("source_sha256")
    if (audit.get("kind") != "electrical_candidate_integrity" or audit.get("success") is not True
            or audit.get("read_only_inputs_unchanged") is not True
            or not board_hash or audit.get("candidate_board_sha256") != board_hash
            or not source_hash or audit.get("source_sha256") != source_hash):
        raise ValueError("Candidate integrity evidence does not pass for this exact board and source")
    require_candidate_normalization(build, root)
    require_compiler_reuse(build, root)
    return path
