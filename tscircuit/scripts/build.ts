/** Build Circuit JSON and a KiCad candidate without touching the reviewed board. */

import React from "react"
import { pathToFileURL } from "node:url"
import { resolve } from "node:path"
import { Circuit } from "tscircuit"
import {
  CircuitJsonToKicadPcbConverter,
  CircuitJsonToKicadProConverter,
  CircuitJsonToKicadSchConverter,
} from "circuit-json-to-kicad"

declare const Bun: {
  argv: string[]
  write(path: string, data: string): Promise<number>
}

type Options = { output: string; entry: string }

function parseOptions(argv: string[]): Options {
  const values = new Map<string, string>()
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index]
    const value = argv[index + 1]
    if (!flag?.startsWith("--") || !value) throw new Error("Expected --output <dir> --entry <file>")
    values.set(flag.slice(2), value)
  }
  const output = values.get("output")
  const entry = values.get("entry")
  if (!output || !entry || values.size !== 2) throw new Error("Expected exactly --output <dir> --entry <file>")
  return { output: resolve(output), entry: resolve(entry) }
}

const { output, entry } = parseOptions(Bun.argv.slice(2))
const module = await import(pathToFileURL(entry).href)
if (typeof module.default !== "function") throw new Error(`Default export must be a tscircuit component: ${entry}`)

const circuit = new Circuit()
circuit.add(React.createElement(module.default))
await circuit.renderUntilSettled()
const circuitJson = circuit.getCircuitJson()

const sch = new CircuitJsonToKicadSchConverter(circuitJson)
sch.runUntilFinished()
const pcb = new CircuitJsonToKicadPcbConverter(circuitJson)
pcb.runUntilFinished()
const project = new CircuitJsonToKicadProConverter(circuitJson, {
  projectName: "candidate",
  schematicFilename: "candidate.kicad_sch",
  pcbFilename: "candidate.kicad_pcb",
})
project.runUntilFinished()

await Bun.write(resolve(output, "circuit.json"), JSON.stringify(circuitJson, null, 2) + "\n")
await Bun.write(resolve(output, "candidate.kicad_sch"), sch.getOutputString())
await Bun.write(resolve(output, "candidate.kicad_pcb"), pcb.getOutputString())
await Bun.write(resolve(output, "candidate.kicad_pro"), project.getOutputString())
