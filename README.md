# AI PCB project template

Token-efficient, reproducible atopile and KiCad project template for Codex.
It combines:

- the latest atopile release installed and built in WSL;
- a project-scoped atopile MCP bridge;
- the latest `pcb-ai-adapter` branch of the Houtini-LM fork;
- the latest KiCadRoutingTools `main` checkout behind a guarded adapter;
- the latest Konnect MCP as the KiCAD-MCP-Server replacement for live,
  undo-aware KiCad 10 operations;
- a SHA-verified FreeRouting JAR for bounded headless routing;
- deterministic DRC, PCB-context, FreeRouting and JLCPCB export scripts;
- compact stage handoffs that avoid repeatedly loading the whole project.

## Create a project

Create a new repository from this GitHub template, clone it, then run:

```text
python scripts/init_project.py --project-name my-board
```

Konnect is installed by default. To omit it, for example while reviewing its
AGPL/commercial license terms:

```text
python scripts/init_project.py --project-name my-board --no-konnect
```

The initializer uses Python on Windows, installs atopile in the selected WSL
distribution, updates Houtini/KRT to their configured latest branches, builds
their adapters, downloads Konnect and writes `.codex/config.toml` with local
absolute paths. API keys are never
written to the repository.

System prerequisites are Git, Python 3.10+, Node.js 18+, Java 25, WSL Ubuntu
and KiCad 10. The initializer installs project-local dependencies and atopile;
the environment check reports missing system prerequisites explicitly.

Konnect replaces the old KiCAD-MCP-Server integration. It is complementary to
KRT: Konnect's routing tools add explicit trace/via geometry through the live
KiCad IPC API, while KRT remains the guarded batch-routing candidate generator.
See `docs/KONNECT_EVALUATION.md` for the boundary and license note.

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

## Latest-version policy

Repositories and latest channels are declared in `config/toolchain-policy.json`.
Every initializer run fetches the current branch/release and records the actual
versions, commits and download hash in ignored
`build/toolchain-resolved.json`. Run the initializer again whenever you want to
upgrade, then rerun the deep environment check before PCB work.

See `docs/AI_PCB_WORKFLOW.md` for the normal design and release sequence.
