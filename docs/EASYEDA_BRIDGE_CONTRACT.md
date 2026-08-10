# EasyEDA Pro bridge contract

This migration defines the boundary but does not implement a KiCad/EasyEDA
converter. The intended adapter is the official EasyEDA API Skill/Bridge and
KiRouting Integration.

## Import boundary

The bridge consumes only a passing `electrical_freeze` receipt and its exact
`.kicad_pcb` candidate. It must emit two compact, normalized schema-1 manifests:

- source: deterministic extraction from the frozen KiCad board;
- imported: deterministic extraction from the EasyEDA Pro document after import.

Each manifest contains `board_sha256`, `components`, `nets`, `board_outline`,
and `holes`. Each component contains `reference`, `footprint`, `side`,
`rotation`, and `pads`; each pad contains `number`, `net`, and `drill`.
Adapters must sort and normalize geometry and units before writing a manifest.

`scripts/check_easyeda_import_parity.py` compares component/reference counts,
footprints, pad counts/numbers, nets, pad-to-net mapping, outline, holes/drills,
side, and rotation. Connectivity and pad-net differences are strict failures.
The validator reads manifests, not complete PCB files, and records their hashes.

## Routing and validation boundary

KRT remains a GUI-independent routing engine. The EasyEDA KiRouting adapter may
export `.kicad_pcb`, run the guarded KRT candidate flow, and apply only a
deterministic track/via diff back to EasyEDA. A router success is not acceptance.

Before a new export, the external integration must provide compact receipts:

- `physical_drc`: `backend=easyeda_pro`, `drc_pass=true`, final interchange
  `board_sha256`;
- `manufacturing_dfm`: `backend=jlcpcb`, `dfm_pass=true`, the same
  `board_sha256`;
- import parity from the frozen source board.

`scripts/check_manufacturing_gates.py` binds these to the freeze and final board.
The existing KiCad CLI audit, connectivity checks, physical review, BOM/PnP/
Gerber audit, export snapshot, and `release_pass=true` requirements remain
independent mandatory gates.

The bridge must do bulk data processing outside LLM context. MCP/API calls are
for high-level import, placement, local edits, and receipt production—not
object-by-object copying or full-board comparison by an agent.
