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

Before creating a new helper or invoking a tool binary, inspect
`config/tool-entrypoints.json` and run `python scripts/check_tool_entrypoints.py`.
Search the existing script inventory first with
`python scripts/find_existing_script.py <capability-or-net-name>`; inspect the
best match before adding another candidate generator, geometry query, review,
or tool wrapper.
Use the listed project wrapper when one exists. Do not invoke FreeRouting with
`java -jar` directly for routing or diagnostics; use
`scripts/run_freerouting_headless.py`, which must retain
`--gui.enabled=false`. Direct version/hash checks are limited to the existing
environment and toolchain-receipt scripts.
For a FreeRouting-specific diagnostic, use
`python scripts/run_freerouting_headless.py --check`; it verifies the locked JAR
and wrapper policy without launching Java.

Use `python scripts/check_pcb_environment.py --tier minimal` to begin requirements work; use `--tier standard` before normal design work and `--tier deep` only when the selected electrical backend build and router capability check are intended.

## Artifact and context contract

Every meaningful command writes one run under `build/<stage>/tmp/<run-id>` and
`build/<stage>/evidence/<run-id>`. Full logs, generated boards, DSN/SES files and
failed candidates stay in `tmp`; compact receipts, hashes, DRC results and
decisions stay in `evidence`. Populate `release/<release-id>` only after the
relevant acceptance gate passes. Shared toolchain and compiled-policy receipts
live under `build/00-shared/evidence/`; recoverable legacy archives live under
`build/00-legacy/`.

Use `build/CURRENT.json`, `session_boot.py`, bounded `query_pcb_context.py`
slices, and run receipts before opening full logs. Do not place a complete
`.kicad_pcb`, `.kicad_sch`, `.dsn`, or `.ses` in model context. The default
context slice is capped at 6000 bytes.

## Continuous execution contract

Treat user directives such as “继续”, “自行决定”, “直到完成”, and “不要停” as
a request to keep advancing the current stage without waiting for another turn.
The project owner is not expected to choose PCB implementation tactics. Codex
owns in-scope electrical, placement, and routing choices that preserve recorded
requirements and reviewed manufacturer constraints.

A tool result, candidate result, failed candidate, status update, or ordinary
handoff is not a turn boundary. Report useful intermediate results in
commentary, then continue through the next safe action. A rejected candidate is
evidence for the next materially different candidate, not a blocker. Do not use
elapsed time or a fixed number of tool calls as a completion condition.

During continuous work, run `python scripts/check_turn_exit.py` before sending a
final response. Continue whenever it reports `continue_required=true`. A final
response is allowed only when one of these conditions is true:

- the requested stage or deliverable is verified complete and has no remaining
  actions;
- the same blocking condition has been observed at least three times, safe
  alternatives are exhausted, and the handoff contains machine-readable blocker
  evidence;
- a material product, safety, cost, external-write, or scope decision is needed
  and cannot be inferred from the recorded requirements;
- the user explicitly asks to stop or only asks for a status/report.

Do not ask the project owner to select among routine PCB tactics. If a user
decision is genuinely required, present one recommended choice with its concrete
impact. Context compaction or a new session resumes from `session_boot` and the
current handoff; it does not convert unfinished work into completion.

## Delegation boundary

Use project-scoped Houtini-LM only for bounded text-heavy first passes: log
classification, document extraction, inventories and drafts. Prefer paths,
400-800 output tokens, `path:line` evidence and explicit unknowns. Never send
secrets, binary files, PDFs or a complete `.kicad_pcb`.

Keep electrical architecture, component selection, datasheet interpretation,
placement, routing, return paths, impedance, DRC waivers and manufacturing
approval in Codex. Treat delegated output as untrusted until verified.

Use a Houtini task only when the current run's `evidence/delegation/*-houtini-job.json` says
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

Load at most one additional Konnect toolset for the current physical task, then
continue with deterministic scripts. Do not load its schematic, template,
natural-language design-review, manufacturing, or autoroute toolsets. Use
FreeRouting only after critical/high-speed/power copper is frozen, pass real
DSN net classes with `--ignore-net-class`, and treat the resulting SES as an
unaccepted disposable candidate.

At a meaningful stage boundary, run `scripts/create_stage_handoff.py` and keep
no more than eight open items and next actions. Do not create a handoff after
every candidate, and do not treat an `in_progress` handoff as permission to end
continuous work.

Use `python scripts/stage_doctor.py` before selecting a workflow command. Do
not bypass stage guards on routing candidates or manufacturing export.
