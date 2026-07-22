# AI PCB project template

Token-efficient, reproducible atopile and KiCad project template for Codex.
It combines:

- the latest atopile release installed and built in WSL;
- a project-scoped atopile MCP bridge;
- the latest `pcb-ai-adapter` branch of the Houtini-LM fork;
- the project-maintained KiCadRoutingTools `codex/repository-support` checkout
  behind a guarded adapter;
- the latest Konnect MCP as the KiCAD-MCP-Server replacement for live,
  undo-aware KiCad 10 operations;
- a SHA-verified FreeRouting JAR for bounded headless routing;
- deterministic DRC, PCB-context, FreeRouting and JLCPCB export scripts;
- compact stage handoffs that avoid repeatedly loading the whole project.

## Create a project

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

System prerequisites are Git, Python 3.10+, Node.js 18+, Java 25, WSL Ubuntu
and KiCad 10. The environment check reports missing prerequisites and manual
remediation steps without changing the machine.

Konnect replaces the old KiCAD-MCP-Server integration. It is complementary to
KRT: Konnect's routing tools add explicit trace/via geometry through the live
KiCad IPC API, while KRT remains the guarded batch-routing candidate generator.
See `docs/KONNECT_EVALUATION.md` for the boundary and license note.

Restart Codex after initialization. `--tier minimal` is sufficient to begin requirements work; verify the full local toolchain before design work:

```text
python scripts/check_pcb_environment.py --deep
```

`--deep` includes a real atopile smoke build and KRT capability check. A
passing router check is not PCB release approval.

Before each new session, run `python scripts/session_boot.py`; it replaces opening the whole board or long logs by default. Use `python scripts/select_manufacturing_profile.py --manufacturer <name> --source <URL-or-card>` to record a reviewed process source before manufacturing export; this command does not invent fabrication values.

Selecting a source does not authorize real routing. After reviewing it, record explicit values and evidence with `apply_reviewed_fab_values.py`; only then can a non-dry-run KRT attempt proceed.

Before physical placement, routing, or release, compile the shared executable policy:

```powershell
py -3.14 scripts/compile_design_policy.py
py -3.14 scripts/stage_doctor.py
```

The receipt at `build/design-policy-resolved.json` records hashes of requirements,
layer stack, manufacturing evidence, KRT policy, and acceptance rules. Changing
any of those inputs invalidates it. A release additionally needs a SHA-bound
physical review created by `record_physical_review.py`; `release_pass` alone is
not sufficient.

After an intentional JLCPCB export, run `check_jlcpcb_release.py --package <export-dir> --output build/reports/jlc-package-check.json` for a compact Gerber/drill/BOM/CPL sanity receipt. It is not release approval. Only after that receipt and the matching audit have passed, create a non-overwriting evidence snapshot with `create_release_snapshot.py --package <export-dir> --package-check build/reports/jlc-package-check.json`.

Use `python scripts/stage_doctor.py` at session start. It returns the current stage, bounded blockers, and one next command. `config/requirements.json` is the structured requirements record; validate it with `python scripts/validate_requirements.py` before electrical capture.

Manufacturing exports are SHA-bound to a passing KiCad release-audit receipt. Use
`run_pcb_pipeline.py --export`; invoking the export script directly also
requires the matching audit JSON and cannot bypass DRC approval.

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
After manually installing or upgrading a tool, record the actual versions,
commits and hashes in ignored `build/toolchain-resolved.json` with
`record_toolchain_receipt.py`, then rerun the deep environment check before PCB
work.

See `docs/AI_PCB_WORKFLOW.md` for the normal design and release sequence.
