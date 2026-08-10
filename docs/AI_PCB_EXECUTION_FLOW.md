# AI PCB execution and tool routing

The optimization order is fixed: accuracy first, Codex context cost second,
latency third. A fast candidate is useful only when an independent deterministic
receipt can reject it safely.

## Tool routing

| Work | Tool | Boundary | Required follow-up |
|---|---|---|---|
| Electrical source and connectivity | atopile | Sole production electrical authority | electrical build, contract validation, candidate review, and electrical freeze |
| Primary placement/physical editing | EasyEDA Pro bridge/API | Must consume an electrical freeze and pass deterministic import parity | EasyEDA DRC plus exported compact receipt |
| Reference/fallback physical editing | KiCad GUI/Konnect | Interchange, debug, and fallback only; no schematic authority | save candidate and independent pipeline audit |
| Large text/log classification | Houtini-LM | One path-based call, 800 tokens; no PCB judgment | Codex verifies cited lines |
| Bounded net routing | KiCadRoutingTools | GUI-independent engine; explicit nets, reviewed rules, separate candidate, two identical attempts maximum | EasyEDA import/diff plus independent KiCad DRC and physical review |
| Non-critical leftover bulk routing | FreeRouting | Critical/power/high-speed work frozen; real ignored DSN classes required | import to separate candidate and independent audit |
| DRC, release and export | EasyEDA DRC, KiCad CLI, JLC DFM, and project scripts | No natural-language approval or regenerated baseline | SHA-bound gate receipts, package checks, and `release_pass=true` |

## Artifact lanes

Each command receives or creates a run ID:

```text
build/
  00-shared/{tmp,evidence}/
  01-requirements/{tmp,evidence,release}/<run-id>/
  ...
  08-prototype/{tmp,evidence,release}/<run-id>/
  00-legacy/<timestamp>.zip
  CURRENT.json
```

`tmp` holds large or disposable material. `evidence` holds bounded receipts and
hashes. `release` holds only passing, immutable indexes; full manufacturing
snapshots remain under `manufacturing/releases/`.

## Token rules

1. Start with `session_boot.py`, `stage_doctor.py`, and `build/CURRENT.json`.
2. Read compact receipts before logs; read only the cited tail or lines on a failure.
3. Query PCB context by net/group/ref; the default response cap is 6000 bytes.
4. Never load a complete PCB, schematic, DSN, or SES into an LLM.
5. Stop identical router retries after two attempts and change the physical or routing hypothesis.
6. Create one handoff at a meaningful stage boundary, with at most eight open items.

## Existing-script rule

`config/tool-entrypoints.json` is the machine-readable inventory of reviewed
wrappers. `scripts/check_tool_entrypoints.py` runs in the PCB pipeline and
rejects a missing wrapper, a FreeRouting wrapper without
`--gui.enabled=false`, or another Python script that directly launches the
FreeRouting JAR. Diagnostic experiments must still go through the wrapper;
only the existing environment and receipt scripts may query binary versions or
hash files directly.
