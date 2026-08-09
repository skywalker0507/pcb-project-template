/**
 * Rev A electrical authority for the ESP32-S3 remote flashing probe.
 *
 * This file captures the reviewed topology and named nets. Exact production
 * land patterns are intentionally verified before transferring the candidate
 * into the manually reviewed KiCad layout authority.
 */

// tscircuit net identifiers cannot begin with a number; V3V3 is the emitted
// alias for the product rail named "3V3" in the requirements contract.
const net = (name: string) => `net.${name === "3V3" ? "V3V3" : name}`
const targetSignals = ["VTREF", "SWDIO", "SWCLK", "NRST", "TARGET_TX", "TARGET_RX", "BOOT0"] as const

export default function RemoteFlashingProbe() {
  return (
    <board width="36mm" height="80mm">
      {/* Controller and local USB device interface. */}
      <chip
        name="U1"
        manufacturerPartNumber="ESP32-S3-WROOM-1-N16R8"
        footprint="qfp64_p0.5mm"
        pcbX={0}
        pcbY={27}
        pinLabels={{
          // ESP32-S3-WROOM-1 module pad numbers, per Espressif Table 3-1.
          // The tscircuit QFP placeholder is only an electrical carrier;
          // the physical candidate replaces it with the verified module land
          // pattern before any layout review.
          pin1: "GND", pin2: "3V3", pin3: "EN", pin4: "GPIO4",
          pin7: "GPIO7", pin8: "GPIO15", pin9: "GPIO16", pin10: "GPIO17",
          pin11: "GPIO18", pin12: "GPIO8", pin13: "GPIO19", pin14: "GPIO20",
          pin17: "GPIO9", pin18: "GPIO10", pin19: "GPIO11", pin20: "GPIO12",
          pin21: "GPIO13", pin22: "GPIO14", pin23: "GPIO21", pin27: "GPIO0",
          // Unique electrical labels are required because the abstract
          // tscircuit footprint maps a connection by label.  All three pads
          // are tied to the single physical GND net below.
          pin40: "GND40", pin41: "EPAD",
        }}
        connections={{
          "3V3": net("3V3"), GND: net("GND"), GND40: net("GND"), EPAD: net("GND"), EN: net("EN"), GPIO19: net("USB_DM"), GPIO20: net("USB_DP"),
          GPIO4: net("NRST_ASSERT"), GPIO7: net("BOOT0_ASSERT"), GPIO8: net("VTREF_ADC"),
          GPIO9: net("TARGET_ENABLE_REQUEST"), GPIO15: net("PROBE_UART_TX"), GPIO16: net("PROBE_UART_RX"),
          GPIO17: net("SWDIO_PROBE"), GPIO18: net("SWCLK_PROBE"), GPIO21: net("STATUS_LED"),
          GPIO10: net("EXP_GPIO10"), GPIO11: net("EXP_GPIO11"), GPIO12: net("EXP_GPIO12"),
          GPIO13: net("EXP_GPIO13"), GPIO14: net("EXP_GPIO14"), GPIO0: net("GPIO0"),
        }}
      />
      <capacitor name="C1" capacitance="100nF" footprint="0402" pcbX={-4} pcbY={24} connections={{ pos: net("3V3"), neg: net("GND") }} />
      <capacitor name="C2" capacitance="10uF" footprint="0603" pcbX={-2} pcbY={24} connections={{ pos: net("3V3"), neg: net("GND") }} />
      <resistor name="R1" resistance="10kohm" footprint="0402" pcbX={3} pcbY={24} connections={{ pin1: net("3V3"), pin2: net("EN") }} />
      <capacitor name="C3" capacitance="1uF" footprint="0402" pcbX={5} pcbY={24} connections={{ pos: net("EN"), neg: net("GND") }} />
      <chip name="SW1" footprint="smdpinheader2" pcbX={7} pcbY={24} pinLabels={{ pin1: "EN", pin2: "GND" }} connections={{ EN: net("EN"), GND: net("GND") }} />
      <resistor name="R18" resistance="10kohm" footprint="0402" pcbX={9} pcbY={24} connections={{ pin1: net("3V3"), pin2: net("GPIO0") }} />
      <chip name="SW2" footprint="smdpinheader2" pcbX={11} pcbY={24} pinLabels={{ pin1: "GPIO0", pin2: "GND" }} connections={{ GPIO0: net("GPIO0"), GND: net("GND") }} />

      <connector
        name="J1"
        manufacturerPartNumber="USB4105-GF-A"
        footprint="smdpinheader16"
        pcbX={0}
        pcbY={-31}
        pinLabels={{ pin1: "VBUS", pin2: "GND", pin3: "CC1", pin4: "CC2", pin5: "D+", pin6: "D-", pin7: "SHIELD" }}
        connections={{ VBUS: net("USB_VBUS_RAW"), GND: net("GND"), CC1: net("CC1"), CC2: net("CC2"), "D+": net("USB_DP_CONN"), "D-": net("USB_DM_CONN"), SHIELD: net("USB_SHIELD") }}
      />
      <resistor name="R2" resistance="5.1kohm" footprint="0402" pcbX={-9} pcbY={-28} connections={{ pin1: net("CC1"), pin2: net("GND") }} />
      <resistor name="R3" resistance="5.1kohm" footprint="0402" pcbX={-7} pcbY={-28} connections={{ pin1: net("CC2"), pin2: net("GND") }} />
      <resistor name="R4" resistance="22ohm" footprint="0402" pcbX={2} pcbY={20} connections={{ pin1: net("USB_DP_CONN"), pin2: net("USB_DP") }} />
      <resistor name="R5" resistance="22ohm" footprint="0402" pcbX={4} pcbY={20} connections={{ pin1: net("USB_DM_CONN"), pin2: net("USB_DM") }} />
      <resistor name="R6" resistance="0ohm" footprint="0402" pcbX={8} pcbY={-28} connections={{ pin1: net("USB_SHIELD"), pin2: net("GND") }} />
      {/* The receptacle's redundant USB-C contacts merge over a deliberately
          short connector-side fan-out, then U2 clamps the two USB 2.0 lines.
          This avoids the long, asymmetric stubs created by per-contact TVS
          parts in the available connector keepout area. */}
      <chip name="U2" manufacturerPartNumber="USBLC6-2P6" footprint="sot666" pcbX={0} pcbY={-27}
        pinLabels={{ pin1: "IO1", pin2: "GND", pin3: "IO2", pin4: "IO2B", pin5: "VBUS", pin6: "IO1B" }}
        connections={{ IO1: net("USB_DM_CONN"), GND: net("GND"), IO2: net("USB_DP_CONN"), IO2B: net("USB_DP_CONN"), VBUS: net("USB_5V"), IO1B: net("USB_DM_CONN") }} />
      <chip name="F1" manufacturerPartNumber="1206L075/16WR" footprint="1206" pcbX={-5} pcbY={-25}
        pinLabels={{ pin1: "IN", pin2: "OUT" }} connections={{ IN: net("USB_VBUS_RAW"), OUT: net("USB_5V") }} />
      <diode name="D1" footprint="smb" pcbX={-3} pcbY={-23} connections={{ anode: net("GND"), cathode: net("USB_5V") }} />

      {/* AP63203 fixed 3.3-V buck. The 100 nF C5 value is from DS41326 Rev. 3. */}
      <chip name="U3" manufacturerPartNumber="AP63203WU-7" footprint="sot23" pcbX={0} pcbY={-18}
        pinLabels={{ pin1: "FB", pin2: "EN", pin3: "VIN", pin4: "GND", pin5: "SW", pin6: "BST" }}
        connections={{ FB: net("3V3"), EN: net("USB_5V"), VIN: net("USB_5V"), GND: net("GND"), SW: net("BUCK_SW"), BST: net("BUCK_BST") }} />
      <capacitor name="C4" capacitance="10uF" footprint="0603" pcbX={-4} pcbY={-18} connections={{ pos: net("USB_5V"), neg: net("GND") }} />
      <capacitor name="C5" capacitance="100nF" footprint="0402" pcbX={-2} pcbY={-18} connections={{ pos: net("BUCK_BST"), neg: net("BUCK_SW") }} />
      <chip name="L1" manufacturerPartNumber="XGL4030-472MEC" footprint="inductor" pcbX={3} pcbY={-18} pinLabels={{ pin1: "IN", pin2: "OUT" }} connections={{ IN: net("BUCK_SW"), OUT: net("3V3") }} />
      <capacitor name="C6" capacitance="22uF" footprint="0805" pcbX={5} pcbY={-16} connections={{ pos: net("3V3"), neg: net("GND") }} />
      <capacitor name="C7" capacitance="22uF" footprint="0805" pcbX={7} pcbY={-16} connections={{ pos: net("3V3"), neg: net("GND") }} />

      {/* Four switched target signals. TMUX SxB paths are grounded. */}
      <chip name="U4" manufacturerPartNumber="TMUX1574PWR" footprint="tssop16_p0.65mm" pcbX={0} pcbY={0}
        pinLabels={{ pin1: "S1A", pin2: "S1B", pin3: "D1", pin4: "S2A", pin5: "S2B", pin6: "D2", pin7: "GND", pin8: "SEL", pin9: "EN_N", pin10: "D3", pin11: "S3B", pin12: "S3A", pin13: "D4", pin14: "S4B", pin15: "S4A", pin16: "VDD" }}
        connections={{ S1A: net("SWDIO_PROBE"), S1B: net("GND"), D1: net("SWDIO_MUX"), S2A: net("SWCLK_PROBE"), S2B: net("GND"), D2: net("SWCLK_MUX"), GND: net("GND"), SEL: net("GND"), "EN_N": net("TARGET_EN_N"), D3: net("TARGET_RX_MUX"), S3B: net("GND"), S3A: net("PROBE_UART_TX"), D4: net("TARGET_TX_MUX"), S4B: net("GND"), S4A: net("PROBE_UART_RX"), VDD: net("3V3") }} />
      <capacitor name="C8" capacitance="100nF" footprint="0402" pcbX={-4} pcbY={0} connections={{ pos: net("3V3"), neg: net("GND") }} />
      <resistor name="R7" resistance="10kohm" footprint="0402" pcbX={4} pcbY={0} connections={{ pin1: net("3V3"), pin2: net("TARGET_EN_N") }} />
      <resistor name="R8" resistance="47ohm" footprint="0402" pcbX={5} pcbY={2} connections={{ pin1: net("SWDIO_MUX"), pin2: net("SWDIO") }} />
      <resistor name="R9" resistance="47ohm" footprint="0402" pcbX={7} pcbY={2} connections={{ pin1: net("SWCLK_MUX"), pin2: net("SWCLK") }} />
      <resistor name="R10" resistance="1kohm" footprint="0402" pcbX={5} pcbY={4} connections={{ pin1: net("TARGET_TX_MUX"), pin2: net("TARGET_TX") }} />
      <resistor name="R11" resistance="1kohm" footprint="0402" pcbX={7} pcbY={4} connections={{ pin1: net("TARGET_RX_MUX"), pin2: net("TARGET_RX") }} />

      {/* VTREF supervisor, ADC divider, reset and target-referenced BOOT0 driver. */}
      <chip name="U5" manufacturerPartNumber="TLV803EA26RDBZR" footprint="sot23" pcbX={-7} pcbY={7} pinLabels={{ pin1: "GND", pin2: "RESET_N", pin3: "VDD" }} connections={{ GND: net("GND"), "RESET_N": net("SUP_RESET_N"), VDD: net("VTREF") }} />
      <chip name="Q4" manufacturerPartNumber="2N7002LT1G" footprint="sot23" pcbX={-4} pcbY={7} pinLabels={{ pin1: "S", pin2: "G", pin3: "D" }} connections={{ G: net("TARGET_ENABLE_REQUEST"), S: net("Q4_SOURCE"), D: net("TARGET_EN_N") }} />
      <chip name="Q5" manufacturerPartNumber="AO3400A" footprint="sot23" pcbX={-2} pcbY={7} pinLabels={{ pin1: "G", pin2: "S", pin3: "D" }} connections={{ G: net("SUP_RESET_N"), S: net("GND"), D: net("Q4_SOURCE") }} />
      <resistor name="R21" resistance="100kohm" footprint="0402" pcbX={-4} pcbY={5} connections={{ pin1: net("TARGET_ENABLE_REQUEST"), pin2: net("GND") }} />
      <resistor name="R22" resistance="100kohm" footprint="0402" pcbX={-2} pcbY={5} connections={{ pin1: net("SUP_RESET_N"), pin2: net("VTREF") }} />
      <resistor name="R16" resistance="1Mohm" footprint="0402" pcbX={-5} pcbY={10} connections={{ pin1: net("VTREF"), pin2: net("VTREF_ADC") }} />
      <resistor name="R17" resistance="1Mohm" footprint="0402" pcbX={-3} pcbY={10} connections={{ pin1: net("VTREF_ADC"), pin2: net("GND") }} />
      <capacitor name="C9" capacitance="4.7nF" footprint="0402" pcbX={-1} pcbY={10} connections={{ pos: net("VTREF_ADC"), neg: net("GND") }} />
      <chip name="Q1" manufacturerPartNumber="2N7002LT1G" footprint="sot23" pcbX={7} pcbY={7} pinLabels={{ pin1: "S", pin2: "G", pin3: "D" }} connections={{ G: net("NRST_ASSERT"), S: net("GND"), D: net("NRST") }} />
      <resistor name="R12" resistance="100kohm" footprint="0402" pcbX={9} pcbY={7} connections={{ pin1: net("NRST_ASSERT"), pin2: net("GND") }} />
      <chip name="Q2" manufacturerPartNumber="AO3401A" footprint="sot23" pcbX={7} pcbY={12} pinLabels={{ pin1: "G", pin2: "S", pin3: "D" }} connections={{ G: net("BOOT0_GATE"), S: net("VTREF"), D: net("BOOT0") }} />
      <chip name="Q3" manufacturerPartNumber="2N7002LT1G" footprint="sot23" pcbX={10} pcbY={12} pinLabels={{ pin1: "S", pin2: "G", pin3: "D" }} connections={{ G: net("BOOT0_ASSERT"), S: net("GND"), D: net("BOOT0_GATE") }} />
      <resistor name="R13" resistance="100kohm" footprint="0402" pcbX={7} pcbY={14} connections={{ pin1: net("VTREF"), pin2: net("BOOT0_GATE") }} />
      <resistor name="R14" resistance="100kohm" footprint="0402" pcbX={10} pcbY={14} connections={{ pin1: net("BOOT0_ASSERT"), pin2: net("GND") }} />
      <resistor name="R15" resistance="100kohm" footprint="0402" pcbX={12} pcbY={14} connections={{ pin1: net("BOOT0"), pin2: net("GND") }} />

      {/* Target connectors: electrically common alternatives, each with its own ESD bank in physical design. */}
      <connector name="J2" manufacturerPartNumber="TC2050-IDC-NL" footprint="pinrow10_rows2_cols5_p1.27mm_py1.27mm_missing(3,4,8,9)" pcbX={-12} pcbY={0}
        pinLabels={{ pin1: "VTREF", pin2: "SWDIO", pin3: "GND1", pin4: "SWCLK", pin5: "GND2", pin6: "TARGET_TX", pin7: "BOOT0", pin8: "TARGET_RX", pin9: "NRST", pin10: "GND3" }}
        connections={{ VTREF: net("VTREF"), SWDIO: net("SWDIO"), GND1: net("GND"), SWCLK: net("SWCLK"), GND2: net("GND"), TARGET_TX: net("TARGET_TX"), BOOT0: net("BOOT0"), TARGET_RX: net("TARGET_RX"), NRST: net("NRST"), GND3: net("GND") }} />
      <connector name="J3" footprint="pinrow8_p2.54mm" pcbX={12} pcbY={0}
        pinLabels={{ pin1: "GND", pin2: "VTREF", pin3: "SWDIO", pin4: "SWCLK", pin5: "NRST", pin6: "BOOT0", pin7: "TARGET_TX", pin8: "TARGET_RX" }}
        connections={{ GND: net("GND"), VTREF: net("VTREF"), SWDIO: net("SWDIO"), SWCLK: net("SWCLK"), NRST: net("NRST"), BOOT0: net("BOOT0"), TARGET_TX: net("TARGET_TX"), TARGET_RX: net("TARGET_RX") }} />
      {targetSignals.map((signal, index) => (
        <chip key={`J2_ESD_${signal}`} name={`D${2 + index}`} manufacturerPartNumber="PESD3V3U1UL,315" footprint="sod882" pcbX={-10} pcbY={-8 + index * 2}
          pinLabels={{ pin1: "A", pin2: "K" }} connections={{ A: net("GND"), K: net(signal) }} />
      ))}
      {targetSignals.map((signal, index) => (
        <chip key={`J3_ESD_${signal}`} name={`D${9 + index}`} manufacturerPartNumber="PESD3V3U1UL,315" footprint="sod882" pcbX={10} pcbY={-8 + index * 2}
          pinLabels={{ pin1: "A", pin2: "K" }} connections={{ A: net("GND"), K: net(signal) }} />
      ))}
      <connector name="J4" manufacturerPartNumber="TSW-107-07-L-S" footprint="pinrow7_p2.54mm" pcbX={0} pcbY={-10}
        pinLabels={{ pin1: "3V3", pin2: "GND", pin3: "GPIO10", pin4: "GPIO11", pin5: "GPIO12", pin6: "GPIO13", pin7: "GPIO14" }}
        connections={{ "3V3": net("3V3"), GND: net("GND"), GPIO10: net("EXP_GPIO10"), GPIO11: net("EXP_GPIO11"), GPIO12: net("EXP_GPIO12"), GPIO13: net("EXP_GPIO13"), GPIO14: net("EXP_GPIO14") }} />
      <resistor name="R19" resistance="1kohm" footprint="0402" pcbX={-6} pcbY={-10} connections={{ pin1: net("3V3"), pin2: net("POWER_LED_A") }} />
      <led name="LED1" footprint="0603" pcbX={-4} pcbY={-10} connections={{ anode: net("POWER_LED_A"), cathode: net("GND") }} />
      <resistor name="R20" resistance="1kohm" footprint="0402" pcbX={4} pcbY={-10} connections={{ pin1: net("STATUS_LED"), pin2: net("STATUS_LED_A") }} />
      <led name="LED2" footprint="0603" pcbX={6} pcbY={-10} connections={{ anode: net("STATUS_LED_A"), cathode: net("GND") }} />
    </board>
  )
}
