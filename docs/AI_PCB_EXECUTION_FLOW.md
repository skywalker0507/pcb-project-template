# AI PCB execution and tool routing

The optimization order is fixed: accuracy first, Codex context cost second,
latency third. A fast candidate is useful only when an independent deterministic
receipt can reject it safely.

## Tool routing

| Work | Tool | Boundary | Required follow-up |
|---|---|---|---|
| Electrical source and connectivity | atopile | Sole production electrical authority | electrical build, contract validation, candidate review, and immutable release-lane freeze bundle |
| Primary placement/physical editing | KiCad GUI/Konnect | Must use a writable candidate created from the immutable freeze; no schematic authority | save candidate and independent pipeline audit |
| Optional compatibility/manufacturing review | EasyEDA Pro bridge/API | One-way reviewed import only; never a second physical authority | import parity and EasyEDA receipt when explicitly selected |
| Large text/log classification | Houtini-LM | One path-based call, 800 tokens; no PCB judgment | Codex verifies cited lines |
| Bounded net routing | KiCadRoutingTools | GUI-independent engine; explicit nets, reviewed rules, separate candidate, two identical attempts maximum | independent KiCad candidate audit and physical review |
| Placement/routing visual review | KiCad visual bundle + structured reviewer receipt | Local: one cropped copper view for an explicit trigger; checkpoint: F/B copper; release: four exact-board views | SHA match, bounded findings, explicit verdict; never replaces DRC or parity |
| Non-critical leftover bulk routing | FreeRouting | Critical/power/high-speed work frozen; real ignored DSN classes required | import to separate candidate and independent audit |
| DRC, release and export | Final electrical parity, KiCad CLI, JLC DFM, and project scripts | No natural-language approval or regenerated baseline | SHA-bound gate receipts, package checks, and `release_pass=true` |

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
  STAGE_RESULTS.json
```

`tmp` holds large or disposable material. `evidence` holds bounded receipts and
hashes. `release` holds the adopted immutable stage artifact and its
`stage-result.json`. Each stage also exposes a local `CURRENT.json`, while
`STAGE_RESULTS.json` provides the cross-stage index. Full manufacturing
snapshots remain under `manufacturing/releases/`.

## Token rules

1. Start with only `session_boot.py`; it summarizes the relevant current-stage
   records and names any on-demand files.
2. Read compact receipts before logs; read only the cited tail or lines on a failure.
3. Query PCB context by net/group/ref; the default response cap is 6000 bytes.
   Use `pcbctl verify remaining` for grouped airwires and corridor queries for
   proposed local paths instead of opening broad PCB geometry slices.
4. Never load a complete PCB, schematic, DSN, or SES into an LLM.
5. Stop identical router retries after two attempts and change the physical or routing hypothesis.
6. Render only on the explicit triggers in `config/validation-tiers.json`. A local edit uses one cropped copper view and `accept/awkward/unsafe`; functional checkpoints use F/B copper; four views are release-only.
7. Run `pcbctl verify pre-route` before broad routing so non-copper release debt
   is classified early. Use `pcbctl route checkpoint-finalize` at zero
   unconnected items to bind evidence and refresh handoff/WORKSET once.
8. Create one handoff at a meaningful stage boundary, with at most eight open items.
9. Before release, run `pcbctl verify readiness` once. Resolve the full blocker
   list it returns; do not discover release debt by repeatedly starting the
   expensive export pipeline.

## Existing-script rule

`config/tool-entrypoints.json` is the machine-readable inventory of reviewed
wrappers. `scripts/check_tool_entrypoints.py` runs in the PCB pipeline and
rejects a missing wrapper, a FreeRouting wrapper without
`--gui.enabled=false`, or another Python script that directly launches the
FreeRouting JAR. Diagnostic experiments must still go through the wrapper;
only the existing environment and receipt scripts may query binary versions or
hash files directly.
