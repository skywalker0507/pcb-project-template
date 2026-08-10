# EasyEDA Pro bridge contract

This migration defines the boundary but does not implement a KiCad/EasyEDA
converter. The intended adapter is the official EasyEDA API Skill/Bridge and
KiRouting Integration.

## Import boundary

The bridge consumes only a passing immutable `electrical_freeze` bundle and its
`frozen.kicad_pcb`. A freeze bundle lives under
`build/04-electrical-freeze/release/<freeze-id>/`; tmp candidates are never
physical inputs. The bridge must emit compact, normalized schema-2 manifests:

- source: deterministic extraction from the frozen KiCad board;
- imported: deterministic extraction from the EasyEDA Pro document after import.

Each manifest declares `normalization.length_unit=nm` and
`normalization.rotation_unit=microdegree`, and contains `board_sha256`,
`components`, `nets`, `board_outline`, and `holes`. Physical quantities are
integers, never floats. Components contain `reference`, `footprint`,
`part_identity`, `side`, `rotation_udeg`, and `pads`. Pads contain logical
`number` (empty is valid for NPTH), `net`, `drill_nm`, `pad_type`, and a normalized
`geometry_signature`. Repeated logical pad numbers are valid and compared as
multisets; adapters must not use backend-private UUIDs as cross-format identity.

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

After placement/routing, `scripts/check_final_electrical_parity.py` compares the
final manifest against the bundle's immutable `frozen-electrical-manifest.json`.
It ignores position, side, rotation, outline, holes, and copper geometry while
strictly checking references, exact part identities, footprints, logical pad
multisets, nets, and pad-to-net topology.

`scripts/check_manufacturing_gates.py` binds import parity, final electrical
parity, EasyEDA DRC, and JLC DFM to the immutable freeze and final board.
The existing KiCad CLI audit, connectivity checks, physical review, BOM/PnP/
Gerber audit, export snapshot, and `release_pass=true` requirements remain
independent mandatory gates.

The bridge must do bulk data processing outside LLM context. MCP/API calls are
for high-level import, placement, local edits, and receipt production—not
object-by-object copying or full-board comparison by an agent.
