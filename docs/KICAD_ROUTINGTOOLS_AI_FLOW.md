# Token-efficient KiCadRoutingTools AI adapter

The repository treats `tools/KiCadRoutingTools` as an updateable upstream
checkout. All project policy and automation live outside that directory:

- `config/kicad-routing-tools.json`: resolved tool commit, net profiles, numeric
  rules, forbidden layers, artifact paths and acceptance policy;
- `config/krt-fab-overrides.txt`: exact reviewed fabrication floor, which also
  disables the upstream router's automatic standard-to-advanced escalation;
- `scripts/krt_flow.py`: compact check/route interface;
- `scripts/init_project.py`: unified atopile, Houtini and KRT initializer.

## AI usage protocol

Start with a small, low-risk network profile. Keep power loops, ground,
high-speed differential pairs, clocks, RF, sensitive analog/reference nets,
reset/boot safety controls and connector protection paths excluded until a
project-specific review explicitly enables them.

```text
python scripts/krt_flow.py check --input elec/layout/default/default.kicad_pcb

python scripts/krt_flow.py route \
  --profile outer_low_risk \
  --input elec/layout/default/default.kicad_pcb \
  --output build/candidates/krt-low-risk.kicad_pcb \
  --run-id low-risk-01 \
  --dry-run
```

Remove `--dry-run` only after checking the resolved net list. The command
returns one compact JSON line. Full router and audit output stays under
`build/logs/krt/`; the complete machine-readable decision record stays under
`build/reports/krt/`.

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
named in the receipt; large first-pass log classification may be delegated by
log path to Houtini without sending the PCB file.

Use `python scripts/krt_flow.py history --limit 5` to orient a new Codex
session, then `python scripts/krt_flow.py report --run-id <id>` for a bounded
failure view. Dry runs and receipts cap all net/failure previews; their full
lists live only in report files.

## Initialize the project

```text
python scripts/init_project.py --project-name new-board
```

The initializer fetches the latest configured KRT branch from
`config/toolchain-policy.json`, records its resolved commit, creates an
isolated Python environment and builds the matching latest native router. Fill
the profile's `null` numeric values only after
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

The initializer also installs durable AI instructions. If the target has no
`AGENTS.md`, it creates one; otherwise it writes
`docs/AGENTS_KRT_FLOW_FRAGMENT.md` for deliberate merging without overwriting
existing project rules. Identical input/net/parameter attempts are limited to
two by default; change the routing policy or inspect the prior report instead
of blindly repeating the same run.
