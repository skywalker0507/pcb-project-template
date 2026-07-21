# AI PCB workflow

## Machine and project setup

Run `python scripts/init_project.py --project-name <name>` on each fresh clone.
The command updates configured tools to their latest channels and is idempotent
when those channels have not changed. Restart
Codex after `.codex/config.toml` is generated, then run the deep environment
check.

Keep API keys in Windows user environment variables such as
`DEEPSEEK_API_KEY`; never commit them.

## Stages

1. requirements and manufacturer decisions;
2. exact parts and footprint qualification;
3. modular electrical capture and build;
4. electrical/designator freeze;
5. mechanics and functional placement;
6. critical routing followed by controlled autorouting;
7. fabrication and assembly export;
8. physical prototype measurements.

After source or board changes, run `python scripts/run_pcb_pipeline.py`. Add
`--build` when electrical source changed. Add `--export` only after review when
manufacturing output is intentionally regenerated.

At a stage boundary, generate `handoff/stages/CURRENT_STAGE.json` and begin the
next meaningful stage in a fresh Codex session.

## Tool roles

- atopile: electrical design, parts, nets and generated board baseline;
- Konnect MCP: live placement, routing, rendering and operations not represented
  by deterministic scripts;
- Houtini-LM: bounded, non-authoritative text work;
- KRT/FreeRouting: candidate copper only, followed by independent audit;
- deterministic Python: orchestration, compact receipts and release gates.

Close live KiCad projects before direct file edits, then rerun the pipeline.
