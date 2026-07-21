# KiCadRoutingTools AI flow

This scaffold keeps the upstream KiCadRoutingTools checkout unmodified. The
adapter resolves allowed nets from project policy, captures complete logs on
disk, emits one compact JSON receipt, verifies the input hash, and rejects
tracks on forbidden layers. Its generic KiCad DRC gate compares the candidate
against the input and rejects new violation objects or a higher unconnected
count without needing a project-specific baseline.

1. Install or clone KiCadRoutingTools at the configured pinned commit.
2. Create its Python environment and compiled Rust router as upstream directs.
3. Set explicit `track_width`, `clearance`, `via_size`, and `via_drill` values
   from the reviewed KiCad project/fabricator rules.
4. Put only low-risk patterns in `include_nets`; critical nets belong in
   `exclude_nets` and should be routed/reviewed deliberately in KiCad.
5. Verify with `python scripts/krt_flow.py check --input <board>`.
6. Preview selection with `python scripts/krt_flow.py route ... --dry-run`.
7. Run without `--dry-run`; inspect the compact receipt first and open its log
   or report only when a specific failure needs investigation.

Use `python scripts/krt_flow.py history` for recent outcomes and
`python scripts/krt_flow.py report --run-id <id>` for a bounded report. These
commands never print the full router log or complete net list.

Never accept a candidate solely because the router process exited zero. The
receipt's `success` includes completion, input preservation, forbidden-layer
checks, and (when configured) the project audit.

Route from a board that has a same-stem `.kicad_pro`. The scaffold requires it
by default, copies it beside each candidate, and verifies its hash so KiCad DRC
uses the same netclass and board-rule context. Snapshot-only projects may
disable `require_project_rules` only when a stronger project-specific audit
supplies the missing rule authority.
