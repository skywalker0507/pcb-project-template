# Git history and source provenance audit — 2026-10-21

## Publication decision: BLOCKED for the original repository

**This is an engineering release audit, not a legal opinion.** The repository remains private. Do **not** change visibility of this existing Git repository merely because its `main` branch is template-only.

### Audited scope

- GitHub repository: `skywalker0507/pcb-project-template`
- Git references fetched: `main`, `chore/oss-readiness`, `agent/tscircuit-first-migration`, three annotated `pcb-template-v1.x` tags, and PR #1 head/merge refs.
- Evidence: [successful private full-history CI run](https://github.com/skywalker0507/pcb-project-template/actions/runs/39568172597) and its retained `git-history-audit` artifact.
- Reachable Git commits: **22** (includes PR merge/test refs).
- Git objects: **878**; unique blobs: **696**; scanned blobs: **696**.
- Skipped oversized blobs: **0**.
- Automated findings matching configured high-confidence key patterns: **0**; findings matching configured contextual email, private IP, developer path, or literal-password heuristics: **0**.
- Commit author name/email identity combinations: **3**. Identity metadata is visible to people who can inspect a public Git log; review it privately before publishing.

**Scope limits:** Only refs available in this full CI checkout are covered. This cannot inspect GitHub-server unreachable/dangling objects, undeclared LFS objects, secret values hidden via encoding/encryption, or any code residing in external tools. Regular-expression tests are not proof that sensitive material does not exist. No copyright registration or employee/client assignment documentation was available for independent legal validation.

### Provenance and disclosure risks found

| Priority | Finding | Evidence / impact | Decision |
| --- | --- | --- | --- |
| **Blocking** | Non-template proprietary/project-specific design history | The `agent/tscircuit-first-migration` lineage includes `HARDWARE_SPEC.md`, an ESP32-S3 remote flashing probe's physical Rev A PCB, power/network/interface requirements, and manufacturing decisions; historical commit `b5cd5e86` introduces many of them. | Keep original repo private; do not publicize full history by changing visibility. |
| **Blocking** | Redistributability of embedded CAD assets unproven | History scan flagged **61 unique historical CAD asset blob versions** (KiCad symbols, footprints and STEP files) for rights review; some sampled files use `faebryk_convert` and LCSC/EasyEDA data, but lack file-level license notice. | Exclude these assets from the public template until exact source and permission are verified. |
| **High** | Original MIT license is not a blanket third-party sublicense | New root MIT license applies only where contributor(s) have authority. External tools and KiCad-library collection redistribution retain different terms. | Keep explicit third-party notices; never imply a proprietary/vendor part is covered by the root MIT. |
| **Review** | Historic Git commit author metadata | Three distinct author identity combinations can be exposed when publishing history. | Decide whether author emails are acceptable; a new clean repo history removes this class of historic metadata from the public copy. |
| **Review** | Ownership of scripts/docs from AI-assisted and external integration work | Commits are under the owner account, but Git authorship alone cannot prove the source is entirely original or that employment/client rights are unrestricted. | Maintainer must verify authorship, obligations and third-party copied snippets before publication. |

### Upstream license spot checks

- [atopile](https://github.com/atopile/atopile): repository declares MIT.
- [KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools): upstream repository declares MIT; verify your separately maintained fork/commit and notices.
- [Konnect](https://github.com/mixelpixx/Konnect/blob/main/COMMERCIAL.md): AGPL-3.0 with commercial license available. Do not bundle or relicense this component as MIT.
- [KiCad symbol libraries](https://github.com/KiCad/kicad-symbols/blob/master/LICENSE.md): CC-BY-SA 4.0 with exception for designs using the libraries; **the exception is not a blanket permit to redistribute a copied library collection under MIT**.
- LCSC / EasyEDA / manufacturer provided symbols, footprints and STEP models: redistribution conditions for each asset are **unverified**; generator markings do not grant rights.
- Houtini-LM and specific FreeRouting release: additional commit/version-specific dependency license review remains required if bundling or redistributing them.

### Recommendation

For the **Codex for OSS** application, publish a **new GitHub repository with a clean initial commit containing only reviewed generic template files** (from `main` plus approved changes in `chore/oss-readiness`). Keep the original repository and its hardware-focused `agent/tscircuit-first-migration` branch **private**.

Do not rely on simply deleting a branch or removing files in a new commit; old objects can persist in tags, PR refs, cached clones or GitHub service retention. Changing original repository visibility is **not approved** by this audit.

### Follow-up acceptance criteria

- [x] Enumerate branch/tag/PR refs and perform a history-based Git object scan.
- [x] Perform targeted provenance inspection on the historical hardware/asset lineage.
- [x] Verify a selection of the external upstream license terms from published repositories.
- [ ] Verify code ownership and third-party license rights for every file selected for the public snapshot.
- [ ] Prepare and inspect a fresh snapshot-based repository containing **no historical commits from the original**.
- [ ] Re-run the full audit on that public-candidate repo, check all tree paths, and verify no vendor or customer PCB source was included.
- [ ] Review author attribution, README claims and release artifact policy.
- [ ] Obtain explicit approval before publishing.

This report intentionally does not reproduce actual secret values, commit emails or private design measurements.
