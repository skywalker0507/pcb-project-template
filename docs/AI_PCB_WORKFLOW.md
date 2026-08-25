# AI PCB workflow

## Machine and project setup

For every new session, run `py -3.14 scripts/session_boot.py`. Its tiny stdout
index points to `build/WORKSET.json`, which contains the active objective,
blockers and next command. Read that file once; treat `read_if_needed` as an
on-demand index rather than a reading list. `stage_doctor.py` remains an on-demand diagnostic,
while routing and export scripts enforce their own stage guards.

Run `py -3.14 scripts/init_project.py --project-name <name>` on each fresh clone.
The command configures project/MCP defaults, points the active board at the
atopile default build output, and resets the tracked handoff to requirements.
Use `--preserve-existing` only when reconfiguring a live project. Install or update
tools manually, then run `python scripts/record_toolchain_receipt.py`. Restart Codex after
`.codex/config.toml` is generated, then run the deep environment check.
An electrical-only receipt may be created first with
`record_toolchain_receipt.py --electrical-only`; production atopile builds and
freezes bind this exact receipt and compiler version. Running the partial
refresh later preserves existing non-electrical tool records and marks them as
unchecked rather than deleting or pretending to refresh them.

Keep API keys in Windows user environment variables such as
`DEEPSEEK_API_KEY`; never commit them.

## Stages

1. requirements (including manufacturer decision);
2. electrical (parts, footprints, build, and designator freeze);
3. physical (mechanics and placement);
4. routing (critical routing and controlled candidate autorouting);
5. release (fabrication/assembly export and subsequent prototype measurements).

The machine-readable stage checklist is authoritative for completion evidence and forbidden actions. The numbered list is explanatory only. `route_ok` means a guarded routing action ran; it is not release approval.

After a functional board checkpoint, run `py -3.14 scripts/pcbctl.py verify`.
Local copper candidates use quick DRC-delta/connectivity checks instead of the
whole pipeline. After electrical source changes, run `py -3.14 scripts/pcbctl.py build`: this creates only an
isolated electrical candidate and cannot audit or export a stale reviewed board.
Run `review_electrical_candidate.py --receipt <electrical-build.json> --output
<new-review-directory>` to generate independent candidate DRC and render
evidence, then create the immutable release-lane electrical freeze bundle.
Create the writable KiCad physical candidate only from its `frozen.kicad_pcb`
with `py -3.14 scripts/pcbctl.py physical-start`. The origin receipt and strict
parity check must pass before placement. Add `--export` only after final
electrical parity, independent KiCad validation, and JLC DFM receipts exist.

Before export, run `py -3.14 scripts/pcbctl.py verify readiness --board
<adopted.kicad_pcb>`. This cheap release preflight reuses the
board-hash/KiCad-context DRC cache and reports all known blockers together:
exact DRC debt, stale parity or review evidence, external DFM order-profile
mismatches, and unconfirmed manufacturing processes. It does not approve
production. The expensive `pcbctl release` command refuses to start until this
receipt is fresh and passing, then independently reruns authoritative gates.

Exact-board release inputs are immutable receipts. Their current selection is
stored in `build/07-release/INPUTS.json`; evidence producers update this small
index even when a check fails. The pipeline validates the pointed receipt and
never treats “latest” as “passing”. Paths in `config/pcb-flow.json` remain only
compatibility fallbacks, avoiding configuration edits after every board change.
The release command defaults to the hash-checked routing `CURRENT.json`
artifact, not the mutable project working board.

Release review is deliberately two-step. `pcbctl verify prepare-review`
prepares deterministic evidence and four views but cannot approve them.
`pcbctl verify finalize-review --packet ... --assessment ...` accepts only a
separate completed assessment, then creates exact DRC and physical-review
receipts. For manufacturing confirmations, use `pcbctl release
confirm-process`; direct edits to `selected_outcome` or unbound prose are not
release evidence.
If a review packet already exists while special-process confirmations remain,
record those confirmations before finalizing the packet; their policy change
would otherwise make a newly created physical review stale.

Before physical/routing/release work, run `py -3.14 scripts/compile_design_policy.py` when its policy inputs changed.
KRT, FreeRouting candidates and export require a fresh policy with the relevant
capability. Release additionally requires `review_pass=true` from
`record_physical_review.py`, bound to both the board SHA and policy SHA.

At a meaningful stage boundary, generate `handoff/stages/CURRENT_STAGE.json`.
A new session is optional, not a gate. Returning from routing to physical
requires a routing-escalation receipt; footprint movement additionally requires
an improved unchanged-outline routability comparison, placement semantics and
passing critical route probes.

## Tool roles

- atopile (default): reviewed production electrical source and isolated KiCad
  candidate generation;
- tscircuit: optional experimental A/B backend; its Circuit JSON is private to
  that adapter and is not part of the common electrical contract;
- KiCad CLI: primary deterministic physical interchange, DRC/connectivity
  validation, rendering, and manufacturing output;
- Konnect MCP: primary bounded live KiCad placement and local trace/via edits
  not represented by deterministic scripts;
- EasyEDA Pro: optional compatibility import, secondary DRC/JLC review, and
  ordering assistance;
- Houtini-LM: optional bounded, non-authoritative text work; never a default failure action;
- KRT: guarded candidate copper, followed by independent audit;
- FreeRouting: headless DSN-to-SES candidate artifact only. Import its SES into
  a separate KiCad candidate board, then run `krt_candidate_audit.py`; a
  successful FreeRouting process is never routing acceptance;
- deterministic Python: orchestration, compact receipts and release gates.

Close live KiCad projects before direct file edits, then rerun the pipeline.

## Efficient validation ladder

- Local copper edit: quick candidate delta/connectivity, plus one bounded crop
  only when triggered by risk.
- Functional block or placement checkpoint: full DRC/connectivity and relevant
  whole-board copper views; placement semantics only if footprints moved.
- Release preparation: one aggregated readiness pass. Resolve its complete
  blocker set without repeatedly invoking the pipeline.
- Production release: one guarded full pipeline after readiness passes.

`status` and `session_boot` never execute DRC or start routing. They only read a
fresh compact readiness receipt; if it is missing or stale, their sole next
command is `pcbctl verify readiness`.
