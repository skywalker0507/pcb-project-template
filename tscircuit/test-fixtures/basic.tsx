/** Non-product fixture that proves the local tscircuit → KiCad adapter. */
export default function BasicFixture() {
  return (
    <board width="20mm" height="20mm">
      <resistor name="R1" resistance="1kohm" footprint="0603" pcbX={0} pcbY={0} />
    </board>
  )
}
