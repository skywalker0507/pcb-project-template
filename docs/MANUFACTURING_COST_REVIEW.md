# Manufacturing cost review

Cost is a pre-order engineering gate, not a fixed capability table. Vendor
prices and promotions change, so the template stores stable decisions and maps
each live quote line to those decisions.

## Workflow

1. During requirements and physical planning, review every process in
   `config/manufacturing-profile.json:cost_review.drivers`.
2. Before release, run `py -3.14 scripts/pcbctl.py release check-cost-policy`.
   A selected surcharge-risk process must state why it is required or preferred,
   cite evidence, and name a lower-cost alternative when one exists.
3. Generate one DFM work packet with `pcbctl release prepare-dfm`. Upload only
   the Gerber/BOM/CPL under its `upload/` directory. The manifest binds the
   board and all three upload files by SHA-256.
4. After obtaining a live quote, copy both the fee label and the selected
   option value into the schema-2 compact observation
   shown in `docs/examples/manufacturing-quote-observation.example.json`.
   Copy artifact hashes and order parameters from the same work-packet manifest.
5. Run:

   ```powershell
   py -3.14 scripts/pcbctl.py release review-quote `
     --quote path/to/quote-observation.json `
     --work-packet build/07-release/tmp/<run>/dfm-work-packet.json `
     --output build/07-release/evidence/<run>/manufacturing-quote-review.json
   ```

6. Remove or explain every unexpected line. Set `approval.status` to `accepted`
   only after the reviewed total and process selections match the intended order.

The quote gate deliberately does not infer whether a price is good. It answers
the safer question: "Did every charged process come from a reviewed design
decision?"

Quotes older than 48 hours, lacking a timezone, using different production
files, or changing the reviewed finish/mask/assembly profile are rejected. The
official API adapter will eventually populate the same observation schema; it
does not change the downstream gate.

## Current project cost-down order

- Deselect four-wire low-resistance testing: no Kelvin/milliohm acceptance
  requirement exists.
- Identify and remove the opaque `特价` line unless the vendor explains its
  triggering process.
- Return via copper deposition to the standard option unless the vendor shows
  that the released board requires the non-standard selection.
- For the next board revision, evaluate 0.30 mm drills and move vias away from
  paste-bearing pads before routing is frozen. Those geometry changes can remove
  both the small-hole and filled-via cost drivers.
- Keep paid impedance control for this released revision because the reviewed
  USB geometry is bound to the selected stack. A cost-down waiver requires a
  separate SI/risk review, not an order-page checkbox change.
- ENIG and black solder mask are preferences rather than core electrical needs.
  They may be changed only through a reviewed order-profile update followed by
  DFM using the actual finish and mask capability.

Primary vendor references:

- JLCPCB via-covering guidance: https://jlcpcb.com/help/article/pcb-via-covering
- JLCPCB surface-finish comparison: https://jlcpcb.com/help/article/jlcpcb-surface-finish
- JLCPCB impedance calculator guide: https://jlcpcb.com/help/article/user-guide-to-the-jlcpcb-impedance-calculator
