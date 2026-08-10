# AI PCB workflow

## Machine and project setup

For every new session, read in this order: `handoff/stages/CURRENT_STAGE.json`, its `stage_rules`, `config/pcb-acceptance.json`, and `config/board-stack.json`. `--tier minimal` permits requirements work; physical work requires the relevant KiCad checks.

Run `python scripts/stage_doctor.py` before choosing a workflow command. It is the authoritative navigation receipt; routing and export scripts enforce their own stage guard.

Run `python scripts/init_project.py --project-name <name>` on each fresh clone.
The command only writes MCP configuration. Install or update tools manually,
then run `python scripts/record_toolchain_receipt.py`. Restart Codex after
`.codex/config.toml` is generated, then run the deep environment check.

Keep API keys in Windows user environment variables such as
`DEEPSEEK_API_KEY`; never commit them.

## Stages

1. requirements (including manufacturer decision);
2. electrical (parts, footprints, build, and designator freeze);
3. physical (mechanics and placement);
4. routing (critical routing and controlled candidate autorouting);
5. release (fabrication/assembly export and subsequent prototype measurements).

The machine-readable stage checklist is authoritative for completion evidence and forbidden actions. The numbered list is explanatory only. `route_ok` means a guarded routing action ran; it is not release approval.

After board changes, run `python scripts/run_pcb_pipeline.py`. After electrical
source changes, run it with `--build`: this creates only an isolated electrical
candidate, then stops so it cannot audit or export a stale reviewed board.
Run `review_electrical_candidate.py --receipt <electrical-build.json> --output
<new-review-directory>` to generate independent candidate DRC and render
evidence, then create the SHA-bound electrical freeze. Import only that frozen
`.kicad_pcb` through the EasyEDA bridge and require
`check_easyeda_import_parity.py` to pass before placement. Add `--export` only
after EasyEDA DRC, independent KiCad validation and JLC DFM receipts exist.

Before physical/routing/release work, run `python scripts/compile_design_policy.py`.
KRT, FreeRouting candidates and export require a fresh policy with the relevant
capability. Release additionally requires `review_pass=true` from
`record_physical_review.py`, bound to both the board SHA and policy SHA.

At a stage boundary, generate `handoff/stages/CURRENT_STAGE.json` and begin the
next meaningful stage in a fresh Codex session.

## Tool roles

- atopile (default): reviewed production electrical source and isolated KiCad
  candidate generation;
- tscircuit: optional experimental A/B backend; its Circuit JSON is private to
  that adapter and is not part of the common electrical contract;
- EasyEDA Pro: primary placement, physical editing, routing orchestration, DRC,
  and JLC DFM backend once its official bridge/API adapter is connected;
- KiCad CLI: interchange, independent DRC/connectivity validation, and fallback;
- Konnect MCP: KiCad fallback/reference live operations not represented by deterministic scripts;
- Houtini-LM: bounded, non-authoritative text work;
- KRT: guarded candidate copper, followed by independent audit;
- FreeRouting: headless DSN-to-SES candidate artifact only. Import its SES into
  a separate KiCad candidate board, then run `krt_candidate_audit.py`; a
  successful FreeRouting process is never routing acceptance;
- deterministic Python: orchestration, compact receipts and release gates.

Close live KiCad projects before direct file edits, then rerun the pipeline.
