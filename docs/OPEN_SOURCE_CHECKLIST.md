# Pre-publication checklist

**Status: BLOCKED for publishing the original repository.** Full-history audit found a separate hardware-development lineage; read [Git history and provenance audit](OSS_HISTORY_AUDIT_2026-10-21.md). The safest publication path is a new, clean-snapshot repository.\n\n**Previous status:** preparatory branch only. Keeping a GitHub repository private while preparing it is intentional. The maintainer must explicitly decide when to change visibility.

## Before changing visibility

- [ ] Confirm ownership and redistribution rights for **every current file** and relevant historical commit, including vendor footprints and docs.
- [ ] Review/accept the proposed **MIT** license (or choose another compatible license). Check inherited code and dependency notices.
- [ ] Review `git log --all --stat`, `git ls-files`, GitHub PRs/issues, and the **entire history**, not only the latest files. Run a reputable secret scanner locally or in a private CI environment.
- [ ] Rotate any accidentally committed tokens, keys and credentials; rewrite or re-create exposed history if needed **before** making the repository public.
- [ ] Inspect `.codex`, `config`, `handoff`, `docs`, tests and examples for customer details, internal IPs, proprietary designs, confidential fabrication quotes, email addresses and developer machine paths.
- [ ] Check that `.gitignore` covers local MCP config, logs, generated builds, manufacturing output, credentials and stage handoffs.
- [ ] Verify links and licensing of Konnect, KRT, Houtini-LM, KiCad, atopile and FreeRouting at actual versions.
- [ ] Run `python -m unittest discover -s tests -p 'test_*.py' -v` and `python scripts/check_open_source_ready.py` locally.
- [ ] Run a new-project smoke test in a clean clone. Check that `init_project.py` does **not** install tools or publish data.
- [ ] Ensure CI passes on the proposed public default branch; do not claim hardware testing from Python unit tests.
- [ ] Review the README, Quick start, CONTRIBUTING and SECURITY for accuracy. Enable GitHub private vulnerability reports if available.
- [ ] Add an informative GitHub repository description, topics, and homepage or demo if available.
- [ ] Decide what evidence to provide for **Codex for OSS**: maintainership, concrete usage, maintenance cadence, reproducible tests and roadmap.

## Suggested initial roadmap

1. Make first-run Windows/WSL setup reproducible across clean environments, without hidden dependencies.
2. Provide a small, independently reviewed **example PCB project** (schematic, DRC, screenshots and test data with redistributable rights).
3. Add vendor-independent fabrication profiles and better policy/unit-test fixtures.
4. Keep router candidates isolated and verify KiCad 10 compatibility with a real EDA integration run.
5. Document trustworthy Codex/MCP usage, human review points and regression evidence.

## Evidence to collect for the application

- Links to specific improvements, reviewed pull requests, unit tests and releases.
- A short explanation of which open-source users benefit and why existing tools do not cover the same workflow.
- Verified usage signals (issues, discussions, genuine stars/forks, downstream uses) when they exist; never invent them.
- A concrete six-month plan describing how Codex reduces maintenance work.

The static checker cannot prove that all old commits are clean, that a license is legally sufficient, or that a PCB is safe for manufacture. A human review remains mandatory.
