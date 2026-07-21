# AI PCB project template

Token-efficient, reproducible atopile and KiCad project template for Codex.
It combines:

- atopile 0.15.7 builds isolated in WSL;
- a project-scoped atopile MCP bridge;
- a pinned Houtini-LM fork for bounded DeepSeek delegation;
- a pinned KiCadRoutingTools checkout behind a guarded adapter;
- a SHA-verified FreeRouting JAR for bounded headless routing;
- deterministic DRC, PCB-context, FreeRouting and JLCPCB export scripts;
- compact stage handoffs that avoid repeatedly loading the whole project.

## Create a project

Create a new repository from this GitHub template, clone it, then run:

```text
python scripts/init_project.py --project-name my-board
```

The initializer uses Python on Windows, installs atopile in the selected WSL
distribution, clones the locked Houtini/KRT revisions, builds both adapters and
writes `.codex/config.toml` with local absolute paths. API keys are never
written to the repository.

System prerequisites are Git, Python 3.10+, Node.js 18+, Java 25, WSL Ubuntu
and KiCad 10. The initializer installs project-local dependencies and atopile;
the environment check reports missing system prerequisites explicitly.

Restart Codex after initialization, then verify:

```text
python scripts/check_pcb_environment.py --deep
```

`--deep` includes a real atopile smoke build and KRT capability check. A
passing router check is not PCB release approval.

## Project layout

- `src/`: single electrical design authority;
- `elec/layout/`: single KiCad layout authority;
- `config/`: reviewed project and tool policy;
- `scripts/`: deterministic project-local workflow;
- `experiments/`: disposable candidate boards and A/B trials;
- `handoff/stages/`: compact cross-session stage state;
- `manufacturing/`: intentional release exports;
- `build/`: disposable logs, reports and caches.

Stages do not duplicate `src/` or `elec/layout/`.

## Locked external tools

Exact repositories, commits and the compatible KRT native release are in
`config/toolchain-lock.json`. Upgrades
are deliberate: test a new commit independently, update the lock, rerun unit
tests and `check_pcb_environment.py --deep`, then commit the lock change.

See `docs/AI_PCB_WORKFLOW.md` for the normal design and release sequence.
