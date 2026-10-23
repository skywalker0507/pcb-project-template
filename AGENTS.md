# Codex instructions for AI PCB projects

This repository has one production path:

`requirements -> electrical -> physical -> routing -> release`

Reviewed requirements/`.ato` are electrical authority; KiCad is physical
authority/validator. KRT creates candidates; integrations are optional.

## Start and command surface

Run `py -3.14 scripts/session_boot.py` once per session. Follow `next_command`,
read only `read_now`, and open `read_if_needed` only after a related failure.

Linux: `.venv-pcb/bin/python`; KiCad: `pcb_runtime`. Setup: `docs/LINUX.md`.

Use only these default commands:

`status / build / freeze / physical-start / place / route / verify / release`

Dispatch with `py -3.14 scripts/pcbctl.py <command>`; run `pcbctl build --plan`
before compiling. Keep optional entrypoints outside default context.

## Hard invariants

- Physical work consumes an immutable passing electrical-freeze bundle. Any
  connectivity, identity, part, footprint or net-name change returns to `.ato`.
- Preserve reviewed parts, designators, nets and physical work. Never invent
  fabrication, clearance, width or impedance values.
- Never put a complete PCB, schematic, DSN or SES file in model context. Use
  deterministic tools, compact receipts and bounded geometry queries.
- Router success is candidate evidence. Release requires exact-board DRC,
  connectivity, parity, visual and manufacturing gates.
- Release Gerbers and DFM scope must match the compiled registry; project rules
  stay out of engine code.
- Run `pcbctl verify readiness` before release; export rejects stale or blocked
  readiness for the adopted board.
- Never regenerate a baseline to conceal a regression or bypass a guard.

## Validation tiers

- `quick`: after local copper edits, run candidate checks, DRC delta and
  connectivity. Render a changed-copper crop only for critical, awkward or
  uncertain edits.
- `checkpoint`: after a functional block or placement checkpoint, run full DRC
  and connectivity. If footprints moved, also run placement semantics and
  routability comparison. Review whole-board copper views.
- `release`: run the full pipeline, exact-board placement semantics, four-view
  visual review, parity and manufacturing gates.

Do not rerun placement semantics when footprints did not move. Do not render
3D or run the release pipeline after every trace.

At a functional checkpoint, after two related rejections, or before a third
same-board full verification, run `pcbctl verify workflow`. Finish the bounded
quick batch first, then remove reported repetition. It is metadata-only and
cannot relax gates. Template changes use `pcbctl verify template`; logs stay on
disk.

## Placement and routing contract

Physical-start must pass footprint/rule-area integrity. Place by function, not
alignment. After moves recheck decoupling, power loops, protection,
termination, antenna keepouts and connector access. Before routing, own release
debt and compare airwires, congestion and critical probes. Route critical
power, return and high-speed paths first; batch low-risk nets.

Classify nets before KRT. Use bounded trace plans for short two-endpoint links;
reserve KRT for path search or low-risk batches. Copper preflight is
rejection-only; exact DRC and connectivity remain authoritative.

Route related low-risk nets per candidate and audit once per block. Run long
KRT work through `pcbctl route start/status/finalize`; observation must not
restart or revalidate it.
At zero unconnected items and a passing checkpoint review, run
`pcbctl route checkpoint-finalize` to bind receipts and refresh state.
Every attempt names stable `--route-area` and structural `--strategy-class`;
changing nets or numeric tuning does not reset escalation or define a strategy.
Dry-run emits the work packet. Three rejected area candidates require
diagnosis; this never authorizes a footprint move.
`--route-bbox-mm` scopes obstacle identity and the reference corridor;
otherwise fingerprint the whole board. Copper excursion triggers local visual
review, not structural failure or automatic placement return.
Only no-path or incomplete-connectivity results count as structural failures;
tool, guardrail and candidate-DRC failures stay in routing. After two structural
failures, record an escalation and review routing order/corridors first. A
footprint move requires two distinct failed strategies plus an improved
routability comparison, passing placement semantics and critical route probes.

Keep logs/candidates in `tmp`, compact receipts in `evidence`, and passing
artifacts in `release`. Accepted results update stage `CURRENT.json`; handoffs
cannot adopt `tmp`. Create handoffs only at meaningful checkpoints; continue
bounded work without expanding it to full-stage completion.
