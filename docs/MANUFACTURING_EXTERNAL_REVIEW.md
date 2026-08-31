# External manufacturing review

The production files, external DFM result and live quote must describe the same
board. Screenshots and prose are review aids; SHA-bound structured receipts are
the gate inputs.

## 1. Prepare one work packet

Run `pcbctl release prepare-dfm` with the adopted board and its exact passing
audit. The command creates:

```text
<run>/
  upload/
    candidate-gerber.zip
    BOM_JLCPCB.csv
    CPL_JLCPCB.csv
  source/review-board.kicad_pcb
  evidence/candidate-audit.json
  dfm-work-packet.json
  dfm-observation.json
  quote-observation.json
```

Upload only the three files under `upload/`. Never mix a BOM or CPL from a
different run. `build/07-release/tmp/jlc-dfm-work-packet-CURRENT.json` points to
the latest packet without making it a released artifact.

## 2. Record the external DFM result

The vendor PDF remains opaque evidence. A Web AI or operator may summarize it
by completing `dfm-observation.json`, but the record must include both PCB and
SMT module results, every accepted exception, and any unresolved or blocking
finding. Set `review.status=confirmed` and name the reviewer only after the
complete report has been checked.

```powershell
py -3.14 scripts/pcbctl.py release import-dfm-result `
  --work-packet <run>/dfm-work-packet.json `
  --report path/to/vendor-report.pdf `
  --observation <run>/dfm-observation.json `
  --output build/07-release/evidence/<review>/manufacturing-dfm.json
```

The importer rejects changed files, changed order parameters, unconfirmed
reviews, malformed exceptions, and remaining blocking/unresolved findings. It
does not parse or trust the PDF automatically.

## 3. Review a live quote

Complete the pre-bound `quote-observation.json`. Record a timezone-aware
timestamp, quantity, complete line-item breakdown and total. Classify each line
as `fabrication_base`, `assembly_base`, `components`, `surcharge`, `discount`,
`shipping`, `tax`, or `other`. Every surcharge or other line must map to a
reviewed cost driver; the line-item sum must reconcile to the total.

```powershell
py -3.14 scripts/pcbctl.py release review-quote `
  --work-packet <run>/dfm-work-packet.json `
  --quote <run>/quote-observation.json `
  --output build/07-release/evidence/<quote>/manufacturing-quote-review.json
```

The default freshness window is 48 hours. The future JLC Open Platform adapter
will populate the same observation instead of introducing a second gate model.
