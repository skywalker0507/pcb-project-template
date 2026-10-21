# Token-efficient KiCadRoutingTools AI adapter

The repository treats `tools/KiCadRoutingTools` as an updateable upstream
checkout. All project policy and automation live outside that directory:

- `config/kicad-routing-tools.json`: resolved tool commit, net profiles, numeric
  rules, forbidden layers, artifact paths and acceptance policy;
- `config/routing-planner.json`: deterministic task classification and reviewed
  functional routing order;
- `config/krt-fab-overrides.txt`: exact reviewed fabrication floor, which also
  disables the upstream router's automatic standard-to-advanced escalation;
- `scripts/krt_flow.py`: compact check/route interface;
- `scripts/krt_checkpoint.py`: detached start/status/finalize lifecycle for
  routes that outlive an Agent tool call;
- `scripts/init_project.py`: project MCP configuration only;
- `scripts/record_toolchain_receipt.py`: records a manually installed toolchain.

## AI usage protocol

Classify the requested nets before choosing an executor. A short, unrouted,
two-pad connection within the configured span is handled by a bounded explicit
trace plan. KRT is used for genuine path search or a useful batch of related
low-risk nets. The classifier also enforces reviewed functional order so a
later trunk does not consume an earlier connector escape corridor.

Start KRT with a small, low-risk network profile. Keep power loops, ground,
high-speed differential pairs, clocks, RF, sensitive analog/reference nets,
reset/boot safety controls and connector protection paths excluded until a
project-specific review explicitly enables them.

```text
py -3.14 scripts/krt_flow.py check --input <board-from-config/pcb-flow.json>

py -3.14 scripts/krt_flow.py route \
  --profile outer_low_risk \
  --input <board-from-config/pcb-flow.json> \
  --route-area <stable-functional-block-name> \
  --strategy-class <stable-structural-strategy-name> \
  [--route-bbox-mm <x0> <y0> <x1> <y1>] \
  --run-id low-risk-01 \
  --dry-run
```

Remove `--dry-run` only after checking the resolved net list. Dry-run creates no
artifact directories. A real command returns one compact JSON line. Unless `--output` is explicitly supplied, the
candidate and full log stay under `build/06-routing/tmp/<run-id>/`; the bounded
decision report stays under `build/06-routing/evidence/<run-id>/`.

For a real route that may take longer than one tool-call timeout, use the default
dispatcher lifecycle:

```text
py -3.14 scripts/pcbctl.py route start --run-id low-risk-01 <route arguments>
py -3.14 scripts/pcbctl.py route status --run-id low-risk-01
py -3.14 scripts/pcbctl.py route finalize --run-id low-risk-01
```

`start` records a process manifest and launches one detached route. `status`
only observes that process. `finalize` reads the already-produced compact KRT
report, so a frontend timeout cannot cause a duplicate route or duplicate audit.

Bounded explicit trace plans perform a fast geometric preflight against outer
layer tracks, vias and pad copper, plus planned copper, drilled holes and the
board edge. Pad geometry is intentionally conservative and approximate; this
gate can reject an obviously blocked plan before candidate creation, but only
exact KiCad candidate DRC and connectivity can accept it.

The adapter always passes `--no-fix-drc-settings`. Older KRT revisions also
receive `--no-clamp-netclasses`; newer revisions removed that switch, so the
adapter verifies the configured clearance against the KiCad Default class and
omits KRT's clearance ceiling, preserving native netclasses. It scans output copper independently and rejects any
segment on configured forbidden signal layers. A zero upstream exit code is
not acceptance: incomplete nets, input mutation, forbidden-layer copper, or a
failed configured audit all make the receipt unsuccessful.

`route_ok` means the selected routing action ran and its local layer/input
guards passed. `success` is stricter and means the configured candidate
acceptance gates passed. During staged routing it is normal for `route_ok` to
be true while `success` remains false because the rest of the board is still
unconnected. Open the full log only for the specific failed nets or diagnostics
named in the receipt. Optional text delegation is disabled for default routing
failures.

Only no-path or incomplete-connectivity outcomes count as structural failures.
Candidate DRC, audit, guardrail and tool failures remain routing work. Two
independent structural strategies in one `--route-area` require an escalation
receipt and corridor review; they do not directly authorize footprint moves.

Use `py -3.14 scripts/krt_flow.py history --limit 5` to orient a new Codex
session, then `py -3.14 scripts/krt_flow.py report --run-id <id>` for a bounded
failure view. Dry runs and receipts cap all net/failure previews; their full
lists live only in report files.

## Initialize the project

```text
py -3.14 scripts/init_project.py --project-name new-board
```

Manually fetch the configured KRT branch from `config/toolchain-policy.json`,
create the isolated Python environment and build the matching native router as
upstream directs. Then run `record_toolchain_receipt.py`. Fill the profile's
`null` numeric values only after
reviewing the new project's KiCad rules; routing intentionally refuses to run
while they remain unset.

The generic scaffold includes `krt_candidate_audit.py`. It runs KiCad JSON DRC
on the input and candidate, rejects any new non-connectivity violation object,
and rejects an increase in unconnected items. A mature project may replace
that command with a stricter reviewed object baseline, as this repository does.

By default the generic scaffold requires a same-stem `.kicad_pro` beside the
input board. It copies that file beside the output candidate and verifies the
hash after routing, ensuring candidate DRC uses the same netclass and board
rules. This repository's isolated experiment snapshots intentionally lack that
companion file, so its specialized config permits them only because the
reviewed project audit remains authoritative.

The repository supplies durable AI instructions in `AGENTS.md`. Identical
input/net/parameter attempts are limited to two by default; change the routing
policy or inspect the prior report instead of blindly repeating the same run.
