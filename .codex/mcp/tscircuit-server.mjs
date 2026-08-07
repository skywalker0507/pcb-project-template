/** Minimal local MCP surface for the tscircuit-first electrical backend. */

import { spawn } from "node:child_process"
import path from "node:path"

const workspace = path.resolve(process.env.TSCIRCUIT_WORKSPACE ?? process.cwd())
const python = process.env.PCB_PYTHON ?? "py"
const bun = process.env.BUN_BINARY ?? "bun"
const MAX_OUTPUT = 100_000
const TIMEOUT_MS = 170_000

const tools = [
  {
    name: "tscircuit_status",
    description: "Validate the selected native tscircuit backend wiring without building a board.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "tscircuit_validate",
    description: "Run the tscircuit TypeScript validation from the local locked dependency tree.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "tscircuit_build_candidate",
    description: "Generate an isolated Circuit JSON and KiCad candidate. It never overwrites the reviewed KiCad board.",
    inputSchema: {
      type: "object",
      properties: { build: { type: "string", default: "default" } },
      additionalProperties: false,
    },
  },
]

function boundedRun(command, args, cwd) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, windowsHide: true })
    let stdout = ""
    let stderr = ""
    let timedOut = false
    const append = (target, value) => target.length < MAX_OUTPUT ? target + value.toString() : target
    const timer = setTimeout(() => {
      timedOut = true
      child.kill()
    }, TIMEOUT_MS)
    child.stdout.on("data", (chunk) => { stdout = append(stdout, chunk) })
    child.stderr.on("data", (chunk) => { stderr = append(stderr, chunk) })
    child.on("error", (error) => {
      clearTimeout(timer)
      resolve({ ok: false, text: `Failed to start ${command}: ${error.message}` })
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      const text = [stdout.trim(), stderr.trim(), timedOut ? `Timed out after ${TIMEOUT_MS / 1000}s.` : ""]
        .filter(Boolean)
        .join("\n")
      resolve({ ok: code === 0 && !timedOut, text: text || `Exit code: ${code}` })
    })
  })
}

async function callTool(name, input = {}) {
  if (name === "tscircuit_status") {
    return boundedRun(python, ["scripts/build_electrical.py", "--backend", "tscircuit", "--check"], workspace)
  }
  if (name === "tscircuit_validate") {
    return boundedRun(bun, ["run", "typecheck"], path.join(workspace, "tscircuit"))
  }
  if (name === "tscircuit_build_candidate") {
    const build = input.build ?? "default"
    return boundedRun(python, ["scripts/build_electrical.py", "--backend", "tscircuit", "--build", String(build)], workspace)
  }
  return { ok: false, text: `Unknown tscircuit tool: ${name}` }
}

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`)
}

async function handle(request) {
  if (request.method?.startsWith("notifications/")) return
  try {
    if (request.method === "initialize") {
      send({ jsonrpc: "2.0", id: request.id, result: { protocolVersion: request.params?.protocolVersion ?? "2025-06-18", capabilities: { tools: { listChanged: false } }, serverInfo: { name: "tscircuit-local", version: "0.1.0" } } })
      return
    }
    if (request.method === "ping") {
      send({ jsonrpc: "2.0", id: request.id, result: {} })
      return
    }
    if (request.method === "tools/list") {
      send({ jsonrpc: "2.0", id: request.id, result: { tools } })
      return
    }
    if (request.method === "tools/call") {
      const result = await callTool(request.params?.name, request.params?.arguments)
      send({ jsonrpc: "2.0", id: request.id, result: { content: [{ type: "text", text: result.text }], isError: !result.ok } })
      return
    }
    send({ jsonrpc: "2.0", id: request.id, error: { code: -32601, message: `Unknown method: ${request.method}` } })
  } catch (error) {
    send({ jsonrpc: "2.0", id: request.id, error: { code: -32603, message: String(error) } })
  }
}

let buffered = ""
process.stdin.setEncoding("utf8")
process.stdin.on("data", (chunk) => {
  buffered += chunk
  const lines = buffered.split(/\r?\n/)
  buffered = lines.pop() ?? ""
  for (const line of lines) {
    if (!line.trim()) continue
    try { void handle(JSON.parse(line)) } catch (error) {
      send({ jsonrpc: "2.0", id: null, error: { code: -32700, message: `Invalid JSON: ${error.message}` } })
    }
  }
})
