# Tool-Neutral Hardware Specification — Rev A.1

## 1. Scope

Implement the reviewed Rev A.1 ESP32-S3 remote flashing probe described here.
The first reviewed atopile migration must preserve the reviewed topology, MPNs,
connector pin order and supported 3.0–3.6 V target range.

Authoritative component datasheets and stated electrical/safety requirements take
precedence over migration preservation. If a preserved topology, MPN, symbol pin
mapping or footprint pad mapping conflicts with an authoritative datasheet or a
stated product operating requirement, do not silently preserve or optimize it:
report the conflict and require a reviewed specification change.

Rev A.1 is initially for STM32 targets. The ESP32-S3 connects to Wi-Fi and runs
as a standalone remote programmer: no PC is required while programming a
target. Firmware is outside this PCB specification, but the pin assignments
and J2/J3 target-interface contract are hardware constraints. Future target
families require a separately reviewed interface extension. Product decisions
confirmed for this revision are recorded in `config/product-decisions.json`.

The target board powers itself. The probe has no target-power output. VTREF is
used only by the ADC divider, BOOT0 source and target-powered safety supervisor.

Rev A.1 is a USB-default-power design. It does not implement USB PD or Type-C
current-advertisement detection and must keep its worst-case 5 V input current
at or below 500 mA. The selected N16R8 module limits the product operating
ambient range to -20 °C through +60 °C, leaving margin to the module's +65 °C
rating.

## 2. Functional blocks

Keep these as separate reusable modules:

1. ESP32-S3 module and reset/boot support.
2. USB-C receptacle, ESD and input protection.
3. 5 V to 3.3 V AP63203 buck supply.
4. TMUX1574 target-signal isolation.
5. VTREF hardware enable interlock.
6. NRST and BOOT0 recovery control.
7. Two alternate target connectors with dedicated ESD.
8. LEDs, buttons and expansion header.

## 3. ESP32-S3

Use `ESP32-S3-WROOM-1-N16R8`.

- C1 100 nF and C2 22 µF from 3V3 to GND near the module. C2 uses the same
  reviewed 25 V X5R 1206 MLCC as the buck bulk capacitors to retain useful
  capacitance under 3.3 V DC bias and reduce unique BOM lines.
- R1 10 kΩ from 3V3 to EN.
- C3 1 µF from EN to GND.
- SW1 RESET button from EN to GND, using top-actuated `SKRPANE010`.
- R18 10 kΩ from 3V3 to GPIO0.
- SW2 BOOT/provision button from GPIO0 to GND, using top-actuated
  `SKRPANE010`. For both switches, pins 1/2 are the signal-side common and pins
  3/4 are the GND-side common, matching the official SKRP circuit diagram.
- GPIO0 is a strapping pin intentionally reserved for BOOT/provision use; do not
  assign it another product function.
- GPIO assignment must exactly match `design-data.json`.
- GPIO3, GPIO45 and GPIO46 are reserved strapping pins and are not available
  for normal product functions in this revision.
- GPIO35, GPIO36 and GPIO37 are unavailable because the selected N16R8 module
  uses 8 MB Octal SPI PSRAM. `design-data.json` must not assign them.
- Module GND1, GND2 and EPAD all connect to GND.

Place the module at the top board edge with the PCB antenna section protruding
beyond the base-board edge. Apply the complete manufacturer land pattern and
antenna keepout to every copper layer, components, fasteners, cables and
enclosure metal. The keepout geometry is taken from the current Espressif
module data sheet and must be captured in footprint provenance before placement.

## 4. USB-C and input protection

Use `USB4105-GF-A` in USB 2.0 device/sink configuration.

- Tie all four VBUS contacts to `USB_VBUS_RAW`.
- Tie all four GND contacts to GND.
- CC1 and CC2 each have an independent 5.1 kΩ pull-down to GND.
- SBU1 and SBU2 are not connected.
- A6/B6 are the D+ reversible contacts.
- A7/B7 are the D- reversible contacts.
- U2 is `USBLC6-2P6`, a two-line low-capacitance USB ESD protector in the
  smaller SOT-666 package. A6/B6
  merge as D+ and A7/B7 merge as D- in a short connector-side fan-out before
  U2. Its verified flow-through channels are pins 6 to 1 (I/O1) and pins 4 to
  3 (I/O2), with pin 2 = GND and pin 5 = VBUS. ST specifies matched D+/D-
  behavior rather than a fixed polarity per channel, so Rev A.1 assigns D+ to
  I/O1 and D- to I/O2 to preserve pair order through the turn toward U1. The two physical
  sides use distinct PCB nets so KiCad does not require an external copper
  bypass around U2: `USB_DM_RAW`/`USB_DP_RAW` before U2 and
  `USB_DM_ESD`/`USB_DP_ESD` after U2.
- U2 pin 5 VBUS connects to connector-side `USB_VBUS_RAW`, before F1. Keep this
  VBUS connection, both data connections and the U2 ground return short.
- R5 22 Ω is D- and terminates at GPIO19.
- R4 22 Ω is D+ and terminates at GPIO20.
- R4/R5 are placed near the ESP32 pads.
- J1 shield connects through R6 0 Ω to GND. Keep R6 as an explicit,
  replaceable shield strategy.
- F1 `1206L075/16WR` is between raw USB VBUS and protected `USB_5V`.
- D1 `SMBJ5.0A`: cathode to `USB_5V`, anode to GND.

USB routing requirements:

- 90 Ω differential impedance using the exact JLC04161H-3313A stack recorded in
  `config/jlcpcb-impedance-evidence.json`.
- F.Cu over continuous In1 GND.
- Board geometry and any fabrication compensation come only from
  `config/jlcpcb-impedance-evidence.json`; do not duplicate approximate width or
  gap ranges in this specification.
- No layer changes after U2. The J1-to-U2 reversible-contact fan-out may use a
  short, reviewed F.Cu/B.Cu crossover only when needed to join A6/B6 and
  A7/B7; each signal-layer transition needs nearby GND return vias. This
  bounded connector-side exception does not apply to the U2-to-U1 USB pair.
- The J1-to-U2 RAW-net escape may use the reviewed 0.20 mm trace / 0.15 mm
  clearance fine-pitch exception only on `USB_DP_RAW` and `USB_DM_RAW`.
  After U2, short 0.20 mm F.Cu endpoint escapes may transition into the
  manufacturer-calculated 0.290576 mm / 0.2032 mm protected differential
  corridor. The long corridor remains F.Cu-only over continuous In1.Cu GND;
  the fine-pitch clearance exception never propagates past U2.
- Matched topology and minimal skew.
- U2 stays near J1; R4/R5 stay near U1.

The exact J1 land pattern contains two 0.65 mm locating holes, 16 SMT contacts
and four plated shield slots. Do not replace it with a similar USB-C package.

## 5. 5 V to 3.3 V power

Use fixed-output `AP63203WU-7`:

- pin 1 FB to regulated 3V3 output;
- pin 2 EN to protected USB 5 V;
- pin 3 VIN to protected USB 5 V;
- pin 4 GND to GND;
- pin 5 SW to L1 input and C5 bootstrap return;
- pin 6 BST to C5 bootstrap input.

Required components:

- C4: 22 µF, 25 V X5R, 1206 from USB_5V to GND, immediately beside VIN/GND.
- C5: 100 nF from BST to SW, immediately beside pins 6/5.
- L1: `XGL4030-472MEC`, 4.7 µH shielded power inductor, adjacent to SW. Its
  documented 20%-drop saturation current and 40°C-rise current must retain
  margin over the AP63203 peak-current design case. The 4.7 µH value is an
  intentional reviewed choice; do not substitute another value solely to match
  a datasheet reference circuit.
- C6/C7: two 22 µF, 25 V X5R, 1206 capacitors from 3V3 to GND beside L1
  output.

The converter's 2 A rating is component capability, not the product load
budget. Rev A.1 reserves a 500 mA-capable 3V3 supply for the ESP32-S3 module and
at most 25 mA for J4 and the remaining low-power loads. The total worst-case USB
5 V input current, including conversion loss and startup behavior, must remain
at or below 500 mA. C2/C4/C6/C7 use Samsung `CL31A226KAHNNNE`. The official
Samsung characteristic data records typical capacitance change of about
-20.5% at 3.3 V and -38.0% at 5 V for the part family; these curves are design
reference data rather than guaranteed minima. Prototype validation must still
cover startup, load transient and converter temperature.

The VIN–SW–L1–COUT–GND switching loop must be compact. Keep SW copper small.
Do not route USB, antenna, VTREF, SWD or UART through the switch region. Use
appropriate power widths and short local ground returns; generic autorouting is
not acceptable for this block.

## 6. Target signal isolation

Use `TMUX1574PWR`, exact TI PW/TSSOP-16.

- VDD to probe 3V3 with C8 100 nF local decoupling.
- GND to GND.
- SEL to GND, selecting all A inputs.
- S1B, S2B, S3B and S4B to GND.
- `/EN` net name: `TARGET_EN_N`.
- R7 10 kΩ from `/EN` to probe 3V3, so default is disabled.

Channel mapping is defined in `design-data.json`. SWDIO must remain genuinely
bidirectional. Do not use a fixed-direction level translator.

The TMUX is not a voltage translator. This product supports only targets whose
VTREF remains between 3.0 and 3.6 V. Probe GND and target GND are directly
common; TMUX1574 provides signal-path disconnect and powered-off protection,
not galvanic isolation.

## 7. Hardware VTREF interlock

Firmware GPIO9 must not be able to enable the TMUX unless target VTREF exceeds
the supervisor threshold.

- Q4: `2N7002LT1G`.
  - physical pin 1 gate to GPIO9;
  - R21 100 kΩ gate-to-GND;
  - physical pin 3 drain to `TARGET_EN_N`;
  - physical pin 2 source to Q5 drain.
- Q5: `AO3400A`.
  - physical pin 3 drain to Q4 source;
  - physical pin 2 source to GND;
  - physical pin 1 gate to U5 `/RESET`.
- U5: `TLV803EA26RDBZR`, exact R-pinout SOT-23/DBZ variant.
  - physical pin 1 `/RESET`, open-drain, to Q5 gate;
  - physical pin 2 GND to GND;
  - physical pin 3 VDD powered only by target VTREF;
  - R22 100 kΩ from Q5 gate to VTREF;
  - C10 100 nF ceramic from U5 VDD/VTREF to GND, placed immediately beside U5.

U5 is the 2.64 V threshold, approximately 200 ms release-delay variant. With
target absent or undervoltage, Q5 is off and Q4 cannot pull TMUX `/EN` low.

Firmware must still require ADC-confirmed VTREF 3.0–3.6 V with hysteresis and
debounce, and immediately release GPIO9 on invalid VTREF. The hardware
supervisor gates only the TMUX-carried SWD/UART paths. NRST and BOOT0 are
separate safe-default controls: they are not hardware-gated by U5 and firmware
must not assert them until VTREF has been validated.

## 8. NRST

Use Q1 `2N7002LT1G`:

- physical pin 3 drain to target NRST;
- physical pin 2 source to GND;
- physical pin 1 gate to GPIO4;
- R12 100 kΩ gate-to-GND.

GPIO4 high asserts reset. GPIO4 low releases reset. NRST must never be actively
driven high from probe 3V3.

## 9. BOOT0

Use Q2 `AO3401A` PMOS:

- source, physical pin 2, to target VTREF;
- drain, physical pin 3, to target BOOT0;
- gate, physical pin 1, to Q3 drain;
- R13 100 kΩ from Q2 gate to VTREF.

Use Q3 `2N7002LT1G`:

- physical pin 3 drain to Q2 gate;
- physical pin 2 source to GND;
- physical pin 1 gate to GPIO7;
- R14 100 kΩ gate-to-GND.

R15 100 kΩ pulls BOOT0 to GND. GPIO7 high turns Q3/Q2 on and sources BOOT0
only from target VTREF. GPIO7 low turns Q2 off and BOOT0 returns low.

The AO3401A body diode must point from BOOT0 toward VTREF, not from VTREF toward
BOOT0.

Recovery sequence:

1. Confirm valid VTREF.
2. Set BOOT0 high.
3. Assert and release NRST.
4. Wait for ROM bootloader.
5. Perform recovery.
6. Set BOOT0 low.
7. Reset target again.

## 10. VTREF sensing

- R16 100 kΩ, 1% or better, from VTREF to `VTREF_ADC`.
- R17 100 kΩ, 1% or better, from `VTREF_ADC` to GND.
- C9 100 nF X7R from `VTREF_ADC` to GND, placed at GPIO8.
- `VTREF_ADC` connects to GPIO8.

A 3.3 V target produces approximately 1.65 V at GPIO8. VTREF must have no
direct connection to probe 3V3 or USB 5 V. Firmware must use ADC1, an attenuation
range that includes 1.8 V, calibration and multi-sampling. The 50 kΩ Thevenin
resistance and 100 nF filter have a 5 ms nominal time constant; firmware must
wait at least 25 ms after target hot-plug before accepting VTREF as valid. The
threshold decision must include resistor tolerance, GPIO leakage and calibrated
ADC error, and must immediately disable the interface for a measured value above
3.6 V.

## 11. Target connectors and ESD

J2 and J3 implement the exact shared pinout in `design-data.json`. J2 is a DNL
PCB contact footprint for the `TC2050-IDC-NL` cable, not a fitted connector; its
contact pads receive no solder paste. J3 is the keyed vertical JST-XH
`B8B-XH-A (LF)(SN)` header using KiCad footprint
`Connector_JST:JST_XH_B8B-XH-A_1x08_P2.50mm_Vertical`; its mating housing is
`XHP-8` with a contact chosen for the final wire gauge.

Each connector has its own seven `PESD5V0U1UA,115` devices. This 5 V
reverse-standoff selection is intentional so the protection device remains within
its specified normal operating range for the full 3.0–3.6 V target range. Do not
substitute the previous 3.3 V standoff variant.

- one each for VTREF, SWDIO, SWCLK, NRST, TARGET_TX, TARGET_RX and BOOT0;
- exact SOD-323 package;
- signal connects to diode cathode, physical pin 1;
- diode anode, physical pin 2, connects to GND;
- place each bank beside its connector before signals enter the board.

Do not share one ESD bank between J2 and J3. The connectors are alternatives
and must not be attached to two powered targets simultaneously.

Place 47 Ω series resistors on SWDIO/SWCLK and 1 kΩ series resistors on UART
lines between TMUX and target-side protection.

Rev A.1 validates operation for target cables no longer than 300 mm, SWD clock
no higher than 4 MHz and UART no higher than 3 Mbit/s. The exposed target
connectors have a system validation target of IEC 61000-4-2 ±8 kV contact and
±15 kV air discharge; the protection-device rating alone is not a product-level
pass.

## 12. LEDs and expansion

- Power LED1: 3V3 → R19 1 kΩ → green `LTST-C190KGKT` → GND.
- Status LED2: GPIO21 → R20 1 kΩ → blue `LTST-C190TBKT` → GND.
- J4 `TSW-107-07-L-S`: 3V3, GND, GPIO10, GPIO11, GPIO12, GPIO13, GPIO14.
- J4 is a trusted, internal development header: no hot-plugging, no external
  cable exposure, and no more than 25 mA total load from 3V3. It is not a target
  power output.

Keep J4 clear of the BOOT button and board edge.

The Rev A.1 PCB is a 44 mm × 70 mm four-layer rectangle with no mounting holes
or enclosure constraint. J1 occupies the left portion of the bottom short edge,
with the footprint's documented PCB-edge datum aligned to the outline so that
the protected USB pair can use the reviewed left-side corridor. U1 is centered
on the top short edge with its antenna section beyond the board outline, and J2
and J3 face outward from the long edges. Controls remain accessible in the
lower area and the buck converter remains in the lower-right power island.
Adhesive non-conductive feet or an external test fixture provide prototype
support; layout must not reserve unreviewed mounting geometry.

## 13. Power-state behavior

Probe on, target off:

- TMUX `/EN` remains high.
- U5 is unpowered, Q5 is off, and GPIO9 cannot enable Q4/Q5 pull-down.
- NRST Q1 is off by its gate pulldown.
- BOOT0 remains low and Q2 has no VTREF source.

Probe off, target on:

- TMUX powered-off protection is required for target signals up to 3.6 V.
- NRST is not forward-powered through Q1.
- Q2 is held off by its VTREF-referenced gate pull-up.
- VTREF ADC injection is limited by R16 100 kΩ.

Both on:

- TMUX may enable only after both the hardware supervisor and firmware 3.0–3.6 V
  policy are satisfied.

Real leakage, threshold, transient, ESD-clamping and hot-plug behavior remain
physical prototype tests; they are not proven by this specification.

## 14. Controlled review invariants

The following are reviewed design decisions and must not be silently changed by an
EDA agent merely because a generic reference design differs:

- target operating range is 3.0–3.6 V and the probe never powers the target;
- `ESP32-S3-WROOM-1-N16R8` is the required module variant;
- AP63203 uses the reviewed 4.7 µH `XGL4030-472MEC` inductor with its deviation
  from the 3.9 µH typical application recorded in the power-component review;
- SWDIO remains bidirectional through TMUX1574;
- NRST is open-drain-style pull-low only and is never driven high from probe 3V3;
- BOOT0 high is sourced only from target VTREF;
- J2 and J3 are alternate interfaces and must not be connected to two powered
  targets at the same time;
- target-interface ESD uses `PESD5V0U1UA,115` with physical pin 1 as cathode and
  pin 2 as anode;
- layout-sensitive USB, antenna and buck-converter constraints in this document
  are mandatory and must not be relaxed by generic autorouting.

Any proposed change to one of these invariants requires an explicit reviewed
specification update rather than an implicit schematic or PCB optimization.
