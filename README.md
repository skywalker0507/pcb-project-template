# AI PCB project template

Token-efficient, reproducible tscircuit-first and KiCad project template for Codex.
It combines:

- a native Windows tscircuit/Bun electrical backend that emits Circuit JSON and
  isolated KiCad candidates;
- an optional, removable atopile compatibility adapter;
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
`python scripts/stage_doctor.py` to see the current stage's authoritative files
and allowed commands.

Create a new repository from this GitHub template, clone it, then run:

```text
python scripts/init_project.py --project-name my-board --layers 2
```

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

System prerequisites are Git, Python 3.10+, Node.js 18+, Bun, Java 25 and
KiCad 10. WSL Ubuntu and atopile are only needed when deliberately selecting
the legacy atopile backend. The environment check reports missing prerequisites
and manual remediation steps without changing the machine.

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

The initial tscircuit entry and reviewed requirements record are a deliberately
non-manufacturing, one-resistor smoke test. Replace both with the actual product
requirements and design before selecting parts, opening a physical layout, or
creating manufacturing artifacts.

Before each new session, run `python scripts/session_boot.py`; it replaces opening the whole board or long logs by default. Use `python scripts/select_manufacturing_profile.py --manufacturer <name> --source <URL-or-card>` to record a reviewed process source before manufacturing export; this command does not invent fabrication values.

`session_boot.py` also returns the reviewed script entrypoints for the current
stage. Before adding automation, run `python scripts/check_tool_entrypoints.py`
and `python scripts/find_existing_script.py <task-keywords>`, then reuse the
best existing script or wrapper listed in `config/tool-entrypoints.json`. In particular,
never start the FreeRouting JAR directly: use
`scripts/run_freerouting_headless.py`, which fixes headless mode with
`--gui.enabled=false` and applies the project candidate guards.

Selecting a source does not authorize real routing. After reviewing it, record explicit values and evidence with `apply_reviewed_fab_values.py`; only then can a non-dry-run KRT attempt proceed.

Before physical placement, routing, or release, compile the shared executable policy:

```powershell
py -3.14 scripts/compile_design_policy.py
py -3.14 scripts/stage_doctor.py
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

Use `python scripts/stage_doctor.py` at session start. It returns the current stage, bounded blockers, and one next command. `config/requirements.json` is the structured requirements record; validate it with `python scripts/validate_requirements.py` before electrical capture.

Manufacturing exports are SHA-bound to a passing KiCad release-audit receipt. Use
`run_pcb_pipeline.py --export`; invoking the export script directly also
requires the matching audit JSON and cannot bypass DRC approval.

After `python scripts/run_pcb_pipeline.py --build`, independently inspect the
new candidate using the receipt path printed by the build:

```text
python scripts/review_electrical_candidate.py --receipt build/03-electrical/evidence/<run-id>/electrical-build.json --output build/03-electrical/evidence/<review-run-id>
```

This review intentionally fails on any KiCad DRC violation or unconnected item,
renders the candidate, and never makes it manufacturing-ready.

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

- `tscircuit/`: default electrical design authority and locked native toolchain;
- `src/` and `ato.yaml`: optional legacy atopile source only;
- `elec/layout/`: single KiCad layout authority;
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
