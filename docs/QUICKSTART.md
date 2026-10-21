# Quick start

**Supported primary workflow:** Windows with Python 3.10+, WSL Ubuntu, Node.js, KiCad 10, atopile and optional KiCad routing tools. This template is also inspectable and unit-testable on Linux/macOS, but the complete EDA/MCP workflow is currently Windows/WSL-oriented.

## 1. Obtain a clean copy

Prefer **Use this template** on GitHub to start a new repository, rather than cloning this repository and storing unrelated projects in its history.

```powershell
git clone https://github.com/<you>/<your-project>.git
cd <your-project>
py -3 scripts/init_project.py --project-name demo-board --layers 2 --no-konnect
```

The command creates a **local** `.codex/config.toml`, initializes `handoff/stages/CURRENT_STAGE.json`, and updates tracked project policy placeholders in your **new project copy**. It does not clone or install third-party applications. `--no-konnect` keeps the first-run setup simple; choose `--with-konnect` only if you intend to configure that optional service.

## 2. Validate before installing the EDA stack

```powershell
py -3 -m unittest discover -s tests -p "test_*.py" -v
py -3 scripts/validate_requirements.py
py -3 scripts/check_open_source_ready.py
py -3 scripts/check_pcb_environment.py --tier minimal
py -3 scripts/stage_doctor.py
```

`check_open_source_ready.py` is a lightweight source-control hygiene check, **not** a substitute for a secret-history audit or license review. Requirements intentionally start in draft with an explicit unknown. A passing smoke test **does not** assert that routing or production is safe.

## 3. Set up the full local environment

Read [AI PCB Workflow](AI_PCB_WORKFLOW.md) and [third-party terms](THIRD_PARTY.md) first. Manually install and review external tools; avoid unreviewed bulk install scripts.

```powershell
py -3 scripts/check_pcb_environment.py --tier standard
```

After installing the exact tools you selected, record actual versions/commits and artifact hashes:

```powershell
py -3 scripts/record_toolchain_receipt.py --distro Ubuntu-24.04 --without-konnect
py -3 scripts/check_pcb_environment.py --tier deep
```

The default local config and setup scripts use Windows paths/WSL. Do not copy those paths into CI or other operating systems as-is. If you chose Konnect, omit `--without-konnect` after installing and reviewing it.

## 4. Design and release gates

Update `config/requirements.json` with real requirements, and resolve blocking unknowns. Then use `stage_doctor.py` to determine the allowed next action.

Electrical, placement, routing and release stages have separate gates. **No manufacturing export may be treated as approved from unit tests, an AI answer, or a successful router run alone.** Actual KiCad DRC, matching SHA-bound policy, physical review, source-backed fabricator rules and validated Gerbers are required.

## Common errors

- **No CURRENT_STAGE.json**: run `init_project.py` first. Its generated stage file is deliberately excluded from source control.
- **Missing tools**: use `check_pcb_environment.py` suggestions; tests should not install missing dependencies on your behalf.
- **Routing/export blocked**: do not bypass acceptance guards. Supply verified requirements and manufacturing evidence.
- **Config already exists**: `init_project.py` preserves it and writes a merge fragment; review the fragment instead of overwriting secrets.
