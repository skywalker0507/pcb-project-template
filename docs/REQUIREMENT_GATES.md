# Requirement evidence gates

`config/requirements.json` remains the reviewed requirement authority. Every
unfinished structured unknown has a unique `id` (legacy `item` is accepted) and
an explicit `blocks` list. Empty lists are deliberate nonblocking notes; missing,
null, false, scalar or misspelled scopes are invalid. Legacy strings block all
gates. No field automatically marks an unknown resolved.

| Scope | First required evidence | Also blocks |
| --- | --- | --- |
| `electrical_freeze` | Independent source, part, pin and electrical review | Physical work and every export |
| `physical_start` | Reviewed physical constraints and manufacturing inputs | Physical work and every export |
| `release` | Exact-board layout, assembly and manufacturing acceptance | Every export |
| `production_release` | Final hardware and product acceptance | Production export |

Scopes accumulate. Navigation, electrical contract, freeze, physical-start and
compiled release policy use `pcb_template_engine.requirements.problems`. The
validator accepts `--gate`; its default is `electrical_freeze`. Draft validation
checks input structure without claiming review. Real operations additionally
require `status: reviewed`.

Release defaults to `production`. An explicit `release_target: prototype`
permits manufacturing test boards before hardware acceptance. It requires an
embedded `prototype_validation` object with nonempty `purpose`, `restrictions`
and a `tests` list. Each test has an `unknown_id` and a nonempty `procedure`;
the set must match all pending `production_release` unknowns exactly. Review of
this plan is part of requirements review. A sample does not approve normal
operation, field use or production. Service permissions require actual evidence.

Prototype export retains full DRC, connectivity, parity, placement, visual,
manufacturing and DFM gates. It defers only obligations explicitly assigned to
production acceptance. All unknowns and the test plan remain SHA-bound in the
resolved policy. Export atomically includes `release-scope.json`; snapshots
reject a scope that differs from the exact board and current policy, and carry
the target and `production_ready` flag. Prototype packages always report
`production_ready: false`. Gerber file scope still comes from the manufacturing
registry. A target, test plan or unknown change makes previous policy and
readiness evidence stale; it does not change electrical connectivity authority.

Split mixed obligations before moving them to later gates. Retain their original
identity, description, previous scopes and mapping to the replacement records in
a project-owned review. For example, source fault limits belong before freeze;
prototype transient measurements belong in hardware acceptance. Neither a
calculation nor a changed scope constitutes a passing measurement.
