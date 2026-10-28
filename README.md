# AI PCB project template

A reproducible, audit-first PCB workflow for Codex. Reviewed atopile source is
the electrical authority, KiCad is the physical authority and independent
validator, and KiCadRoutingTools (KRT) creates isolated routing candidates.

> **Personal open-source project.** This repository also contains an ESP32-S3
> remote flashing probe design used during development, so it is not an empty
> example board. The workflow is mainly developed for Windows/WSL, Python 3.14,
> KiCad 10, and atopile. Please review and adapt design rules, parts and DRC
> results before manufacturing your own board.
>
> Project-authored code: [MIT](LICENSE). Upstream tools, CAD symbols, footprints
> and vendor models retain their own terms; see [third-party notes](docs/THIRD_PARTY.md).

The only default production path is:

```text
requirements -> electrical -> physical -> routing -> release
```

Electrical freeze is the immutable output of `electrical`, not a separate work
stage. Part selection is completed during `requirements`. Optional EDA,
alternate-router, experimental-backend and text-delegation integrations remain
available through the internal entrypoint registry, but do not enter normal
session context.

## Start a session

When this checkout hosts work on another PCB project, set the ignored
`.pcb-workspace.local.json` to `{"schema":1,"active_project":"../my-board"}`.
Session boot and `pcbctl` then use that project. Override with
`pcbctl.py --project PATH <command>`; use `--local` for this template's checks.
An invalid selection fails explicitly instead of falling back to the example.

Run existing project checks through `pcbctl.py verify project --plan`, then
`pcbctl.py verify project`. Checks with unchanged input and output hashes are
reused, including failures; no new checkpoint script is needed. See
[project check declarations and scope](docs/PROJECT_CHECKS.md).

Use Python 3.14 throughout this project:

```powershell
py -3.14 scripts/session_boot.py
```

The command writes `build/WORKSET.json` and emits one compact navigation
receipt. Read only the workset by default. Open a path from `read_if_needed`
only when the active action or a failure requires it. Never load a complete
`.kicad_pcb`, `.kicad_sch`, DSN or SES into model context.
Worksets keep bounded list previews with counts and truncation flags. In the
requirements stage, draft status and blocking unknowns are review blockers even
when the basic input structure passes; status does not replace the full gate.
Electrical contract validation and the freeze entrypoint independently apply the
same reviewed-requirements predicate. See [requirement evidence gates](docs/REQUIREMENT_GATES.md)
for cumulative scopes and explicit prototype test plans. A `reviewed` label or old passing receipt
cannot hide blocking unknowns or missing requirement structure. Freeze rechecks
the bound inputs before promotion and snapshots the requirements validator.

The Agent-facing command surface is intentionally limited to eight commands:

```powershell
py -3.14 scripts/pcbctl.py status
py -3.14 scripts/pcbctl.py build --plan
py -3.14 scripts/pcbctl.py build
py -3.14 scripts/pcbctl.py freeze
py -3.14 scripts/pcbctl.py physical-start
py -3.14 scripts/pcbctl.py place
py -3.14 scripts/pcbctl.py route
py -3.14 scripts/pcbctl.py verify
py -3.14 scripts/pcbctl.py release
```

These are thin dispatchers. Common physical and routing operations are grouped
under stable subcommands, for example `pcbctl.py place score`, `place
semantics`, `place render`, `place promote`, `route run` and `route escalate`.
An exact surviving electrical candidate blocks a duplicate build; use
`build --force-rebuild` only for an intentional compiler reproducibility check.
Build plans and successful build receipts state `validation_scope: modeled_components_only`
and include a bounded, informational snapshot of review states and declared unknowns.
This snapshot is not a completeness check or freeze authorization; live review metadata
does not change the compiler reuse fingerprint. Freeze retains its independent gates.
Advanced operations remain in `config/tool-entrypoints.json`; use them only
when no default subcommand expresses the reviewed operation.

## Create and configure a project

```powershell
py -3.14 scripts/init_project.py --new-project --project-name my-board --layers 4
py -3.14 scripts/check_pcb_environment.py --deep
py -3.14 scripts/record_toolchain_receipt.py --electrical-only
```

The initializer never installs or downloads tools. The production path expects
Git, Python 3.14, KiCad 10 and atopile. Konnect is a bounded live-KiCad helper;
it does not replace KRT and must not author the schematic or approve release.

Use `--new-project` in a separate project checkout. It archives inherited
requirements, decisions, selected parts, footprint provenance and adoption
pointers under `tmp/project-initialization/`, then creates unreviewed drafts.
Existing CAD and release artifacts remain on disk; a different project's freeze
cannot be adopted. Project-specific placement, routing and manufacturing inputs
still require review for the new design. Without `--new-project`, initialization
only reconfigures the current project and preserves its identity and progress.

Electrical builds audit every candidate footprint against local resources under
the configured backend `source_root`, including nested resource directories.
Pad numbering/geometry, source identity properties and footprint rule areas must
survive export before a build can pass or freeze. Compile-time library tables use
current resource paths; the existing physical board and its library table are
restored after compilation. Failed builds return a bounded cause and receipt path.
Physical-start resolves footprint libraries from the immutable freeze's source
snapshot and rejects stale physical tables before creating a candidate board.
For atopile 0.15.8's missing footprint-zone schema, the build adapter retains the
raw compiler PCB and produces a separate candidate containing the omitted source
rule areas. Recovery rejects partial/mismatched areas or other resource errors,
checks exact electrical/geometry parity, and still requires the independent
all-component integrity gate. It never repairs a routed physical board.
Cross-file consistency checks only the impedance channels explicitly declared in
`requirements.controlled_impedance` (use `{}` for a design without them). Stack
identity, target, optional exact geometry/layer sequence and calculator evidence
are compared with project data; no USB stack or trace width is built into the
validator.

Projects may declare `source_preflight` in the selected backend in
`config/electrical-backend.json`: a workspace `script` (Python), a non-empty
`inputs` list of exact workspace files, and optional `timeout_seconds` (1–60,
default 60). `build --plan`, `build --check` and builds run it before compiling
or reusing a candidate. The script receives `--output <temporary-json-path>`
and must exit zero with `{"schema":1,"success":true,"issues":[]}`. List imported
project helpers, rule manifests and reference documents explicitly; the source
tree is already hashed. The check must preserve source, backend config and all
declared inputs. Its bounded result and hashes stay in evidence; logs stay in
tmp. No preflight means no additional process. Manufacturer dimensions and
project rules belong in the project checker/data, outside generic engine code.
Freeze rejects missing or stale declared-check evidence and snapshots its input
files. `--new-project` archives and clears inherited preflight declarations;
ordinary reconfiguration preserves them. A passing check does not approve a
footprint, placement or fabrication beyond its declared scope.

Required preflight inputs must be durable workspace files and must pass Git
ignore checks; `tmp`, `build` and generated dependency directories cannot supply
them. Optional `config/source-references.json` (schema1, non-empty `files`) pins
each reference's workspace `path`, uppercase SHA-256 `sha256` and bounded `scope`.
Store reference snapshots under `references/`; preserve their origin, third-party
notices and existing review limits. Missing, altered or ignored inputs reject
before compilation. Freeze snapshots the manifest and its exact input files.
New-project initialization archives/removes the inherited manifest and preserves
the files as historical material. Default reconfiguration preserves both.

Build plans separate compiler inputs from validation inputs. `build_required`
reports whether a compiler invocation is needed; `validation_required` reports
whether the surviving output needs current gates. An unchanged source, entry,
driver, project initialization and resolved toolchain can supply its pinned raw
compiler PCB even when checks or reference paths changed. Run the suggested
`build` command to revalidate: it copies the raw result, reruns normalization,
parity and all-component integrity, binds fresh preflight/reference evidence and
retains the original receipt. A failed new gate stops there without restarting
the compiler. Schema1 receipts require a valid full input fingerprint and every
compiler dependency before they can supply this cache; incomplete proof requires
compilation. Schema2 stores separate compiler/full fingerprints. The common
driver argument protocol has an explicit version; changes to its compile
semantics must bump that version. `--force-rebuild` remains an explicit override.

## Template engine boundary

Reusable policy compilation and manufacturing gates live in the independently
versioned `template-engine/` Python package. The project owns only reviewed
inputs, board sources, evidence and artifact paths. `config/template-engine.json`
pins the engine API, version and source digest; `scripts/` contains thin project
adapters. The path source can later be replaced by an installed engine package
without changing the PCB command surface.

Run `py -3.14 scripts/check_template_engine.py` to verify the lock. An engine
source change fails closed until its version/digest is intentionally reviewed.

## Authority and gates

- Requirements and reviewed `.ato` files define electrical intent.
- Physical work starts only from an immutable passing electrical freeze.
- Any connectivity, identity, part, footprint or net-name change returns to
  atopile and creates a new freeze.
- Physical-start compares critical embedded footprints and rule areas with the
  reviewed project libraries before placement begins.
- Numeric fabrication, clearance, width and impedance values require reviewed
  evidence; the Agent may not invent them.
- Router success is candidate evidence, never release approval.
- Release requires exact-board DRC, connectivity, final electrical parity,
  visual review, DFM, package checks and the compiled manufacturing registry.
- A PCBA release requires DFM evidence recorded with the reviewed finish and
  solder-mask color, with both PCB DFM and SMT DFM executed and passing.
- `pcbctl release prepare-dfm` therefore creates an isolated, non-release
  Gerber + JLC BOM + JLC CPL candidate bound to one audited board SHA. This
  breaks the SMT-DFM dependency cycle without weakening final export gates.
- Release also compares reviewed critical embedded footprints with their pinned
  libraries; missing rule areas require explicit board-level compensation.
- Under-component holes whose Excellon class does not itself prove plugging or
  filling require a manufacturer/CAM confirmation before export.

Compile the shared reviewed policy before physical work when its inputs change:

```powershell
py -3.14 scripts/compile_design_policy.py
```

This also emits `manufacturing-registry.json`. Its Gerber layer-presence and
geometry rules come from `config/manufacturing-rules.json`; special-process
rules come from `config/manufacturing-profile.json`. All are bound to the same
design-policy SHA and are evaluated in `validate`, `pre_export` and `gerber`
phases. Engine code contains no component-specific manufacturing branches.
The external-DFM phase also rejects reports whose order parameters or executed
analysis modules differ from the reviewed order profile.

## Efficient physical workflow

Validation has three tiers, defined in `config/validation-tiers.json`:

- `quick`: isolated candidate integrity, DRC delta and connectivity. Render one
  cropped copper image only for critical, awkward or uncertain edits.
- `checkpoint`: full DRC/connectivity after a functional block. If footprints
  moved, rerun placement semantics and relative routability comparison. Review
  whole-board front/back copper, without 3D.
- `release`: full pipeline, exact-board placement semantics, four-view visual
  review, parity and manufacturing gates.

Before the full release tier, run `pcbctl.py verify readiness --board <adopted
board>`. It aggregates cached DRC, parity, physical review, DFM and special
process status in one compact receipt. `pcbctl.py release` fails immediately if
that receipt is missing, stale or blocked, avoiding repeated partial pipelines.
Current exact-board evidence is selected through `build/07-release/INPUTS.json`;
the index records failed receipts too and never implies approval.
When no `--board` is supplied, release resolves the hash-checked artifact in
`build/06-routing/CURRENT.json`; it never falls back to a mutable working board.

`pcbctl.py verify prepare-review` creates the exact DRC report/audit, bounded
footprint equivalence, placement semantics and four-view bundle in one run. It
also emits an intentionally incomplete assessment template. After a reviewer
inspects all four views and saves a separate assessment, `verify finalize-review`
validates that assessment and binds the visual, exact DRC and physical-review
receipts. The generated template itself is rejected as approval.

Manufacturing/CAM confirmations use `pcbctl.py release confirm-process` with an
allowlisted outcome and a real evidence file. The command records hashes,
updates the reviewed manufacturing profile atomically and recompiles policy;
it cannot substitute for PCB/SMT DFM.

Do not run placement semantics when footprints did not move. Do not run the
full pipeline or render four views after each trace.

At functional checkpoints, or after repeated candidate rejection, run the
metadata-only workflow profiler:

```powershell
py -3.14 scripts/pcbctl.py verify workflow
```

It reads compact receipts, run timestamps and file metadata only. It never
opens PCB/image/log contents, and reports candidate churn, repeated same-board
pipeline runs, slow operation families and retention pressure. Do not run it
after every trace. A fresh hash-bound checkpoint receipt should be reused for
handoff and diagnosis while its dependencies are unchanged; release gates are
always rerun after any bound dependency changes.

For an unchanged local checkpoint, explicit reuse is available:

```powershell
py -3.14 scripts/pcbctl.py verify --tier checkpoint --board <board> `
  --baseline-board <baseline> --reuse-checkpoint <passing-pipeline-summary.json>
```

Reuse fails closed unless board/baseline SHA, every project script and JSON
policy, template-engine source and the recorded toolchain receipt are unchanged.
It is forbidden for release and electrical builds.

When changing the template implementation itself, use `pcbctl verify template`.
It captures unit-test and static-check output under
`build/00-shared/template-checks/` and prints only one compact result. This is
developer regression evidence, not a PCB checkpoint or production gate.

Placement is judged by function before appearance. `placement-semantics.json`
protects decoupling, switch-mode power loops, connector-side ESD,
receiver-side termination, antenna keepouts and connector access.
Switch-mode input capacitors are judged as a complete VIN/GND current loop,
including plane-entry vias, not by one short pad distance.
`placement-routability.json` adds relative weighted airwire length, MST length,
crossings and critical-group comparison:

```powershell
py -3.14 scripts/check_kicad_placement_routability.py `
  --baseline <old.kicad_pcb> --board <candidate.kicad_pcb> `
  --require-improvement --output <receipt.json>
```

This metric is a placement screen, not proof of routability. Before committing
the floorplan, trial-route the critical USB, power and ground-return groups.

Route critical/high-risk paths first. Batch several related low-risk nets into
one KRT candidate and audit once per functional block. A route family is bound
to its placement, obstacle-copper fingerprint, nets, profile and allowed layers.
The routing task classifier sends short, unrouted two-pad connections to a
bounded explicit trace plan and reserves KRT for actual search or useful net
batches. Reviewed functional ordering is enforced; for example, connector
configuration and data fanout must not be deferred until after a power trunk
occupies the same escape corridor. Explicit trace plans reject approximate
existing-copper conflicts before creating a candidate, but KiCad DRC remains
the exact acceptance check.

Long KRT runs use a persistent checkpoint instead of keeping an Agent call open:

```powershell
py -3.14 scripts/pcbctl.py route start --run-id <id> <route arguments>
py -3.14 scripts/pcbctl.py route status --run-id <id>
py -3.14 scripts/pcbctl.py route finalize --run-id <id>
```

`status` only observes the existing process. `finalize` reads its compact
receipt and never launches or revalidates the route a second time.
Route and explicit-trace commands print only acceptance, failure-family and
audit indexes; use `pcbctl route report --path <krt-report.json>` for bounded
diagnostic detail. Full geometry and logs remain on disk.

Use KRT `--dry-run` as the routing work packet. It returns classification,
batch advice, route-area health and stopping conditions without artifacts.
After three consecutive rejected candidates in one route area, diagnose the
routing hypothesis before another attempt; this does not authorize placement.

Only no-path or incomplete-connectivity results count as structural failures.
Two such failures trigger a routing/corridor escalation, not an automatic
placement change. Footprints may move only after two distinct strategies fail
and the proposed placement improves routability, passes semantics and passes
critical route probes.

Every real KRT attempt therefore names a stable physical block and structural
strategy, for example `--route-area usb_input --strategy-class fcu-only`.
Changing the routed net subset does not create a new area, and numeric tuning
alone does not count as a distinct strategy. When two independent strategies fail structurally, create the mandatory
receipt before returning to physical work:

```powershell
py -3.14 scripts/assess_routing_escalation.py `
  --scope usb_input --report <attempt-a-krt-report.json> `
  --report <attempt-b-krt-report.json> --output <escalation.json>
```

The receipt may make placement review eligible, but always records
`placement_change_authorized=false`. A routing-to-physical handoff requires
that receipt. Promotion of the resulting candidate additionally requires an
unchanged board outline, an improved routability comparison and passing probe
reports for every group in `placement-routability.json`.

`--route-bbox-mm X0 Y0 X1 Y1` may bind obstacle identity to an explicitly
reviewed local corridor. If omitted, the whole-board copper fingerprint is used
conservatively; the template never guesses a local box.

## Visual review

For a bounded local copper edit:

```powershell
py -3.14 scripts/render_kicad_visual_bundle.py --board <board> `
  --tier local --trigger critical_net_changed --layer F.Cu `
  --bbox-mm <x0> <y0> <x1> <y1>
```

The local assessment is only `accept`, `awkward` or `unsafe`. Checkpoint views
use `--tier checkpoint`; release uses `--tier release`. Visual judgment is
complementary evidence and never overrides DRC, connectivity, return-path or
manufacturing gates.

## Artifacts and retention

Each internal artifact stage has `tmp`, `evidence` and `release` lanes. Large
logs and candidate boards belong in `tmp`; compact decisions and hashes belong
in `evidence`; immutable passing outputs belong in `release`. Start discovery
at `build/STAGE_RESULTS.json`, then follow the selected stage's `CURRENT.json`;
an accepted board must never be referenced only from `tmp`.

```powershell
py -3.14 scripts/artifact_store.py inventory
py -3.14 scripts/artifact_store.py prune
py -3.14 scripts/artifact_store.py checkpoint-prune
```

Both prune commands are previews unless `--apply` is explicitly supplied.
Applied pruning creates and verifies a recoverable zip before removing the
selected run directories, and never archives a release or active reference.
Reference protection includes stage/global CURRENT pointers, release INPUTS,
DFM work-packet pointers, asynchronous routing state and manufacturing
manifests. This protection is checked before an archive is created.

## Optional capabilities

EasyEDA Pro remains useful for explicit compatibility/JLC review. FreeRouting
remains an uninstalled lab-only leftover-net comparison. Experimental electrical backends
also remain available. They are not part of
the default stage capabilities, are never electrical or physical authorities,
and cannot relax release gates.

Manufacturing cost is reviewed in two layers. The project-owned cost drivers in
`config/manufacturing-profile.json` explain every selected surcharge-risk
process and its lower-cost alternative. After uploading the package, copy the
live quote into a compact JSON observation and run `pcbctl release review-quote`;
unexpected options such as an unrequested four-wire resistance test fail before
ordering. Prices are never embedded in the template because vendor pricing is volatile.

Key paths:

- `src/`, `ato.yaml`: electrical authority;
- `elec/layout/`: authoritative KiCad physical boards after freeze;
- `config/`: reviewed policies and selected evidence;
- `scripts/`: deterministic guarded operations;
- `template-engine/`: independently versioned project-neutral engine package;
- `config/template-engine.json`: project-to-engine version and source lock;
- `config/manufacturing-rules.json`: reviewed project Gerber rule source;
- `docs/MANUFACTURING_COST_REVIEW.md`: stable cost-driver and live-quote review;
- `docs/MANUFACTURING_EXTERNAL_REVIEW.md`: one SHA-bound DFM/quote work packet,
  structured external-result import, and order-time evidence rules;
- `handoff/stages/CURRENT_STAGE.json`: stage state;
- `build/WORKSET.json`: compact current-session context;
- `build/STAGE_RESULTS.json`: one compact index of accepted stage outputs;
- `build/<stage>/CURRENT.json`: pointer to that stage's adopted immutable artifact;
- `build/*/{tmp,evidence,release}`: artifact lanes;
- `manufacturing/`: intentional release exports.
