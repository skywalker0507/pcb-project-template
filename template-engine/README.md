# PCB Template Engine

This directory is an independently versioned Python package. It contains only
project-independent compilation and deterministic gate logic. Project names,
component references, board paths, manufacturing choices and reviewed values
belong in the consuming project's JSON inputs.

The project binds the engine through `config/template-engine.json`. The current
checkout uses a pinned path source so development stays atomic; the same
contract accepts an installed distribution when this directory is extracted to
its own Git repository. Compatibility wrappers under `scripts/` preserve the
project command surface and pass explicit project paths into this package.

Release orchestration, current-evidence pointers and readiness caching remain
in the consuming project adapter because they bind project boards and artifact
locations. Reusable policy compilation and manufacturing-rule evaluation remain
here. This boundary keeps the engine project-independent while still allowing
the project to aggregate blockers before expensive tool execution.
