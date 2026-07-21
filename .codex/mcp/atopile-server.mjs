import { spawn } from "node:child_process";
import path from "node:path";

const SERVER = { name: "atopile-local", version: "0.2.0" };
const PROTOCOL_VERSION = "2025-06-18";
const atoBinary =
  process.env.ATO_BINARY
  || (() => { throw new Error("ATO_BINARY must be set by start-atopile-mcp-wsl.sh"); })();
const atopilePython =
  process.env.ATOPILE_PYTHON
  || (() => { throw new Error("ATOPILE_PYTHON must be set by start-atopile-mcp-wsl.sh"); })();
const workspace = path.resolve(process.env.ATOPILE_WORKSPACE ?? process.cwd());
const agentBridge = path.join(
  workspace,
  ".codex",
  "mcp",
  "atopile-agent-bridge.py",
);
const MAX_OUTPUT = 100_000;

const tools = [
  {
    name: "atopile_version",
    description: "Return the installed atopile CLI version.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "atopile_validate",
    description: "Validate one .ato source file for syntax and consistency.",
    inputSchema: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description: "Source file path, relative to the workspace.",
        },
      },
      required: ["path"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_build",
    description:
      "Build an atopile project or entry point and update its KiCad outputs.",
    inputSchema: {
      type: "object",
      properties: {
        entry: {
          type: "string",
          description:
            "Optional project path, build target address, or path.ato:Module.",
        },
        build: { type: "string", description: "Named build from ato.yaml." },
        target: {
          type: "array",
          items: { type: "string" },
          description: "Output target(s), for example bom or mfg-data.",
        },
        excludeTarget: {
          type: "array",
          items: { type: "string" },
          description: "Output target(s) to exclude.",
        },
        keepPickedParts: { type: "boolean", default: true },
        keepNetNames: { type: "boolean", default: true },
        keepDesignators: { type: "boolean", default: true },
        jobs: { type: "integer", minimum: 1, maximum: 32 },
        workingDirectory: {
          type: "string",
          description: "Workspace-relative directory containing ato.yaml.",
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: "atopile_inspect",
    description: "Inspect connectivity for a module or component.",
    inputSchema: {
      type: "object",
      properties: {
        entry: { type: "string" },
        build: { type: "string" },
        target: { type: "string" },
        inspect: { type: "string" },
        context: { type: "string" },
        workingDirectory: { type: "string" },
      },
      required: ["inspect"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_dependencies",
    description: "List or synchronize the current project's dependencies.",
    inputSchema: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["list", "sync"] },
        workingDirectory: { type: "string" },
      },
      required: ["action"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_stdlib_list",
    description:
      "Search atopile's installed standard library for modules, interfaces, and components.",
    inputSchema: {
      type: "object",
      properties: {
        type_filter: {
          type: ["string", "null"],
          description: "Optional item type such as module or interface.",
        },
        search: {
          type: ["string", "null"],
          description: "Optional case-insensitive search text.",
        },
        limit: { type: "integer", minimum: 1, maximum: 120, default: 40 },
      },
      additionalProperties: false,
    },
  },
  {
    name: "atopile_stdlib_get_item",
    description:
      "Inspect one atopile standard-library item using an ID returned by atopile_stdlib_list.",
    inputSchema: {
      type: "object",
      properties: { item_id: { type: "string" } },
      required: ["item_id"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_packages_search",
    description:
      "Search the official atopile package registry for reusable modules and libraries.",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_packages_install",
    description:
      "Install an atopile registry package into the current project.",
    inputSchema: {
      type: "object",
      properties: {
        identifier: { type: "string" },
        version: { type: ["string", "null"] },
      },
      required: ["identifier"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_parts_search",
    description: "Search the LCSC/JLC parts catalog by keyword.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 100, default: 20 },
      },
      required: ["query"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_parts_install",
    description:
      "Install an exact LCSC part into the project, optionally as a reusable local package.",
    inputSchema: {
      type: "object",
      properties: {
        lcsc_id: { type: "string" },
        create_package: { type: "boolean", default: false },
        project_path: { type: ["string", "null"] },
      },
      required: ["lcsc_id"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_parts_datasheet",
    description:
      "Resolve or download the datasheet PDF for an LCSC part ID.",
    inputSchema: {
      type: "object",
      properties: { lcsc_id: { type: "string" } },
      required: ["lcsc_id"],
      additionalProperties: false,
    },
  },
  {
    name: "atopile_workspace_list_targets",
    description:
      "List build targets in the project and its local sub-packages.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
];

const agentToolNames = new Map([
  ["atopile_stdlib_list", "stdlib_list"],
  ["atopile_stdlib_get_item", "stdlib_get_item"],
  ["atopile_packages_search", "packages_search"],
  ["atopile_packages_install", "packages_install"],
  ["atopile_parts_search", "parts_search"],
  ["atopile_parts_install", "parts_install"],
  ["atopile_parts_datasheet", "parts_datasheet"],
  ["atopile_workspace_list_targets", "workspace_list_targets"],
]);

function workspacePath(input = ".") {
  const resolved = path.resolve(workspace, input);
  const relative = path.relative(workspace, resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Path must stay inside workspace: ${input}`);
  }
  return resolved;
}

function appendMany(args, flag, values = []) {
  for (const value of values) args.push(flag, String(value));
}

function commandFor(name, input) {
  const cwd = workspacePath(input.workingDirectory);

  switch (name) {
    case "atopile_version":
      return { args: ["--version"], cwd: workspace };
    case "atopile_validate":
      return { args: ["validate", workspacePath(input.path)], cwd };
    case "atopile_build": {
      const args = ["--non-interactive", "build"];
      if (input.build) args.push("--build", input.build);
      appendMany(args, "--target", input.target);
      appendMany(args, "--exclude-target", input.excludeTarget);
      if (input.keepPickedParts !== false) args.push("--keep-picked-parts");
      if (input.keepNetNames !== false) args.push("--keep-net-names");
      if (input.keepDesignators !== false) args.push("--keep-designators");
      if (input.jobs) args.push("--jobs", String(input.jobs));
      if (input.entry) args.push(input.entry);
      return { args, cwd };
    }
    case "atopile_inspect": {
      const args = ["--non-interactive", "inspect"];
      if (input.build) args.push("--build", input.build);
      if (input.target) args.push("--target", input.target);
      args.push("--inspect", input.inspect);
      if (input.context) args.push("--context", input.context);
      if (input.entry) args.push(input.entry);
      return { args, cwd };
    }
    case "atopile_dependencies":
      return {
        args: ["--non-interactive", "dependencies", input.action],
        cwd,
      };
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

function runAto(name, input = {}) {
  const { args, cwd } = commandFor(name, input);
  return new Promise((resolve) => {
    const child = spawn(atoBinary, args, {
      cwd,
      env: { ...process.env, ATO_NON_INTERACTIVE: "1" },
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, 170_000);

    child.stdout.on("data", (chunk) => {
      if (stdout.length < MAX_OUTPUT) stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      if (stderr.length < MAX_OUTPUT) stderr += chunk.toString();
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({
        ok: false,
        text: `Failed to start ${atoBinary}: ${error.message}`,
      });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      const sections = [
        `Command: ato ${args.join(" ")}`,
        `Working directory: ${cwd}`,
        `Exit code: ${code}`,
        timedOut ? "Timed out after 170 seconds." : "",
        stdout.trim() ? `stdout:\n${stdout.trim()}` : "",
        stderr.trim() ? `stderr:\n${stderr.trim()}` : "",
      ].filter(Boolean);
      resolve({ ok: code === 0 && !timedOut, text: sections.join("\n\n") });
    });
  });
}

function runAgentTool(name, input = {}) {
  return new Promise((resolve) => {
    const internalName = agentToolNames.get(name);
    if (!internalName) {
      resolve({ ok: false, text: `Unknown atopile agent tool: ${name}` });
      return;
    }

    const child = spawn(
      atopilePython,
      [agentBridge, workspace, internalName, JSON.stringify(input)],
      {
        cwd: workspace,
        env: { ...process.env, ATO_NON_INTERACTIVE: "1" },
        windowsHide: true,
      },
    );
    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, 170_000);

    child.stdout.on("data", (chunk) => {
      if (stdout.length < MAX_OUTPUT) stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      if (stderr.length < MAX_OUTPUT) stderr += chunk.toString();
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({
        ok: false,
        text: `Failed to start atopile Python bridge: ${error.message}`,
      });
    });
    child.on("close", () => {
      clearTimeout(timer);
      const prefix = "__ATOPILE_MCP_RESULT__";
      const resultLine = stdout
        .split(/\r?\n/)
        .findLast((line) => line.startsWith(prefix));

      if (timedOut) {
        resolve({ ok: false, text: "Atopile agent tool timed out after 170 seconds." });
        return;
      }
      if (!resultLine) {
        resolve({
          ok: false,
          text: [
            "Atopile agent bridge returned no structured result.",
            stdout.trim() ? `stdout:\n${stdout.trim()}` : "",
            stderr.trim() ? `stderr:\n${stderr.trim()}` : "",
          ]
            .filter(Boolean)
            .join("\n\n"),
        });
        return;
      }

      try {
        const payload = JSON.parse(resultLine.slice(prefix.length));
        const rendered = JSON.stringify(
          payload.ok ? payload.result : payload,
          null,
          2,
        );
        resolve({
          ok: payload.ok,
          text: stderr.trim()
            ? `${rendered}\n\nBridge diagnostics:\n${stderr.trim()}`
            : rendered,
        });
      } catch (error) {
        resolve({
          ok: false,
          text: `Could not parse atopile agent result: ${error.message}`,
        });
      }
    });
  });
}

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

async function handle(request) {
  const { id, method, params = {} } = request;
  if (method.startsWith("notifications/")) return;

  try {
    let result;
    switch (method) {
      case "initialize":
        result = {
          protocolVersion: params.protocolVersion ?? PROTOCOL_VERSION,
          capabilities: { tools: { listChanged: false } },
          serverInfo: SERVER,
        };
        break;
      case "ping":
        result = {};
        break;
      case "tools/list":
        result = { tools };
        break;
      case "tools/call": {
        const execution = agentToolNames.has(params.name)
          ? await runAgentTool(params.name, params.arguments ?? {})
          : await runAto(params.name, params.arguments ?? {});
        result = {
          content: [{ type: "text", text: execution.text }],
          isError: !execution.ok,
        };
        break;
      }
      default:
        send({
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `Method not found: ${method}` },
        });
        return;
    }
    send({ jsonrpc: "2.0", id, result });
  } catch (error) {
    send({
      jsonrpc: "2.0",
      id,
      error: { code: -32602, message: error.message },
    });
  }
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  while (buffer.includes("\n")) {
    const newline = buffer.indexOf("\n");
    const line = buffer.slice(0, newline).trim();
    buffer = buffer.slice(newline + 1);
    if (!line) continue;
    try {
      void handle(JSON.parse(line));
    } catch (error) {
      send({
        jsonrpc: "2.0",
        id: null,
        error: { code: -32700, message: error.message },
      });
    }
  }
});
