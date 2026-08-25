# Template engine and project boundary

The reusable engine is a standalone Python package rooted at
`template-engine/`. It has its own package metadata and semantic version. It
does not discover the PCB project, import project scripts, or contain project
names, component references, board paths, manufacturer names or reviewed
values.

The PCB project owns:

- `src/`, `elec/` and the immutable electrical/physical artifacts;
- all reviewed files under `config/`;
- evidence, handoffs, manufacturing outputs and compact receipts;
- thin compatibility/orchestration adapters under `scripts/`.

`config/template-engine.json` is the dependency lock. In this checkout it uses
a content-addressed path source so changes to engine and project adapters can be
reviewed atomically. The contract also supports an installed package; extracting
`template-engine/` into its own Git repository therefore requires only
publishing/installing the pinned version and changing `source.type` from `path`
to `installed`. Project command names and policy schemas remain unchanged.

`scripts/check_template_engine.py` verifies version, API, source digest and the
absence of the current project/manufacturer identity from distributable engine
sources. A mismatch fails closed before release work.

Manufacturing rules follow one direction:

```text
project reviewed inputs
  -> design-policy-resolved.json
  -> manufacturing-registry.json
  -> validate / external_dfm / pre_export / gerber receipts
  -> package check and immutable release snapshot
```

`config/manufacturing-rules.json` owns Gerber file and geometry rules.
`config/manufacturing-profile.json` owns manufacturer selection, reviewed
routing values and special-process evidence. The registry expands `all_copper`
against the active board stack and binds every rule to the design-policy SHA.
Package checks consume the registry result instead of maintaining another list
of copper layers, outline files, drills or keepouts.
