# AI PCB project template

Token-efficient, reproducible atopile-first PCB project template for Codex, with
EasyEDA Pro as the primary physical backend and KiCad retained as interchange,
independent validator, and fallback.
It combines:

- reviewed atopile source as the production electrical authority and immutable
  release-lane freeze bundles as the only electrical-to-physical handoff;
- an optional experimental tscircuit/Bun backend for A/B research;
- an EasyEDA Pro physical-backend contract with deterministic import parity,
  DRC, and JLC DFM receipt gates (the official bridge/API adapter is pending);
- the latest `pcb-ai-adapter` branch of the Houtini-LM fork;
- the project-maintained KiCadRoutingTools `codex/repository-support` checkout
  behind a guarded adapter;
- the latest Konnect MCP as the KiCAD-MCP-Server replacement for live,
  undo-aware KiCad 10 operations;
- a SHA-verified FreeRouting JAR for bounded headless routing;
- deterministic DRC, PCB-context, FreeRouting and JLCPCB export scripts;
- compact stage handoffs that avoid repeatedly loading the whole project.

## Create a project

The workflow has eight stable stages: requirements, parts, electrical,
electrical freeze, placement, routing, release, and prototype validation. Run
`python scripts/session_boot.py` once per session for the current stage, gates,
on-demand files, and next command.

Create a new repository from this GitHub template, clone it, then run:

```text
python scripts/init_project.py --project-name my-board --layers 2
```

Initialization resets the template handoff to `01-requirements` and points the
configured board at atopile's `elec/layout/default/default.kicad_pcb` output.
Use `--preserve-existing` only when intentionally reconfiguring an already-active
project.

Konnect is configured by default. To omit it, for example while reviewing its
AGPL/commercial license terms:

```text
python scripts/init_project.py --project-name my-board --no-konnect
```

The initializer only writes project-local MCP configuration. It never
downloads, clones, installs or compiles tools. Install tools manually when the
network is reliable, then record their local versions and hashes with
`python scripts/record_toolchain_receipt.py`. API keys are never written to the
repository.

Before a production atopile build, at minimum record the actual electrical
compiler identity without requiring the routing toolchain to be installed:

```text
python scripts/record_toolchain_receipt.py --electrical-only
```

If a full toolchain receipt already exists, `--electrical-only` refreshes only
the active electrical backend/compiler fields and preserves the existing KRT,
FreeRouting, Houtini and Konnect records as explicitly unchecked sections.

System prerequisites for the production path are Git, Python 3.10+, Java 25,
KiCad 10, WSL Ubuntu and atopile. Node.js and Bun are required only when running
the experimental tscircuit backend. The environment check reports missing
prerequisites and manual remediation steps without changing the machine.

Konnect replaces the old KiCAD-MCP-Server integration. It is complementary to
KRT: Konnect's routing tools add explicit trace/via geometry through the live
KiCad IPC API, while KRT remains the guarded batch-routing candidate generator.
See `docs/KONNECT_EVALUATION.md` for the boundary and license note.

Restart Codex after initialization. `--tier minimal` is sufficient to begin requirements work; verify the full local toolchain before design work:

```text
python scripts/check_pcb_environment.py --deep
```

`--deep` includes a selected-backend smoke check and KRT capability check. A
passing router check is not PCB release approval.

The template `.ato` entry may be a deliberately non-manufacturing scaffold.
Replace it with reviewed product sources before creating an electrical freeze,
opening physical layout work, or creating manufacturing artifacts.

The authority chain is:

```text
requirements -> atopile -> electrical checks -> immutable ELECTRICAL_FREEZE bundle
             -> .kicad_pcb interchange -> EasyEDA Pro -> KRT
             -> final electrical parity + EasyEDA DRC
             -> independent KiCad validation -> JLC DFM -> release
```

`config/backend-architecture.json` keeps electrical backend, physical backend,
routing engine, validation backend, and manufacturing backend as separate
roles. KRT is not coupled to the KiCad GUI. Router success never proves release.
The pending official adapter contract is documented in
`docs/EASYEDA_BRIDGE_CONTRACT.md`.

Before each new session, run `python scripts/session_boot.py`; its compact
receipt replaces separately opening `CURRENT.json`, every stage rule,
acceptance, board-stack, workflow documentation, or long logs. Read its
`read_if_needed` paths only when the task or a failure requires them.

The common Agent-facing command surface has six commands and only dispatches to
the existing guarded scripts:

```text
python scripts/pcbctl.py status
python scripts/pcbctl.py build [build_electrical arguments]
python scripts/pcbctl.py freeze [create_electrical_freeze arguments]
python scripts/pcbctl.py route [krt_flow arguments]
python scripts/pcbctl.py verify [run_pcb_pipeline arguments]
python scripts/pcbctl.py release [run_pcb_pipeline arguments]
```

Use specialized scripts directly when detailed arguments are needed. Before
adding automation, inspect `config/tool-entrypoints.json` or run
`find_existing_script.py <task-keywords>`. Never start FreeRouting directly;
reuse its reviewed headless wrapper.

Use `select_manufacturing_profile.py --manufacturer <name> --source
<URL-or-card>` to record a reviewed process source before manufacturing export;
the command does not invent fabrication values.

Selecting a source does not authorize real routing. After reviewing it, record explicit values and evidence with `apply_reviewed_fab_values.py`; only then can a non-dry-run KRT attempt proceed.

Before physical placement, routing, or release, compile the shared executable policy:

```powershell
py -3.14 scripts/compile_design_policy.py
py -3.14 scripts/pcbctl.py status
```

The receipt at `build/00-shared/evidence/design-policy/design-policy-resolved.json` records semantic hashes of requirements,
layer stack, manufacturing evidence, KRT policy, and acceptance rules. Changing
electrical constraints invalidates it; changing only a log/output directory does
not. A release additionally needs a SHA-bound
physical review created by `record_physical_review.py`; `release_pass` alone is
not sufficient.

`apply_reviewed_fab_values.py` records canonical KRT names (`via_size`, not
`via_diameter`). Compilation emits KRT, DFM and KiCad-rule artifacts under
`build/`. Before a real KRT run on current KRT versions, explicitly synchronize
the reviewed Default netclass using `apply_kicad_rules.py --board <board>`;
the command is stage guarded and writes a receipt.

Before a JLCPCB assembly export, complete `config/jlcpcb-parts.json`. The file is
bound to the board SHA and maps every SMD position to a reviewed board value,
footprint, MPN and LCSC number; unlisted, duplicate, stale or mismatched refs
make the export fail. Stock notes are time-sensitive procurement evidence, not
permission to silently substitute a part.

After an intentional JLCPCB export, use the package-audit path printed by
`run_pcb_pipeline.py --export`. It is not release approval. Only after that
receipt and the matching audit have passed, create a non-overwriting evidence
snapshot with `create_release_snapshot.py --package <export-dir>
--package-check <package-audit> --review-asset <top.png> --review-asset
<bottom.png>`. The snapshot retains the exact assembly-parts source and cited
copper renders alongside hash-manifested release evidence.

`config/requirements.json` is the structured requirements record; validate it
with `python scripts/validate_requirements.py` before electrical capture.

Manufacturing exports are SHA-bound to passing EasyEDA import parity, final
electrical parity against the frozen manifest, EasyEDA DRC, JLC DFM,
independent KiCad release audit, and physical-review receipts.
Use `run_pcb_pipeline.py --export`; invoking the export script directly also
requires the matching consolidated manufacturing-gate receipt and cannot
bypass DRC/DFM approval.

After `python scripts/pcbctl.py build`, independently inspect the new candidate
using the receipt path printed by the build:

```text
python scripts/review_electrical_candidate.py --receipt build/03-electrical/evidence/<run-id>/electrical-build.json --output build/03-electrical/evidence/<review-run-id>
```

This review intentionally fails on any KiCad DRC violation or unconnected item,
renders the candidate, and never makes it manufacturing-ready.

Create the compact schema-2 electrical manifest, validate it, and promote the
reviewed candidate into an immutable freeze bundle:

```text
python scripts/extract_kicad_parity_manifest.py --board <candidate.kicad_pcb> --output <candidate-manifest.json>
python scripts/validate_electrical_contract.py --receipt <electrical-build.json> --manifest <candidate-manifest.json> --output <contract.json>
python scripts/create_electrical_freeze.py --build-receipt <electrical-build.json> --contract-receipt <contract.json> --candidate-review <candidate-review.json> --freeze-id <freeze-id>
```

Then set `manufacturing.gate_receipts.electrical_freeze` in
`config/pcb-flow.json` to that bundle's immutable receipt. Never point it back
to an electrical tmp candidate.

## Audited routing candidates

Routing work must stay isolated until it has both a before/after DRC comparison
and an explicit review decision. After a candidate passes
`krt_candidate_audit.py`, bind it to a SHA-checked receipt instead of changing
the active board path:

```text
python scripts/record_routing_candidate.py --candidate <candidate.kicad_pcb> --audit <candidate-audit.json> --output <candidate-receipt.json> --scope "short reviewed scope"
```

Use that same board and receipt when creating the routing-stage handoff:

```text
python scripts/create_stage_handoff.py --stage routing --status in_progress --objective "..." --board <candidate.kicad_pcb> --candidate-receipt <candidate-receipt.json> --skip-pipeline
```

A `complete` handoff must be backed by a passing pipeline or one or more
`--completion-receipt` files; `--skip-pipeline` alone cannot claim completion.

The receipt verifies the candidate path and SHA against a zero-new-DRC audit.
It deliberately cannot promote copper, satisfy physical review, authorize an
export, or act as release evidence. This keeps candidate provenance available
to an AI without silently changing the configured release board.

When two or more rejected candidates for the same routing area encounter
structural placement constraints (board edge, courtyard, NPTH, or hole
clearance), record the escalation and return to physical placement rather than
trying more coordinate variations:

```text
python scripts/assess_routing_escalation.py --audit <rejected-audit-1.json> --audit <rejected-audit-2.json> --scope "J2 escape" --output <escalation.json>
```

## Project layout

- `src/` and `ato.yaml`: production atopile electrical design authority;
- `tscircuit/`: optional experimental electrical backend and locked native toolchain;
- `elec/layout/`: KiCad interchange/reference boards, not a second electrical authority;
- `config/physical-backend.json`: EasyEDA-primary and KiCad-fallback physical contract;
- `config/backend-architecture.json`: explicit electrical/physical/routing/validation/manufacturing role separation;
- `config/`: reviewed project and tool policy;
- `scripts/`: deterministic project-local workflow;
- `experiments/`: disposable candidate boards and A/B trials;
- `handoff/stages/`: compact cross-session stage state;
- `manufacturing/`: intentional release exports;
- `build/00-shared/`: compact toolchain and compiled-policy receipts shared by stages;
- `build/01-requirements/` through `build/08-prototype/`: stage artifacts;
- each stage has `tmp/<run-id>`, `evidence/<run-id>`, and `release/<release-id>` lanes;
- `build/00-legacy/*.zip`: verified, recoverable archives from the former flat layout.

`tmp` may be deleted after its retention period. `evidence` contains compact,
independently checkable receipts. `release` is populated only after its gate
passes; the full immutable manufacturing snapshot remains under
`manufacturing/releases/`. Use `python scripts/artifact_store.py inventory` to
inspect the store and `archive-legacy` without `--apply` to preview a migration.
Use `python scripts/artifact_store.py prune` to preview disposable runs older
than the configured retention window; add `--apply` only after reviewing the
list. Pruning creates and verifies a recoverable zip before removing old `tmp`
runs and never touches `evidence` or `release`.

Stages do not duplicate `src/` or `elec/layout/`.

## Latest-version policy

Repositories and latest channels are declared in `config/toolchain-policy.json`.
After manually installing or upgrading a tool, record the actual versions,
commits and hashes in ignored `build/00-shared/evidence/toolchain/toolchain-resolved.json` with
`record_toolchain_receipt.py`, then rerun the deep environment check before PCB
work.

See `docs/AI_PCB_WORKFLOW.md` for the normal design and release sequence.
