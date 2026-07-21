#!/usr/bin/env python3
"""Token-efficient, policy-driven adapter for KiCadRoutingTools.

The upstream checkout is treated as immutable. Full router output is written to
disk; stdout receives one compact JSON receipt suitable for an AI tool call.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


DEFAULT_CONFIG = Path("config/kicad-routing-tools.json")
PROTECTED_EXTRA_FLAGS = {
    "--layers", "--layer-costs", "--track-width", "--impedance",
    "--clearance", "--via-size", "--via-drill", "--fab-tier",
    "--fab-overrides", "--hole-to-hole-clearance",
    "--board-edge-clearance", "--enable-used-layers", "--overwrite",
}


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bounded(values: list[Any], limit: int) -> dict[str, Any]:
    limit = max(0, limit)
    return {
        "count": len(values),
        "preview": values[:limit],
        "truncated": len(values) > limit,
    }


def protected_extra_conflicts(values: list[str]) -> list[str]:
    normalized = {
        value.split("=", 1)[0].lower() for value in values if value.startswith("--")
    }
    return sorted(normalized & PROTECTED_EXTRA_FLAGS)


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_config(path: Path) -> tuple[dict[str, Any], Path]:
    path = path.resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema") != 1:
        raise SystemExit(f"Unsupported config schema: {config.get('schema')!r}")
    return config, path.parent.parent


def resolve(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def net_names(board: Path) -> list[str]:
    text = board.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r'\(net\s+(?:\d+\s+)?"([^"]+)"\)', text))
    return sorted(name for name in names if name)


def expand_nets(board: Path, includes: list[str], excludes: list[str]) -> list[str]:
    available = net_names(board)
    selected = {
        name
        for pattern in includes
        for name in available
        if fnmatch.fnmatchcase(name, pattern)
    }
    selected = {
        name for name in selected
        if not any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes)
    }
    return sorted(selected)


def sexpr_blocks(text: str, head: str):
    needle = f"({head}"
    offset = 0
    while True:
        start = text.find(needle, offset)
        if start < 0:
            return
        depth = 0
        quoted = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
            elif quoted and char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        yield text[start:index + 1]
                        offset = index + 1
                        break
        else:
            return


def board_metrics(board: Path, forbidden_layers: list[str]) -> dict[str, Any]:
    text = board.read_text(encoding="utf-8", errors="replace")
    counts: dict[str, int] = {}
    widths: set[float] = set()
    forbidden: dict[str, int] = {}
    for block in sexpr_blocks(text, "segment"):
        layer_match = re.search(r'\(layer\s+(?:"([^"]+)"|([^\s)]+))\)', block)
        if not layer_match:
            continue
        layer = layer_match.group(1) or layer_match.group(2)
        counts[layer] = counts.get(layer, 0) + 1
        width_match = re.search(r"\(width\s+([0-9.]+)\)", block)
        if width_match:
            widths.add(float(width_match.group(1)))
    for layer in forbidden_layers:
        if counts.get(layer):
            forbidden[layer] = counts[layer]
    vias = sum(1 for _ in sexpr_blocks(text, "via"))
    return {
        "segments": counts,
        "vias": vias,
        "widths_mm": sorted(widths),
        "forbidden_segments": forbidden,
    }


def parse_router_summaries(output: str) -> dict[str, Any] | None:
    parsed: list[dict[str, Any]] = []
    for line in output.splitlines():
        if "JSON_SUMMARY:" not in line:
            continue
        try:
            parsed.append(json.loads(line.split("JSON_SUMMARY:", 1)[1].strip()))
        except json.JSONDecodeError:
            continue
    if not parsed:
        return None
    # Reconciliation prints a smaller later summary. Start with the broadest
    # scope, then apply explicit later successes/failures to its single-net set.
    base = max(
        parsed,
        key=lambda item: (
            int(item.get("multipoint_pads_total", 0)),
            len(item.get("routed_single", [])) + len(item.get("failed_single", [])),
        ),
    )
    merged = dict(base)
    routed = set(base.get("routed_single", []))
    failed = set(base.get("failed_single", []))
    base_index = parsed.index(base)
    for item in parsed[base_index + 1:]:
        recovered = set(item.get("routed_single", []))
        still_failed = set(item.get("failed_single", []))
        routed.update(recovered)
        failed.difference_update(recovered)
        failed.update(still_failed)
        routed.difference_update(still_failed)
    merged["routed_single"] = sorted(routed)
    merged["failed_single"] = sorted(failed)
    merged["summary_record_count"] = len(parsed)
    return merged


def concise_router(summary: dict[str, Any] | None, max_items: int) -> dict[str, Any]:
    if not summary:
        return {"summary_found": False}
    failed_multi = summary.get("failed_multipoint", [])
    return {
        "summary_found": True,
        "routed_single": len(summary.get("routed_single", [])),
        "failed_single": bounded(summary.get("failed_single", []), max_items),
        "multipoint_pads_connected": summary.get("multipoint_pads_connected"),
        "multipoint_pads_total": summary.get("multipoint_pads_total"),
        "failed_multipoint_count": sum(
            len(item.get("failed_pads", [])) for item in failed_multi
        ),
        "vias_reported": summary.get("total_vias"),
        "time_s": summary.get("total_time"),
        "summary_record_count": summary.get("summary_record_count", 1),
    }


def concise_metrics(metrics: dict[str, Any] | None, max_items: int) -> dict[str, Any] | None:
    if not metrics:
        return None
    segments = sorted(metrics.get("segments", {}).items())
    widths = metrics.get("widths_mm", [])
    return {
        "segment_layers": bounded(segments, max_items),
        "vias": metrics.get("vias"),
        "widths_mm": bounded(widths, max_items),
        "forbidden_segments": metrics.get("forbidden_segments", {}),
    }


def concise_audit(audit: dict[str, Any] | None, max_items: int) -> dict[str, Any] | None:
    if not audit:
        return None
    categories = sorted((audit.get("drc_categories") or {}).items())
    return {
        "exit_code": audit.get("exit_code"),
        "baseline_pass": audit.get("baseline_pass"),
        "unconnected": audit.get("unconnected"),
        "in1_non_ground_segments": audit.get("in1_non_ground_segments"),
        "new_violation_count": audit.get("new_violation_count"),
        "drc_categories": bounded(categories, max_items),
        "report": audit.get("report"),
    }


def tool_state(config: dict[str, Any], root: Path) -> dict[str, Any]:
    tool = config["tool"]
    repository = resolve(root, tool["repository"])
    python = resolve(root, tool["python"])
    router = repository / tool.get("router_script", "route.py")
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    actual_commit = commit.stdout.strip() if commit.returncode == 0 else None
    expected_commit = tool.get("commit")
    import_check = subprocess.run(
        [
            str(python), "-c",
            "import numpy, scipy, shapely; "
            f"import sys; sys.path.insert(0, {str(repository)!r}); "
            "import rust_router.grid_router",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    ) if python.is_file() else None
    help_check = subprocess.run(
        [str(python), str(router), "--help"],
        cwd=repository, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    ) if python.is_file() and router.is_file() else None
    required_flags = {
        "--layers", "--layer-costs", "--no-fix-drc-settings",
        "--no-clamp-netclasses", "--fab-overrides",
    }
    help_text = help_check.stdout if help_check and help_check.returncode == 0 else ""
    missing_flags = sorted(flag for flag in required_flags if flag not in help_text)
    return {
        "ok": bool(
            repository.is_dir()
            and python.is_file()
            and router.is_file()
            and expected_commit
            and actual_commit
            and actual_commit == expected_commit
            and import_check is not None and import_check.returncode == 0
            and help_check is not None and help_check.returncode == 0
            and not missing_flags
        ),
        "repository": str(repository),
        "python": str(python),
        "router": str(router),
        "expected_commit": expected_commit,
        "actual_commit": actual_commit,
        "python_import_ok": bool(import_check and import_check.returncode == 0),
        "missing_cli_flags": missing_flags,
    }


def build_command(
    config: dict[str, Any], root: Path, profile: dict[str, Any],
    source: Path, output: Path, nets: list[str],
) -> list[str]:
    state = tool_state(config, root)
    if not state["ok"]:
        raise SystemExit(compact({"success": False, "error": "tool_check_failed", **state}))
    command = [state["python"], state["router"], str(source), str(output), *nets]
    routing = profile["routing"]
    required_numeric = ("track_width", "clearance", "via_size", "via_drill")
    missing = [key for key in required_numeric if routing.get(key) is None]
    if missing:
        raise SystemExit(
            "Explicit project routing values are required: " + ", ".join(missing)
        )
    layers = routing["layers"]
    costs = routing["layer_costs"]
    if len(layers) != len(costs):
        raise SystemExit("routing.layers and routing.layer_costs must have equal length")
    command += ["--layers", *map(str, layers), "--layer-costs", *map(str, costs)]
    options = {
        "track_width": "--track-width",
        "clearance": "--clearance",
        "via_size": "--via-size",
        "via_drill": "--via-drill",
        "grid_step": "--grid-step",
        "max_ripup": "--max-ripup",
        "hole_to_hole_clearance": "--hole-to-hole-clearance",
        "board_edge_clearance": "--board-edge-clearance",
        "max_iterations": "--max-iterations",
    }
    for key, flag in options.items():
        value = routing.get(key)
        if value is not None:
            command += [flag, str(value)]
    fab_overrides = routing.get("fab_overrides")
    if fab_overrides:
        command += ["--fab-overrides", str(resolve(root, fab_overrides))]
    command += ["--no-fix-drc-settings", "--no-clamp-netclasses"]
    extra_args = [str(value) for value in routing.get("extra_args", [])]
    conflicts = protected_extra_conflicts(extra_args)
    if conflicts:
        raise SystemExit(
            "extra_args may not override protected policy flags: " + ", ".join(conflicts)
        )
    command += extra_args
    return command


def run_audit(
    config: dict[str, Any], root: Path, source: Path, output: Path, log_path: Path,
) -> dict[str, Any] | None:
    audit = config.get("audit", {})
    command_template = audit.get("command")
    if not audit.get("enabled") or not command_template:
        return None
    substitutions = {
        "input": str(source), "output": str(output), "root": str(root),
    }
    command = [str(item).format(**substitutions) for item in command_template]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n===== POST-ROUTE AUDIT =====\n")
        handle.write(result.stdout)
        handle.write(result.stderr)
    parsed = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and (
            "board_sha256" in candidate or "baseline_pass" in candidate
        ):
            parsed = candidate
            break
    return {
        "exit_code": result.returncode,
        "baseline_pass": parsed.get("baseline_pass") if parsed else None,
        "unconnected": (
            (parsed or {}).get("unconnected_after")
            if (parsed or {}).get("unconnected_after") is not None
            else (parsed or {}).get("drc_categories", {}).get("unconnected_items")
        ),
        "in1_non_ground_segments": (parsed or {}).get("in1_non_ground_segments"),
        "drc_categories": (parsed or {}).get("drc_categories"),
        "new_violation_count": (parsed or {}).get("new_violation_count"),
        "report": (parsed or {}).get("report"),
    }


def command_check(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    state = tool_state(config, root)
    board = resolve(root, args.input) if args.input else None
    max_items = int(config.get("artifacts", {}).get("receipt_max_items", 8))
    result = {
        "success": state["ok"] and (board is None or board.is_file()),
        "tool": {
            "expected_commit": state["expected_commit"],
            "actual_commit": state["actual_commit"],
            "python_import_ok": state["python_import_ok"],
            "missing_cli_flags": state["missing_cli_flags"],
        },
        "input": str(board) if board else None,
        "input_sha256": sha256(board) if board and board.is_file() else None,
        "profiles": bounded(sorted(config.get("profiles", {})), max_items),
    }
    print(compact(result))
    return 0 if result["success"] else 1


def command_route(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    if args.profile not in config.get("profiles", {}):
        raise SystemExit(f"Unknown profile: {args.profile}")
    profile = copy.deepcopy(config["profiles"][args.profile])
    if args.grid_step is not None:
        profile["routing"]["grid_step"] = args.grid_step
    if args.max_ripup is not None:
        profile["routing"]["max_ripup"] = args.max_ripup
    source = resolve(root, args.input)
    output = resolve(root, args.output)
    if not source.is_file():
        raise SystemExit(f"Input board not found: {source}")
    if output == source:
        raise SystemExit("Output must be a separate candidate; in-place routing is forbidden")
    if output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite output: {output}")
    include = args.nets or profile.get("include_nets", [])
    if not include:
        raise SystemExit("No include_nets configured; pass --nets explicitly")
    if "*" in include and profile.get("require_explicit_include", True) and not args.allow_all_nets:
        raise SystemExit("Wildcard '*' requires --allow-all-nets for this profile")
    wildcard_patterns = [value for value in include if any(char in value for char in "*?[")]
    if (
        wildcard_patterns
        and profile.get("require_explicit_include", True)
        and not args.allow_wildcard_nets
        and not (include == ["*"] and args.allow_all_nets)
    ):
        raise SystemExit(
            "Wildcard net patterns require --allow-wildcard-nets: "
            + ", ".join(wildcard_patterns)
        )
    nets = expand_nets(source, include, profile.get("exclude_nets", []))
    if not nets:
        raise SystemExit("Net policy selected zero board nets")

    artifacts = config.get("artifacts", {})
    max_items = int(artifacts.get("receipt_max_items", 8))
    history_path = resolve(
        root, artifacts.get("history", "build/reports/krt/history.jsonl")
    )
    source_before = sha256(source)
    signature_payload = compact({
        "profile": args.profile,
        "tool_commit": config["tool"].get("commit"),
        "input_sha256": source_before,
        "nets": nets,
        "routing": profile["routing"],
    })
    attempt_signature = hashlib.sha256(signature_payload.encode("utf-8")).hexdigest()[:16]
    prior_attempts = sum(
        1 for item in read_history(history_path)
        if item.get("attempt_signature") == attempt_signature
    )
    attempt_limit = int(config.get("attempt_policy", {}).get("max_identical_attempts", 2))
    if (
        prior_attempts >= attempt_limit
        and not args.override_attempt_limit
        and not args.dry_run
    ):
        raise SystemExit(compact({
            "success": False,
            "error": "identical_attempt_limit",
            "attempt_signature": attempt_signature,
            "attempts": prior_attempts,
            "limit": attempt_limit,
            "next": "Change the profile/net set/routing parameters or use --override-attempt-limit after review",
        }))
    attempt_number = prior_attempts + 1
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or f"{args.profile}-{timestamp}"
    log_path = resolve(root, artifacts.get("log_dir", "build/logs/krt")) / f"{run_id}.log"
    report_path = resolve(root, artifacts.get("report_dir", "build/reports/krt")) / f"{run_id}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    collisions = [path for path in (log_path, report_path) if path.exists()]
    if collisions and not args.force:
        raise SystemExit("Refusing to overwrite run artifacts: " + ", ".join(map(str, collisions)))

    command = build_command(config, root, profile, source, output, nets)
    source_project = source.with_suffix(".kicad_pro")
    output_project = output.with_suffix(".kicad_pro")
    source_project_hash = sha256(source_project) if source_project.is_file() else None
    started = dt.datetime.now(dt.timezone.utc)
    if args.dry_run:
        dry_report = {
            "schema": 1,
            "dry_run": True,
            "run_id": run_id,
            "profile": args.profile,
            "input": str(source),
            "input_sha256": source_before,
            "attempt_signature": attempt_signature,
            "attempt_number": attempt_number,
            "output": str(output),
            "resolved_nets": nets,
            "command": command,
        }
        report_path.write_text(json.dumps(dry_report, indent=2) + "\n", encoding="utf-8")
        print(compact({
            "success": True, "dry_run": True, "run_id": run_id,
            "attempt_number": attempt_number,
            "nets": bounded(nets, max_items),
            "output": str(output), "report": str(report_path),
        }))
        return 0
    if output.exists() and args.force:
        output.unlink()
    if args.force:
        for path in collisions:
            path.unlink()
        if output_project.is_file() and output_project != source_project:
            output_project.unlink()
    if source_project.is_file() and output_project != source_project:
        output_project.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_project, output_project)
    result = subprocess.run(
        command, cwd=Path(tool_state(config, root)["repository"]),
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    combined = result.stdout + ("\n===== STDERR =====\n" + result.stderr if result.stderr else "")
    log_path.write_text(combined, encoding="utf-8")
    source_after = sha256(source)
    output_project_hash = sha256(output_project) if output_project.is_file() else None
    project_rules_preserved = bool(
        source_project_hash is None or source_project_hash == output_project_hash
    )
    summary = parse_router_summaries(combined)
    concise = concise_router(summary, max_items)
    diagnostics = {
        "log_lines": len(combined.splitlines()),
        "log_bytes": len(combined.encode("utf-8")),
        "fab_escalations": combined.count("escalated standard"),
        "route_failed_markers": combined.count("ROUTE FAILED"),
    }
    metrics = board_metrics(output, config.get("guardrails", {}).get("forbidden_signal_layers", [])) if output.is_file() else None
    audit = run_audit(config, root, source, output, log_path) if output.is_file() else None
    complete = bool(
        summary
        and not summary.get("failed_single")
        and not summary.get("failed_multipoint")
    )
    guardrails_ok = bool(metrics is not None and not metrics["forbidden_segments"])
    acceptance = config.get("acceptance", {})
    accepted = bool(
        result.returncode == 0
        and output.is_file()
        and source_before == source_after
        and project_rules_preserved
        and guardrails_ok
        and (complete or not acceptance.get("require_router_complete", True))
        and (
            not acceptance.get("require_audit_pass", True)
            or bool(audit and audit.get("baseline_pass"))
        )
        and (
            not acceptance.get("require_project_rules", True)
            or source_project_hash is not None
        )
    )
    report = {
        "schema": 1,
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "accepted": accepted,
        "router_exit_code": result.returncode,
        "profile": args.profile,
        "attempt_signature": attempt_signature,
        "attempt_number": attempt_number,
        "input": str(source),
        "input_sha256_before": source_before,
        "input_sha256_after": source_after,
        "input_project": str(source_project) if source_project.is_file() else None,
        "input_project_sha256": source_project_hash,
        "output": str(output),
        "output_sha256": sha256(output) if output.is_file() else None,
        "output_project": str(output_project) if output_project.is_file() else None,
        "output_project_sha256": output_project_hash,
        "resolved_nets": nets,
        "command": command,
        "router": summary,
        "diagnostics": diagnostics,
        "board_metrics": metrics,
        "audit": audit,
        "log": str(log_path),
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "success": accepted,
        "route_ok": bool(
            result.returncode == 0
            and output.is_file()
            and guardrails_ok
            and source_before == source_after
            and project_rules_preserved
        ),
        "attempt_number": attempt_number,
        "run_id": run_id,
        "output": str(output),
        "log": str(log_path),
        "report": str(report_path),
        "router": concise,
        "diagnostics": diagnostics,
        "guardrails": {
            "input_preserved": source_before == source_after,
            "project_rules_preserved": project_rules_preserved,
            "project_rules_available": source_project_hash is not None,
            "forbidden_segments": (metrics or {}).get("forbidden_segments"),
        },
        "audit": {
            "baseline_pass": (audit or {}).get("baseline_pass"),
            "unconnected": (audit or {}).get("unconnected"),
            "in1_non_ground_segments": (audit or {}).get("in1_non_ground_segments"),
        } if audit else None,
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_record = {
        "run_id": run_id,
        "profile": args.profile,
        "input_sha256": source_before,
        "attempt_signature": attempt_signature,
        "attempt_number": attempt_number,
        "success": accepted,
        "route_ok": receipt["route_ok"],
        "failed_single_count": concise.get("failed_single", {}).get("count"),
        "failed_multipoint_count": concise.get("failed_multipoint_count"),
        "forbidden_segment_count": sum(
            (metrics or {}).get("forbidden_segments", {}).values()
        ),
        "audit_pass": (audit or {}).get("baseline_pass"),
        "report": str(report_path),
    }
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(compact(history_record) + "\n")
    print(compact(receipt))
    return 0 if accepted else 2


def command_report(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    artifacts = config.get("artifacts", {})
    max_items = min(max(0, args.max_items), 20)
    if args.path:
        path = resolve(root, args.path)
    else:
        path = resolve(root, artifacts.get("report_dir", "build/reports/krt")) / f"{args.run_id}.json"
    if not path.is_file():
        raise SystemExit(f"Run report not found: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    router = report.get("router")
    print(compact({
        "success": True,
        "run_id": report.get("run_id"),
        "accepted": report.get("accepted"),
        "profile": report.get("profile"),
        "output": report.get("output"),
        "resolved_nets": bounded(report.get("resolved_nets", []), max_items),
        "router": concise_router(router, max_items),
        "diagnostics": report.get("diagnostics"),
        "board_metrics": concise_metrics(report.get("board_metrics"), max_items),
        "audit": concise_audit(report.get("audit"), max_items),
        "log": report.get("log"),
        "report": str(path),
    }))
    return 0


def command_history(args: argparse.Namespace) -> int:
    config, root = load_config(args.config)
    artifacts = config.get("artifacts", {})
    path = resolve(root, artifacts.get("history", "build/reports/krt/history.jsonl"))
    if not path.is_file():
        print(compact({"success": True, "history": [], "path": str(path)}))
        return 0
    records = read_history(path)
    limit = min(max(1, args.limit), 20)
    print(compact({
        "success": True,
        "total": len(records),
        "history": records[-limit:],
        "path": str(path),
    }))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__)
    top.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = top.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Verify the pinned tool and optional input")
    check.add_argument("--input")
    check.set_defaults(handler=command_check)
    route = commands.add_parser("route", help="Run one policy-controlled route attempt")
    route.add_argument("--profile", required=True)
    route.add_argument("--input", required=True)
    route.add_argument("--output", required=True)
    route.add_argument("--nets", nargs="+")
    route.add_argument("--run-id")
    route.add_argument("--grid-step", type=float)
    route.add_argument("--max-ripup", type=int)
    route.add_argument("--allow-all-nets", action="store_true")
    route.add_argument("--allow-wildcard-nets", action="store_true")
    route.add_argument("--force", action="store_true")
    route.add_argument("--override-attempt-limit", action="store_true")
    route.add_argument("--dry-run", action="store_true")
    route.set_defaults(handler=command_route)
    report = commands.add_parser("report", help="Read a bounded run report without opening its log")
    report_source = report.add_mutually_exclusive_group(required=True)
    report_source.add_argument("--run-id")
    report_source.add_argument("--path")
    report.add_argument("--max-items", type=int, default=8)
    report.set_defaults(handler=command_report)
    history = commands.add_parser("history", help="Show compact recent run outcomes")
    history.add_argument("--limit", type=int, default=8)
    history.set_defaults(handler=command_history)
    return top


def main() -> int:
    args = parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
