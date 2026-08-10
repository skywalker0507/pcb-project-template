# Architecture direction: constraint compiler and deterministic validation

## Status and intent

This is the approved direction for future template work, not a statement that
all listed capabilities already exist. The current project remains governed by
the operational stage handoff. Do not treat this roadmap as release evidence or
as permission to bypass existing guards.

The priority order is **accuracy, stability, Codex-context reduction, then
speed**. The target is a constraint compiler and validation pipeline in which
LLMs are narrow-interface operators, never design or release authorities.

## Authority hierarchy

1. `requirements.json` is the product-intent authority: interfaces, rails,
   current, mechanics, constraints, explicit unknowns, and requested
   manufacturer.
2. Reviewed `.ato` sources are the production electrical-realization authority.
   tscircuit is an optional experimental backend and cannot create a production
   electrical freeze.
3. A reviewed manufacturer capability source is the numeric fabrication
   authority. Models must never supply missing numeric values.
4. A passing `ELECTRICAL_FREEZE` is the one-way boundary into physical work.
5. EasyEDA Pro is the primary physical backend. KiCad is the native atopile
   interchange, independent validator, and fallback/reference backend.
6. KiCadRoutingTools is a GUI-independent routing engine; FreeRouting is secondary.
7. A generated resolved policy is the executable contract consumed by routing,
   DFM checks, handoffs, and release guards.

No generated artifact supersedes its reviewed input. EasyEDA, KiCad, KRT, and
FreeRouting may not become electrical authorities. A changed input invalidates
the resolved-policy receipt and all dependent evidence.

## Target pipeline

```text
requirements + reviewed frozen modules + board stack + reviewed fab capability
                                  |
                                  v
                      compile_design_policy.py
                                  |
                                  v
                  build/00-shared/evidence/design-policy/design-policy-resolved.json
                                  |
       +--------------------------+---------------------------+
       |                          |                           |
       v                          v                           v
electrical-backend checks placement/critical-net checks  KRT/DFM/KiCad rules
       \                          |                           /
        \                         v                          /
         +---- SHA-bound DRC + physical review evidence -----+
                                  |
                                  v
 EasyEDA parity + EasyEDA DRC + KiCad validation + JLC DFM + release_pass
                         AND review_pass -> manufacturing export
```

Long text is a separate path: deterministic scripts retain logs on disk and
may create a Houtini job only when the delegation policy permits it. The
sidekick returns a bounded JSON draft; it has no authority over any gate.
Normal sessions begin from `session_boot` and `stage_doctor`, not from a full
board or long log.

## Decisions recorded

- Prefer calculation and deterministic checks over LLM inference for layers,
  widths, spacing, unconnected items, forbidden layers, BOM/CPL consistency,
  and rule provenance.
- Reuse only modules whose electrical and physical provenance includes actual
  validation. A module is not "frozen" merely because an LLM produced it.
- Keep critical nets (for example USB, clock, reset and power loops when
  declared by requirements) out of broad autorouting. Candidate routers remain
  limited to explicitly selected, low-risk nets.
- Add a second, SHA-bound `review_pass` release receipt for declared placement,
  critical-net, return-path, and mechanical evidence. It complements, never
  replaces, `release_pass`.
- Use generic placement/return-path heuristics only as triage. A hard failure
  must be traceable to a project requirement or a reviewed manufacturing rule;
  do not fabricate confidence from a universal score.
- External KiCad or Konnect edits cannot be completely prevented. Their board
  SHA changes invalidate dependent receipts, so later guarded operations must
  rerun the required checks.

## Delivery order

### P0: executable intent and policy

1. Strengthen the requirements schema and validate required evidence before the
   physical stage.
2. Implement `compile_design_policy.py` to produce one SHA-bound resolved
   policy plus KRT, DFM, and KiCad-rule artifacts from requirements, board stack,
   and reviewed manufacturing values.
3. Make KRT, DFM, handoff, and export reject stale or missing resolved policy.
4. Evolve `stage_doctor` into a small command bus with explicit allowed
   commands and evidence gaps per stage.

### P1: accuracy checks that derive from intent

1. Introduce manufacturer capability cards with source/version/hash, then
   compile reviewed values into KiCad-compatible rule artifacts, KRT inputs,
   and DFM checks.
2. Add explicit placement regions, critical-net declarations, and mechanical
   constraints to requirements; implement bounded placement and critical-net
   checkers.
3. Add `review_pass` and bind it to the exact reviewed board SHA.

### P2: validated reuse and optional inspection

1. Build a reference-module library only after each module has board, test, and
   provenance evidence.
2. Add interface-contract checkers for modules with declared requirements.
3. Add targeted rendered-image inspection only where deterministic checks cannot
   cover a stated review question. It is advisory, not release evidence.

## Already implemented foundations

- Stage checklists, stage guard coverage for candidate routing/export, compact
  session boot, bounded handoffs, SHA-bound DRC baseline and release audit.
- Candidate-only KRT/FreeRouting paths and a compact candidate diff.
- Reviewed fabrication-value recording via `apply_reviewed_fab_values.py`;
  unreviewed values cannot authorize real KRT routing.
- Houtini large-text eligibility jobs: short receipts are not delegated; jobs
  use Flash, disabled thinking, and bounded JSON output.

## Explicit non-goals

- No end-to-end autonomous route-to-manufacturing path.
- No LLM-created numeric manufacturing, impedance, or electrical rules.
- No additional agent framework as a substitute for deterministic commands.
- No universal physical-quality score presented as manufacturing approval.
