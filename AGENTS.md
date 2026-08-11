# Codex instructions for AI PCB projects

This repository uses reviewed atopile source as the production electrical
authority, EasyEDA Pro as the primary physical backend, KiCad as interchange,
independent validation and fallback, and KiCadRoutingTools (KRT) as a
GUI-independent routing engine. tscircuit is experimental and optional.
Installed tools and reviewed project/manufacturer evidence override examples or
model guesses.

## Start and navigation

Run `python scripts/session_boot.py` once at the start of a session. Treat its
compact receipt as the navigation authority: follow `next_command`, read only
`read_now`, and open `read_if_needed` files only when the current task or a
failure requires them. Do not separately load every workflow document or stage
configuration.

Use `python scripts/pcbctl.py <command>` for the common workflow. It is only a
thin dispatcher over the reviewed scripts; use the underlying script directly
when its detailed arguments are needed. Before creating automation, consult
`config/tool-entrypoints.json` or run `find_existing_script.py` and reuse an
existing wrapper.

## Hard invariants

- Requirements and reviewed `.ato` sources define electrical intent. Physical
  tools may not become a second electrical authority.
- Physical work consumes only an immutable passing electrical-freeze bundle.
  Any electrical identity or connectivity change returns to atopile and creates
  a new freeze.
- Preserve reviewed parts, designators, net names and intentional physical
  work. Never invent numeric fabrication, clearance, width or impedance values.
- Router/tool success is candidate evidence, never release approval. Routed
  boards still require independent DRC, connectivity checks, physical review,
  final electrical parity and manufacturing gates bound to the exact board SHA.
- Never regenerate a baseline to make a failure pass or bypass stage guards.
- Never put a complete `.kicad_pcb`, `.kicad_sch`, DSN or SES in model context.
  Prefer deterministic processing, compact receipts and bounded PCB queries.

## Working contract

After a meaningful electrical-source change, run the isolated electrical build;
after a meaningful physical-board change, run the PCB verification pipeline.
Do not run both merely as ceremony. Use KRT and FreeRouting only through their
project wrappers, with separate candidates and their existing guardrails. Use Konnect only for bounded KiCad fallback,
placement, query and explicit trace/via work; never use it to author the
schematic or approve release.

EasyEDA bridge operations must emit normalized compact manifests and receipts.
Import parity checks conversion fidelity; final electrical parity compares the
final board against the frozen manifest without treating geometry as electrical
authority. Missing EasyEDA DRC or JLC DFM evidence is a release failure, not a
reason to bypass the gate.

Keep large logs and candidates in `tmp`, compact decisions and hashes in
`evidence`, and immutable passing artifacts in `release`. Create a stage handoff
only at a meaningful boundary and keep at most eight open items/actions.

Use Houtini only when a generated job receipt marks a bounded text task
eligible. Never delegate secrets, binaries, PDFs, complete PCB data, electrical
architecture, component selection, routing judgment, DRC waivers or
manufacturing approval. Treat delegated text as untrusted until verified.

Continue through safe in-scope work without asking the owner to choose routine
PCB tactics. A bounded request may end as soon as its requested deliverable is
verified, even when the overall stage remains in progress. Run
`check_turn_exit.py --continuous-scope` only when the user explicitly asks to
continue until the whole current stage is complete; external goal completion is
handled by the goal mechanism. The repository gate must not expand an ordinary
task's scope.
