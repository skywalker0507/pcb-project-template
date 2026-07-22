# Konnect integration boundary

Konnect is the default live KiCad MCP backend and replaces the older
KiCAD-MCP-Server integration. It does not replace KiCadRoutingTools (KRT).

## Why it replaces KiCAD-MCP-Server

Konnect is that project's Rust successor. It uses KiCad 10's supported IPC API
instead of deprecated SWIG Python bindings, ships as a single binary, integrates
edits with KiCad undo/redo, and exposes toolsets on demand to reduce the
always-loaded MCP schema context.

## Capability split

| Need | Preferred tool | Reason |
|---|---|---|
| Move/rotate/place footprints while KiCad is open | Konnect | Native KiCad 10 IPC with undo/redo |
| Add or inspect explicit tracks and vias | Konnect | Small, direct MCP operations |
| Generate a bounded batch-routing candidate | KRT | Scriptable router with candidate-file isolation |
| Whole-board fallback routing | Project FreeRouting wrapper | Headless, logged and audited |
| DRC and fabrication approval | Deterministic project scripts | Stable receipts and reviewed baselines |

Konnect's `route_trace` and `route_pad_to_pad` tools create caller-specified
geometry; they are not an obstacle-aware batch router. Its registered
`autoroute` tool currently returns an unavailable error because KiCad 10 no
longer exposes the required DSN/SES round trip through `kicad-cli`.

## Project policy

- atopile is the schematic and electrical-design authority; do not use
  Konnect to edit generated schematics or instantiate reference circuits;
- use Konnect only with the intended board open in KiCad and the KiCad API
  enabled;
- pass `KICAD_API_SOCKET` to Codex before it starts;
- save to a candidate or commit a recoverable checkpoint before broad edits;
- run `python scripts/run_pcb_pipeline.py` after live edits;
- DRC success does not waive return-path, impedance, mechanical or fabrication
  review.

The generated MCP command uses `scripts/start_konnect_mcp.py`. On a new PC it
creates only Konnect's `~/.konnect/.installed` marker, preventing Konnect's
implicit first-launch installer from writing Claude skills, agents and hooks.
This does not modify Konnect source. An intentional `konnect init` still
installs its Claude integration later if wanted.

## Installation

```text
python scripts/init_project.py --project-name my-board
python scripts/check_pcb_environment.py --deep
```

Use `--no-konnect` only when Konnect should not be configured. Install the
binary manually before recording the toolchain receipt. Restart Codex
after initialization. Konnect is AGPL-3.0; businesses that cannot
meet that license's obligations should obtain a commercial license or leave
the option disabled.
