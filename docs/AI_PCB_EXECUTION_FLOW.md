# AI PCB execution and tool routing

The optimization order is fixed: accuracy first, Codex context cost second,
latency third. A fast candidate is useful only when an independent deterministic
receipt can reject it safely.

## Tool routing

| Work | Tool | Boundary | Required follow-up |
|---|---|---|---|
| Electrical source and connectivity | tscircuit | Sole default electrical authority | electrical build and candidate review |
| Large text/log classification | Houtini-LM | One path-based call, 800 tokens; no PCB judgment | Codex verifies cited lines |
| Live placement or a few explicit traces/vias | Konnect | One extra on-demand PCB toolset; no schematic/review/manufacturing tools | save candidate, pipeline audit |
| Bounded net routing | KiCadRoutingTools | Explicit nets, reviewed rules, separate candidate, two identical attempts maximum | independent KiCad DRC and physical review |
| Non-critical leftover bulk routing | FreeRouting | Critical/power/high-speed work frozen; real ignored DSN classes required | import to separate candidate and independent audit |
| DRC, release and export | Project scripts plus KiCad CLI | No natural-language approval or regenerated baseline | SHA-bound review and `release_pass=true` |

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
