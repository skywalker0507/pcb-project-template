# Project context and repeatable checks

The eight top-level commands are unchanged. These adapters consolidate project
work; they do not create another production path or approve a PCB.

## Select the working project

An ignored `.pcb-workspace.local.json` in the checkout can contain:

```json
{"schema": 1, "active_project": "../my-board"}
```

`session_boot.py` mirrors the selected project's compact workset, including its
absolute root. `pcbctl.py` dispatches there. Context flags precede the command:
`--project PATH` overrides the local selection and `--local` bypasses it.
Delegation is explicit and does not follow another project's selection. A
missing/invalid target is an error. Initialization archives and clears inherited
selection and check declarations; existing handoffs are not used as selectors.

## Declare existing checks once

`config/project-checks.json` has schema 1 and a `checks` array. The template's
array is empty. A project can add an existing Python checker:

```json
{
  "schema": 1,
  "checks": [{
    "id": "power",
    "script": "project_checks/check_power.py",
    "runtime": "python",
    "args": ["--output", "{output}"],
    "inputs": ["src/power/*.ato", "config/power.json"],
    "pass_field": "calculation_checks_pass"
  }]
}
```

Use `runtime: kicad` for native `pcbnew` checks; its Python executable comes from
the configured KiCad CLI directory. Available argument tokens are `{output}`,
`{evidence}`, `{work}`, and `{build_receipt}`. The last binds a verified passing
electrical build, source, board and component-integrity receipt. `build` writes
its latest passing receipt pointer to `build/03-electrical/BUILD.json`; an
explicit `--build-receipt` may be supplied for migration. This is not a freeze
or an adopted physical board.

Declare the complete source, helper, contract and vendor input set. Inputs must
be durable project files; glob patterns must match. The runner always hashes
the checker and interpreter. Its cache is only as granular as these declared
dependencies; use bounded inputs rather than an entire workspace. Do not use
an undeclared external file or a historical report as a hidden dependency.

```powershell
py -3.14 scripts/pcbctl.py verify project --plan
py -3.14 scripts/pcbctl.py verify project
py -3.14 scripts/pcbctl.py verify project --only power
```

`--plan` executes no checker. Unchanged checks create no new artifact run.
Changing a producer or input invalidates its cached result. Both the check
receipt and every generated evidence file must match recorded hashes before
reuse. Failed checks remain failed and cached until inputs change; retry an
external tool failure explicitly with `--refresh --only ID`. A failure never
falls back to an older pass. A zero exit without passing JSON is a failure.
Mutation of inputs or bound electrical artifacts rejects the result and stops
the batch. Full logs and fault candidates stay in `tmp`; receipts stay in
`evidence`. No commands run through a shell.

One aggregate result lives at
`build/03-electrical/project-checks/current.json`. Status displays it as the
**last batch**, with freshness explicitly unchecked; use `--plan` to assess
current inputs. A subset names its selected ids and cannot stand for all
configured checks. Do not mirror this into hand-edited draft-status, ledger and
handoff files after each small check. Write a handoff at a meaningful milestone.

## Proof and stage boundaries

A checker pass means only its declared scope passed. A calculation/sensitivity
pass is not a measured electrical, thermal or service result. Requirement
review, immutable freeze, placement/DRC/connectivity and manufacturing gates
remain independently enforced. Caching never resolves an unknown or changes
an approval field. Define bounds and acceptance procedures before freeze;
layout-dependent copper and prototype measurements belong in their recorded
stages, with the existing cumulative gates preserved.

Requirements navigation distinguishes malformed input, outstanding review and
ready-to-build input. It does not repeatedly prescribe a known failing
`--require-reviewed` invocation as the next action.

`build --plan` now saves `build/03-electrical/build-plan.json` automatically;
use `--report build/.../plan.json` for a different JSON path. `--output` is an
actual-build directory and is rejected in plan mode.
