# Tool-Neutral Hardware Specification

## 1. Scope

Implement the exact Rev A ESP32-S3 remote flashing probe described here. The
first reviewed atopile migration must preserve the existing topology, MPNs, connector
pin order and supported 3.0–3.6 V target range.

Rev A is initially for STM32 targets. The ESP32-S3 connects to Wi-Fi and runs
as a standalone remote programmer: no PC is required while programming a
target. Firmware is outside this PCB specification, but the pin assignments
and J2/J3 target-interface contract are hardware constraints. Future target
families require a separately reviewed interface extension. Product decisions
confirmed for this revision are recorded in `config/product-decisions.json`.

The target board powers itself. The probe has no target-power output. VTREF is
used only by the ADC divider, BOOT0 source and target-powered safety supervisor.

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

- C1 100 nF and C2 10 µF from 3V3 to GND near the module.
- R1 10 kΩ from 3V3 to EN.
- C3 1 µF from EN to GND.
- SW1 RESET button from EN to GND.
- R18 10 kΩ from 3V3 to GPIO0.
- SW2 BOOT/provision button from GPIO0 to GND.
- GPIO assignment must exactly match `design-data.json`.
- GPIO3, GPIO45 and GPIO46 are not available for product functions.
- Module GND1, GND2 and EPAD all connect to GND.

Place the module at the top board edge with the PCB antenna facing outward.
Apply the complete antenna keepout on every copper layer and to components,
fasteners and enclosure metal.

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
  U2; its verified pin map is 1/6 = D-, 3/4 = D+, 2 = GND and 5 = VBUS.
- R5 22 Ω is D- and terminates at GPIO19.
- R4 22 Ω is D+ and terminates at GPIO20.
- R4/R5 are placed near the ESP32 pads.
- J1 shield connects through R6 0 Ω to GND. Keep R6 as an explicit,
  replaceable shield strategy.
- F1 `1206L075/16WR` is between raw USB VBUS and protected `USB_5V`.
- D1 `SMBJ5.0A`: cathode to `USB_5V`, anode to GND.

USB routing requirements:

- 90 Ω differential impedance using JLC04161H-3313.
- F.Cu over continuous In1 GND.
- Nominal 0.20 mm width and 0.15–0.166 mm pair gap before fabrication
  compensation.
- No layer changes after U2. The J1-to-U2 reversible-contact fan-out may use a
  short, reviewed F.Cu/B.Cu crossover only when needed to join A6/B6 and
  A7/B7; each signal-layer transition needs nearby GND return vias. This
  bounded connector-side exception does not apply to the U2-to-U1 USB pair.
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

- C4: 10 µF from USB_5V to GND, immediately beside VIN/GND.
- C5: 100 nF from BST to SW, immediately beside pins 6/5.
- L1: `XGL4030-472MEC`, 4.7 µH shielded power inductor, adjacent to SW. Its documented 20%-drop saturation current and 40°C-rise current must retain margin over the AP63203 peak-current design case.
- C6/C7: two 22 µF capacitors from 3V3 to GND beside L1 output.

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
VTREF remains between 3.0 and 3.6 V.

## 7. Hardware VTREF interlock

Firmware GPIO9 must not be able to enable the TMUX unless target VTREF exceeds
the supervisor threshold.

- Q4: `2N7002LT1G`.
  - gate to GPIO9;
  - R21 100 kΩ gate-to-GND;
  - drain to `TARGET_EN_N`;
  - source to Q5 drain.
- Q5: `AO3400A`.
  - drain to Q4 source;
  - source to GND;
  - gate to U5 `/RESET`.
- U5: `TLV803EA26RDBZR`.
  - VDD powered only by target VTREF;
  - GND to GND;
  - open-drain `/RESET` to Q5 gate;
  - R22 100 kΩ from Q5 gate to VTREF.

U5 is the 2.64 V threshold, approximately 200 ms release-delay variant. With
target absent or undervoltage, Q5 is off and Q4 cannot pull TMUX `/EN` low.

Firmware must still require ADC-confirmed VTREF 3.0–3.6 V with hysteresis and
debounce, and immediately release GPIO9 on invalid VTREF.

## 8. NRST

Use Q1 `2N7002LT1G`:

- drain to target NRST;
- source to GND;
- gate to GPIO4;
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

- drain to Q2 gate;
- source to GND;
- gate to GPIO7;
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

- R16 1 MΩ from VTREF to `VTREF_ADC`.
- R17 1 MΩ from `VTREF_ADC` to GND.
- C9 4.7 nF from `VTREF_ADC` to GND.
- `VTREF_ADC` connects to GPIO8.

A 3.3 V target produces approximately 1.65 V at GPIO8. VTREF must have no
direct connection to probe 3V3 or USB 5 V.

## 11. Target connectors and ESD

J2 and J3 implement the exact shared pinout in `design-data.json`.

Each connector has its own seven `PESD3V3U1UL,315` devices:

- one each for VTREF, SWDIO, SWCLK, NRST, TARGET_TX, TARGET_RX and BOOT0;
- signal connects to diode cathode/pin 2;
- diode anode/pin 1 connects to GND;
- place each bank beside its connector before signals enter the board.

Do not share one ESD bank between J2 and J3. The connectors are alternatives
and must not be attached to two powered targets simultaneously.

Place 47 Ω series resistors on SWDIO/SWCLK and 1 kΩ series resistors on UART
lines between TMUX and target-side protection.

## 12. LEDs and expansion

- Power LED1: 3V3 → R19 1 kΩ → green `LTST-C190KGKT` → GND.
- Status LED2: GPIO21 → R20 1 kΩ → blue `LTST-C190TBKT` → GND.
- J4 `TSW-107-07-L-S`: 3V3, GND, GPIO10, GPIO11, GPIO12, GPIO13, GPIO14.

Keep J4 clear of the BOOT button and lower board-edge mechanical support.

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
- VTREF ADC injection is limited by R16 1 MΩ.

Both on:

- TMUX may enable only after both the hardware supervisor and firmware 3.0–3.6 V
  policy are satisfied.

Real leakage, threshold, transient and hot-plug behavior remain physical
prototype tests; they are not proven by this specification.
