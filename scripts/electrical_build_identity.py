"""Separate compiler inputs from validation inputs; verify historical manifests."""
import hashlib
import json
from pathlib import Path


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest().upper()


def compiler_inputs(manifest: dict) -> dict:
    backend = manifest["backend_config"]
    paths = {backend["driver"], "scripts/electrical_backend.py"}
    if manifest["backend"] == "atopile":
        paths.update({"ato.yaml", "scripts/validate_atopile_source.py"})
    dependencies = manifest["producer_dependencies"]
    by_path = {entry["path"]: entry["sha256"] for entry in dependencies}
    if len(by_path) != len(dependencies) or not paths.issubset(by_path):
        raise ValueError("Compiler identity is missing or duplicates required producer inputs")
    if "config/project-initialization.json" in by_path:
        paths.add("config/project-initialization.json")
    core = {
        "schema": 1, "driver_invocation_contract": 1,
        "backend": manifest["backend"], "build": manifest["build"], "project": manifest["project"],
        "backend_config": {key: value for key, value in backend.items() if key != "source_preflight"},
        "source_sha256": manifest["contract"]["source_sha256"],
        "producer_dependencies": [{"path": path, "sha256": by_path[path]} for path in sorted(paths)],
        "toolchain": manifest["toolchain"],
    }
    return {**core, "fingerprint": fingerprint(core)}


def recorded_compiler_inputs(manifest: dict) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema") not in {1, 2}:
        raise ValueError("Unsupported historical build-input manifest")
    core = {key: value for key, value in manifest.items() if key != "fingerprint"}
    if fingerprint(core) != manifest.get("fingerprint"):
        raise ValueError("Historical build-input fingerprint is not valid")
    try:
        result = compiler_inputs(manifest)
    except (KeyError, TypeError) as exc:
        raise ValueError("Historical build-input compiler proof is incomplete") from exc
    if manifest["schema"] == 2 and result != manifest.get("compiler_input"):
        raise ValueError("Historical compiler identity does not match the full manifest")
    return result


def require_compiler_reuse(build: dict, root: Path) -> Path | None:
    binding = build.get("compiler_reuse")
    if binding is None:
        return None
    if not isinstance(binding, dict):
        raise ValueError("Compiler reuse binding must be an object")
    path = (root / str(binding.get("origin_receipt", ""))).resolve()
    path.relative_to((root / "build/03-electrical/evidence").resolve())
    from electrical_backend import sha256
    if not path.is_file() or sha256(path) != binding.get("origin_receipt_sha256"):
        raise ValueError("Compiler origin receipt is missing or changed")
    origin = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(origin, dict) or origin.get("success") is not True or origin.get("compiler_reuse") is not None
            or origin.get("backend") != build.get("backend") or origin.get("compiler_version") != build.get("compiler_version")
            or origin.get("compiler_candidate_board_sha256", origin.get("candidate_board_sha256")) != build.get("compiler_candidate_board_sha256")
            or binding.get("compiler_candidate_sha256") != build.get("compiler_candidate_board_sha256")):
        raise ValueError("Compiler origin does not prove this raw compiler candidate")
    old = recorded_compiler_inputs(origin.get("build_input"))
    current = recorded_compiler_inputs(build.get("build_input"))
    if old != current or binding.get("compiler_input_fingerprint") != current["fingerprint"]:
        raise ValueError("Compiler reuse proof does not match current compiler inputs")
    return path
