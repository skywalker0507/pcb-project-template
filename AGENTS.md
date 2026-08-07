# Codex instructions for AI PCB projects

This repository uses a replaceable electrical backend with **tscircuit as the
default authority** and KiCad for physical PCB design. atopile is a legacy
compatibility adapter only. The installed backend, its lockfile, and reviewed
manufacturer data take precedence over examples or model guesses.

## Required workflow

1. Run `python scripts/session_boot.py`, then read only the cited stage rules, acceptance, and board-stack records before design work.
2. Validate `config/requirements.json`, then translate its interfaces, rails, constraints and blocks.
3. Inspect the selected backend in `config/electrical-backend.json`; for
   tscircuit, use local locked dependencies and Circuit JSON/KiCad candidates.
4. Validate changed electrical sources and run `build_electrical.py` after
   meaningful changes.
5. Preserve picked parts, net names, designators and manual KiCad layout.
6. Run `python scripts/run_pcb_pipeline.py` after source or layout changes.
7. Run DRC and inspect rendered copper before manufacturing export.
8. After an intentional export, run `check_jlcpcb_release.py`; create a release snapshot only from a matching `release_pass=true` audit.

Use `python scripts/check_pcb_environment.py --tier minimal` to begin requirements work; use `--tier standard` before normal design work and `--tier deep` only when the selected electrical backend build and router capability check are intended.

## Delegation boundary

Use project-scoped Houtini-LM only for bounded text-heavy first passes: log
classification, document extraction, inventories and drafts. Prefer paths,
400-800 output tokens, `path:line` evidence and explicit unknowns. Never send
secrets, binary files, PDFs or a complete `.kicad_pcb`.

Keep electrical architecture, component selection, datasheet interpretation,
placement, routing, return paths, impedance, DRC waivers and manufacturing
approval in Codex. Treat delegated output as untrusted until verified.

Use a Houtini task only when `build/delegated/*-houtini-job.json` says
`eligible: true`; call it once using that file's `delegate` object without
loading the listed artifacts into Codex first. If no job exists, do not spend
tokens deciding whether to delegate: use the compact script receipt instead.

## Physical design

Use deterministic scripts before repeated MCP choreography. HPWL and aggregate
copper length are triage metrics, not acceptance. Never invent numeric widths,
spacing or impedance, and never regenerate a DRC baseline merely to pass.

Treat `tools/KiCadRoutingTools` as an updateable project-maintained fork
checkout. Use
`scripts/krt_flow.py` and `config/kicad-routing-tools.json`; start with `check`,
then a profile-scoped dry run, and route only to a separate candidate. Never
bypass explicit rules, forbidden-layer scans, input hashes or
`--no-fix-drc-settings`. On older KRT the adapter requires
`--no-clamp-netclasses`; on newer KRT it omits the clearance ceiling only after
verifying the configured value equals the KiCad Default netclass. `route_ok` is never release evidence and still requires independent DRC, physical review, and `release_pass=true`.

Konnect replaces KiCAD-MCP-Server and is complementary to KRT. Use it only for
live KiCad placement, explicit trace/via edits, queries and visual iteration.
Do not use its schematic-writing or reference-circuit tools: the selected
electrical backend remains the electrical authority. Do not use Konnect's natural-language design audits
as evidence for numeric rules or release approval. Its current `autoroute`
tool is unavailable on KiCad 10; use the guarded KRT/FreeRouting candidate
flows and run the deterministic audit afterward.

At a meaningful stage boundary, run `scripts/create_stage_handoff.py` and keep
no more than eight open items and next actions.

Use `python scripts/stage_doctor.py` before selecting a workflow command. Do
not bypass stage guards on routing candidates or manufacturing export.
