/**
 * Electrical source entry point for this template.
 *
 * Deliberately unconfigured: requirements.json is still draft and must define
 * the product before an AI generates a real board.  Replace this default export
 * with a reviewed tscircuit <board /> design.
 */
export default function UnconfiguredBoard(): never {
  throw new Error(
    "No tscircuit design is configured. Record reviewed requirements before creating a board.",
  )
}
